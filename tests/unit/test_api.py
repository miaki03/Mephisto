"""HTTP API 测试：真起一个服务器（端口 0），真走 HTTP，用假 Runner。

为什么不打真 dsh：这些用例要秒级跑完、不联网、不花钱。
Runner 的边界已经在 `test_runner.py` 里用真进程验过了，这里只验协议层。
"""

from __future__ import annotations

import json
import shutil
import socket
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mephisto.adapters.http.server import build_api, build_server
from mephisto.app.chat import ChatService
from mephisto.blobs import BlobStore, UploadArea, digest_of_bytes
from mephisto.config import Config, StorageConfig
from mephisto.domain import Notice
from mephisto.ids import new_ulid
from mephisto.ports import RunResult, Task
from mephisto.store import open_db

TMP_ROOT = Path(__file__).resolve().parent / "_tmp"
NOW = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)


class FakeRunner:
    def __init__(self, text: str = "收到，这是假回复") -> None:
        self.text = text
        self.calls: list[Task] = []

    def run(self, task: Task, *, on_event=None) -> RunResult:
        self.calls.append(task)
        return RunResult(
            ok=True,
            text=self.text,
            exit_code=0,
            usage={"totalTokens": 12, "cacheReadTokens": 8},
            duration_ms=1,
            dsh_session_id="session-fake",
        )


class ApiTestBase(unittest.TestCase):
    runner_text = "收到，这是假回复"

    def setUp(self) -> None:
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.dir = TMP_ROOT / f"api_{uuid.uuid4().hex[:12]}"
        self.dir.mkdir()
        self.config = Config(
            root=self.dir,
            storage=StorageConfig(data_dir=self.dir.as_posix()),
        )
        self.db = open_db(self.config.data_dir)
        self.db.migrate()
        self.runner = FakeRunner(self.runner_text)
        self.service = ChatService(
            self.db,
            self.runner,
            blob_root=self.dir / "blobs",
            upload_root=self.dir / "uploads",
        )
        self.api = build_api(self.config, service=self.service)
        self.server = build_server(self.config, service=self.service, host="127.0.0.1", port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        info, token = self.service.devices.add("测试手机", at=NOW)
        self.device_id, self.token = info.id, token

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    # ---------- HTTP 辅助 ----------
    def request(self, method: str, path: str, *, body=None, token: str | None = "default", raw: bytes | None = None):
        url = self.base + path
        headers: dict[str, str] = {}
        data: bytes | None = None
        if token is not None:
            headers["Authorization"] = "Bearer " + (self.token if token == "default" else token)
        if raw is not None:
            data = raw
            headers["Content-Type"] = "application/octet-stream"
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                if "application/json" in ctype:
                    return resp.status, json.loads(payload)
                return resp.status, payload
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            try:
                return exc.code, json.loads(payload)
            except json.JSONDecodeError:
                return exc.code, payload


class AuthTest(ApiTestBase):
    def test_healthz_is_public(self) -> None:
        status, body = self.request("GET", "/healthz", token=None)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIn("version", body)

    def test_api_requires_token(self) -> None:
        status, body = self.request("GET", "/api/v1/sessions", token=None)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "unauthorized")

    def test_bad_token_rejected(self) -> None:
        status, _ = self.request("GET", "/api/v1/sessions", token="nope")
        self.assertEqual(status, 401)

    def test_method_not_allowed(self) -> None:
        status, body = self.request("PUT", "/api/v1/sessions")
        self.assertEqual(status, 405)
        self.assertEqual(body["error"]["code"], "method_not_allowed")

    def test_unknown_path_404(self) -> None:
        status, _ = self.request("GET", "/api/v1/nope")
        self.assertEqual(status, 404)

    def test_revoked_device_loses_access(self) -> None:
        self.assertEqual(self.request("GET", "/api/v1/sessions")[0], 200)
        status, _ = self.request("DELETE", f"/api/v1/devices/{self.device_id}")
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", "/api/v1/sessions")[0], 401)


class SessionTest(ApiTestBase):
    def test_create_list_get(self) -> None:
        status, body = self.request("POST", "/api/v1/sessions", body={"title": "交表这件事"})
        self.assertEqual(status, 201)
        session_id = body["session"]["id"]

        status, listing = self.request("GET", "/api/v1/sessions")
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in listing["items"]], [session_id])

        status, got = self.request("GET", f"/api/v1/sessions/{session_id}")
        self.assertEqual(status, 200)
        self.assertEqual(got["session"]["title"], "交表这件事")

    def test_missing_title_rejected(self) -> None:
        status, body = self.request("POST", "/api/v1/sessions", body={})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "bad_request")

    def test_unknown_session_404(self) -> None:
        status, _ = self.request("GET", "/api/v1/sessions/does-not-exist")
        self.assertEqual(status, 404)


class MessageTest(ApiTestBase):
    def setUp(self) -> None:
        super().setUp()
        _, body = self.request("POST", "/api/v1/sessions", body={"title": "对话"})
        self.sid = body["session"]["id"]

    def test_post_runs_runner_and_returns_reply(self) -> None:
        status, body = self.request(
            "POST", f"/api/v1/sessions/{self.sid}/messages", body={"text": "你好", "client_msg_id": "c_1"}
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["message"]["direction"], "in")
        self.assertEqual(body["reply"]["text"], self.runner_text)
        self.assertEqual(body["run"]["ok"], True)
        self.assertEqual(len(self.runner.calls), 1)

    def test_client_msg_id_is_idempotent(self) -> None:
        first = self.request("POST", f"/api/v1/sessions/{self.sid}/messages", body={"text": "a", "client_msg_id": "c_x"})
        second = self.request("POST", f"/api/v1/sessions/{self.sid}/messages", body={"text": "b", "client_msg_id": "c_x"})
        self.assertEqual(first[0], 200)
        self.assertTrue(second[1]["duplicate"], "重复 client_msg_id 要标记 duplicate")
        self.assertNotIn("reply", second[1])
        self.assertEqual(len(self.runner.calls), 1, "幂等命中时不许再跑一次模型")

    def test_empty_message_rejected(self) -> None:
        status, body = self.request("POST", f"/api/v1/sessions/{self.sid}/messages", body={"text": "   "})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "empty_message")

    def test_list_messages_incremental(self) -> None:
        self.request("POST", f"/api/v1/sessions/{self.sid}/messages", body={"text": "第一句"})
        status, body = self.request("GET", f"/api/v1/sessions/{self.sid}/messages")
        self.assertEqual(status, 200)
        self.assertEqual([m["direction"] for m in body["items"]], ["in", "out"])
        first_seq = body["items"][0]["seq"]

        _, tail = self.request("GET", f"/api/v1/sessions/{self.sid}/messages?since_seq={first_seq}")
        self.assertEqual([m["direction"] for m in tail["items"]], ["out"])

    def test_ack(self) -> None:
        _, posted = self.request("POST", f"/api/v1/sessions/{self.sid}/messages", body={"text": "x"})
        status, body = self.request("POST", f"/api/v1/messages/{posted['message']['id']}/ack")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_runner_failure_maps_to_502(self) -> None:
        class Failing(FakeRunner):
            def run(self, task, *, on_event=None):
                super().run(task, on_event=on_event)
                return RunResult(ok=False, text="", exit_code=1, error="boom", duration_ms=1)

        self.service.runner = Failing()
        status, body = self.request("POST", f"/api/v1/sessions/{self.sid}/messages", body={"text": "x"})
        self.assertEqual(status, 502)
        self.assertEqual(body["run"]["error"], "boom")


class EventsTest(ApiTestBase):
    def test_sse_streams_existing_events(self) -> None:
        _, body = self.request("POST", "/api/v1/sessions", body={"title": "流"})
        sid = body["session"]["id"]
        self.request("POST", f"/api/v1/sessions/{sid}/messages", body={"text": "触发事件"})

        url = f"{self.base}/api/v1/events?since_seq=0"
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.token})
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/event-stream", resp.headers.get("Content-Type", ""))
            first = resp.readline().decode("utf-8")
            second = resp.readline().decode("utf-8")
        self.assertTrue(first.startswith("id: "), first)
        self.assertEqual(second.strip(), "event: message")

    def test_sse_respects_last_event_id(self) -> None:
        _, body = self.request("POST", "/api/v1/sessions", body={"title": "续传"})
        sid = body["session"]["id"]
        self.request("POST", f"/api/v1/sessions/{sid}/messages", body={"text": "一"})
        last = self.service.events.last_seq()

        url = f"{self.base}/api/v1/events?since_seq={last}"
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.token})
        with urllib.request.urlopen(req, timeout=3) as resp:
            try:
                resp.readline()
                got_data = True
            except (socket.timeout, TimeoutError, OSError):
                got_data = False
        self.assertFalse(got_data, "since_seq 之后没有新事件时不该立刻有数据")


class AttachmentTest(ApiTestBase):
    def test_chunked_upload_then_download(self) -> None:
        payload = b"hello mephisto" * 1000
        digest = digest_of_bytes(payload)
        status, body = self.request(
            "POST", "/api/v1/attachments", body={"filename": "a.txt", "size": len(payload), "sha256": digest}
        )
        self.assertEqual(status, 201, body)
        upload_id = body["upload_id"]
        self.assertEqual(body["chunk_count"], 1)

        status, body = self.request("PUT", f"/api/v1/attachments/{upload_id}/chunks/0", raw=payload)
        self.assertEqual(status, 200)
        self.assertEqual(body["received"], [0])

        status, body = self.request("POST", f"/api/v1/attachments/{upload_id}/complete")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["attachment_id"], digest)

        status, data = self.request("GET", f"/api/v1/attachments/{digest}")
        self.assertEqual(status, 200)
        self.assertEqual(data, payload)

    def test_digest_mismatch_is_rejected(self) -> None:
        payload = b"real bytes"
        wrong = "sha256:" + "0" * 64
        _, body = self.request(
            "POST", "/api/v1/attachments", body={"filename": "b.bin", "size": len(payload), "sha256": wrong}
        )
        self.request("PUT", f"/api/v1/attachments/{body['upload_id']}/chunks/0", raw=payload)
        status, error = self.request("POST", f"/api/v1/attachments/{body['upload_id']}/complete")
        self.assertEqual(status, 400)
        self.assertEqual(error["error"]["code"], "digest_mismatch")

    def test_incomplete_upload_rejected(self) -> None:
        payload = b"x" * 10
        _, body = self.request(
            "POST", "/api/v1/attachments",
            body={"filename": "c.bin", "size": len(payload) + 4 * 1024 * 1024, "sha256": "sha256:" + "a" * 64},
        )
        self.assertEqual(body["chunk_count"], 2)
        self.request("PUT", f"/api/v1/attachments/{body['upload_id']}/chunks/0", raw=b"x" * (4 * 1024 * 1024))
        status, error = self.request("POST", f"/api/v1/attachments/{body['upload_id']}/complete")
        self.assertEqual(status, 409)
        self.assertEqual(error["error"]["code"], "incomplete_upload")

    def test_message_can_carry_attachment(self) -> None:
        payload = b"report"
        digest = digest_of_bytes(payload)
        _, body = self.request(
            "POST", "/api/v1/attachments", body={"filename": "r.txt", "size": len(payload), "sha256": digest}
        )
        self.request("PUT", f"/api/v1/attachments/{body['upload_id']}/chunks/0", raw=payload)
        self.request("POST", f"/api/v1/attachments/{body['upload_id']}/complete")

        _, session = self.request("POST", "/api/v1/sessions", body={"title": "带附件"})
        sid = session["session"]["id"]
        status, posted = self.request(
            "POST", f"/api/v1/sessions/{sid}/messages", body={"text": "看这个", "attachment_ids": [digest]}
        )
        self.assertEqual(status, 200, posted)
        self.assertEqual(posted["message"]["attachments"][0]["filename"], "r.txt")

    def test_unknown_attachment_rejected(self) -> None:
        _, session = self.request("POST", "/api/v1/sessions", body={"title": "坏附件"})
        sid = session["session"]["id"]
        status, error = self.request(
            "POST", f"/api/v1/sessions/{sid}/messages",
            body={"text": "x", "attachment_ids": ["sha256:" + "f" * 64]},
        )
        self.assertEqual(status, 400)
        self.assertIn("附件", error["error"]["message"])


class NoticeTest(ApiTestBase):
    def test_list_and_ack(self) -> None:
        notice = Notice(
            id=new_ulid(),
            kind="reminder",
            title="交表",
            due_utc=NOW,
            created_at=NOW,
            origin="reminder:x@2026-10-26T15:00:00Z",
        )
        self.assertTrue(self.service.notices.insert(notice))

        status, body = self.request("GET", "/api/v1/notices?until_days=365")
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in body["items"]], [notice.id])

        status, body = self.request("POST", f"/api/v1/notices/{notice.id}/state", body={"state": "acked"})
        self.assertEqual(status, 200)
        self.assertEqual(body["notice"]["state"], "acked")

        _, after = self.request("GET", "/api/v1/notices?until_days=365")
        self.assertEqual(after["items"], [], "确认后不该再出现")

    def test_illegal_transition_maps_to_409(self) -> None:
        notice = Notice(id=new_ulid(), kind="reminder", title="x", due_utc=NOW, created_at=NOW)
        self.service.notices.insert(notice)
        self.request("POST", f"/api/v1/notices/{notice.id}/state", body={"state": "acked"})
        status, error = self.request("POST", f"/api/v1/notices/{notice.id}/state", body={"state": "acked"})
        self.assertEqual(status, 409)
        self.assertEqual(error["error"]["code"], "illegal_transition")

    def test_snooze(self) -> None:
        past = datetime.now(tz=UTC) - timedelta(hours=1)
        notice = Notice(id=new_ulid(), kind="reminder", title="x", due_utc=past, created_at=past)
        self.service.notices.insert(notice)
        status, body = self.request(
            "POST", f"/api/v1/notices/{notice.id}/state", body={"state": "snoozed", "snooze_minutes": 30}
        )
        self.assertEqual(status, 200)
        self.assertIsNotNone(body["notice"].get("snooze_until"))


if __name__ == "__main__":
    unittest.main()

"""HTTP 端点的实现。路由在这儿组装，判断在 app/domain 层。

约定：
- 成功返回 `Response(200, {...})`；失败抛 `HttpError(status, code, message)`。
- 鉴权在 `server.py` 统一做（`/healthz` 除外），handler 只管拿到 `request.device_id`。
- 该文件只做"取参数 → 调服务 → 组响应"，不写业务规则。
"""

from __future__ import annotations

import dataclasses
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from ...app.chat import ChatService
from ...app.dto import message_to_dict, notice_to_dict, session_to_dict, upload_to_dict
from ...blobs import CHUNK_SIZE, MAX_FILE_BYTES, BlobStore, UploadArea, digest_of_file
from ...domain import Attachment
from ...store import MessageRow
from .router import HttpError, Request, Response, Router, StreamResponse

MAX_LIMIT = 500


def _int(value: str | None, default: int, *, minimum: int = 0, maximum: int = MAX_LIMIT) -> int:
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except ValueError as exc:
        raise HttpError(400, "bad_query", f"参数应为整数：{value!r}") from exc
    return max(minimum, min(maximum, number))


class Api:
    def __init__(
        self,
        service: ChatService,
        *,
        blobs: BlobStore,
        uploads: UploadArea,
        version: str,
    ) -> None:
        self.service = service
        self.blobs = blobs
        self.uploads = uploads
        self.version = version
        self.started_at = time.monotonic()
        self.router = Router()
        self._wire()

    # ---------- 路由表 ----------
    def _wire(self) -> None:
        add = self.router.add
        add("GET", r"^/$", self.index)
        add("GET", r"^/healthz$", self.healthz)
        add("GET", r"^/api/v1/sessions$", self.sessions_list)
        add("POST", r"^/api/v1/sessions$", self.sessions_create)
        add("GET", r"^/api/v1/sessions/(?P<sid>[^/]+)$", self.sessions_get)
        add("GET", r"^/api/v1/sessions/(?P<sid>[^/]+)/messages$", self.messages_list)
        add("POST", r"^/api/v1/sessions/(?P<sid>[^/]+)/messages$", self.messages_post)
        add("POST", r"^/api/v1/messages/(?P<mid>[^/]+)/ack$", self.messages_ack)
        add("GET", r"^/api/v1/events$", self.events)
        add("GET", r"^/api/v1/notices$", self.notices_list)
        add("POST", r"^/api/v1/notices/(?P<nid>[^/]+)/state$", self.notices_state)
        add("POST", r"^/api/v1/attachments$", self.uploads_create)
        add("PUT", r"^/api/v1/attachments/(?P<uid>[^/]+)/chunks/(?P<idx>\d+)$", self.uploads_chunk)
        add("POST", r"^/api/v1/attachments/(?P<uid>[^/]+)/complete$", self.uploads_complete)
        add("GET", r"^/api/v1/attachments/(?P<aid>[^/]+)$", self.attachments_download)
        add("DELETE", r"^/api/v1/attachments/(?P<uid>[^/]+)$", self.uploads_abandon)
        add("DELETE", r"^/api/v1/devices/(?P<did>[^/]+)$", self.devices_revoke)

    # ---------- 调试页 ----------
    def index(self, request: Request) -> Response:
        page = Path(__file__).with_name("static") / "index.html"
        if not page.exists():
            raise HttpError(500, "missing_static", "调试页缺失")
        return Response(200, page.read_bytes(), {"Content-Type": "text/html; charset=utf-8"})

    # ---------- 健康检查 ----------
    def healthz(self, request: Request) -> Response:
        return Response(
            200,
            {
                "ok": True,
                "version": self.version,
                "uptime_seconds": int(time.monotonic() - self.started_at),
                "sessions": len(self.service.list_sessions(limit=MAX_LIMIT)),
                "pending_notices": self.service.notices.count_pending(),
            },
        )

    # ---------- 会话 ----------
    def sessions_list(self, request: Request) -> Response:
        since = _int(request.query.get("since_seq"), 0)
        limit = _int(request.query.get("limit"), 100, minimum=1)
        items = [session_to_dict(item) for item in self.service.list_sessions(since_seq=since, limit=limit)]
        return Response(200, {"items": items})

    def sessions_create(self, request: Request) -> Response:
        body = _object(request.body)
        title = _text(body, "title", required=True)
        kind = _text(body, "kind", default="chatter")
        session = self.service.create_session(title, kind=kind)
        return Response(201, {"session": session_to_dict(session)})

    def sessions_get(self, request: Request) -> Response:
        session = self.service.get_session(request.params["sid"])
        if session is None:
            raise HttpError(404, "session_not_found", "会话不存在")
        return Response(200, {"session": session_to_dict(session)})

    # ---------- 消息 ----------
    def messages_list(self, request: Request) -> Response:
        since = _int(request.query.get("since_seq"), 0)
        limit = _int(request.query.get("limit"), 100, minimum=1)
        session_id = request.params["sid"]
        if self.service.get_session(session_id) is None:
            raise HttpError(404, "session_not_found", "会话不存在")
        rows = self.service.list_messages(session_id, since_seq=since, limit=limit)
        return Response(200, {"items": [self._message_dto(row) for row in rows]})

    def messages_post(self, request: Request) -> Response:
        body = _object(request.body)
        text = _text(body, "text", default="")
        client_msg_id = _text(body, "client_msg_id", default=None)
        attachment_ids = body.get("attachment_ids") or []
        if not isinstance(attachment_ids, list) or not all(isinstance(i, str) for i in attachment_ids):
            raise HttpError(400, "bad_request", "attachment_ids 应为字符串数组")
        if not text.strip() and not attachment_ids:
            raise HttpError(400, "empty_message", "消息既没有文本也没有附件")

        try:
            outcome = self.service.post_message(
                request.params["sid"], text, client_msg_id=client_msg_id, attachment_ids=attachment_ids
            )
        except KeyError as exc:
            raise HttpError(404, "session_not_found", "会话不存在") from exc

        payload: dict[str, Any] = {
            "message": self._message_dto(outcome.in_message),
            "duplicate": outcome.duplicate,
        }
        if outcome.out_message is not None:
            payload["reply"] = self._message_dto(outcome.out_message)
        if outcome.run is not None:
            payload["run"] = {
                "ok": outcome.run.ok,
                "error": outcome.run.error,
                "duration_ms": outcome.run.duration_ms,
                "usage": dict(outcome.run.usage),
            }
        return Response(200 if outcome.run is None or outcome.run.ok else 502, payload)

    def messages_ack(self, request: Request) -> Response:
        ok = self.service.ack_message(request.params["mid"])
        if not ok:
            raise HttpError(404, "message_not_found", "消息不存在")
        return Response(200, {"ok": True})

    # ---------- 事件流 ----------
    def events(self, request: Request) -> Response | StreamResponse:
        since = _int(request.query.get("since_seq"), 0)
        header = request.headers.get("last-event-id")
        if header:
            try:
                since = max(since, int(header))
            except ValueError:
                pass
        return StreamResponse(since_seq=since)

    # ---------- 通知 ----------
    def notices_list(self, request: Request) -> Response:
        days = _int(request.query.get("until_days"), 7, minimum=1, maximum=365)
        until = self.service.clock.now() + timedelta(days=days)
        items = [notice_to_dict(item) for item in self.service.notices.list_pending(until=until)]
        return Response(200, {"items": items})

    def notices_state(self, request: Request) -> Response:
        body = _object(request.body)
        state = _text(body, "state", required=True)
        notice = self.service.notices.get(request.params["nid"])
        if notice is None:
            raise HttpError(404, "notice_not_found", "通知不存在")
        now = self.service.clock.now()
        try:
            if state == "acked":
                notice = notice.ack(now)
            elif state == "dismissed":
                notice = notice.dismiss(now)
            elif state == "missed":
                notice = notice.mark_missed(now)
            elif state == "snoozed":
                minutes = body.get("snooze_minutes")
                if not isinstance(minutes, int) or minutes <= 0:
                    raise HttpError(400, "bad_request", "snoozed 需要正整数 snooze_minutes")
                notice = notice.snooze(now + timedelta(minutes=minutes))
            else:
                raise HttpError(400, "bad_state", f"不支持的状态：{state!r}")
        except ValueError as exc:
            raise HttpError(409, "illegal_transition", str(exc)) from exc
        client_ref = body.get("client_ref")
        if isinstance(client_ref, str):
            notice = dataclasses.replace(notice, client_ref=client_ref)
        self.service.notices.update(notice)
        return Response(200, {"notice": notice_to_dict(notice)})

    # ---------- 附件 ----------
    def uploads_create(self, request: Request) -> Response:
        body = _object(request.body)
        filename = _text(body, "filename", required=True)[:255]
        size = body.get("size")
        sha256 = _text(body, "sha256", required=True)
        if not isinstance(size, int) or size <= 0:
            raise HttpError(400, "bad_request", "size 必须是正整数")
        if size > MAX_FILE_BYTES:
            raise HttpError(413, "file_too_large", f"单文件上限 {MAX_FILE_BYTES} 字节")
        chunk_count = max(1, -(-size // CHUNK_SIZE))
        row = self.service.uploads.create(
            filename=filename,
            size=size,
            sha256=sha256,
            chunk_size=CHUNK_SIZE,
            chunk_count=chunk_count,
            at=self.service.clock.now(),
            device_id=request.device_id,
        )
        return Response(201, upload_to_dict(row))

    def uploads_chunk(self, request: Request) -> Response:
        upload_id = request.params["uid"]
        index = int(request.params["idx"])
        row = self.service.uploads.get(upload_id)
        if row is None:
            raise HttpError(404, "upload_not_found", "上传不存在")
        data = request.body if isinstance(request.body, bytes) else b""
        if not data:
            raise HttpError(400, "empty_chunk", "分片为空")
        try:
            self.uploads.write_chunk(upload_id, index, data)
            row = self.service.uploads.mark_chunk(upload_id, index)
        except (ValueError, KeyError) as exc:
            raise HttpError(400, "bad_chunk", str(exc)) from exc
        return Response(200, upload_to_dict(row))

    def uploads_complete(self, request: Request) -> Response:
        upload_id = request.params["uid"]
        row = self.service.uploads.get(upload_id)
        if row is None:
            raise HttpError(404, "upload_not_found", "上传不存在")
        missing = [i for i in range(row.chunk_count) if i not in row.received]
        if missing:
            raise HttpError(409, "incomplete_upload", f"缺少分片：{missing[:5]}")
        assembled = self.uploads.dir_for(upload_id) / "assembled.bin"
        try:
            size = self.uploads.assemble(upload_id, row.chunk_count, assembled)
        except ValueError as exc:
            raise HttpError(409, "assemble_failed", str(exc)) from exc
        digest = digest_of_file(assembled)
        if size != row.size:
            self.uploads.cleanup(upload_id)
            raise HttpError(400, "size_mismatch", f"实际 {size} 字节，声明 {row.size}")
        if digest != row.sha256:
            self.uploads.cleanup(upload_id)
            raise HttpError(400, "digest_mismatch", f"实际 {digest}，声明 {row.sha256}")
        adopted = self.blobs.adopt(assembled)
        self.service.uploads.finish(upload_id, adopted)
        self.uploads.cleanup(upload_id)
        return Response(200, {"attachment_id": adopted, "digest": adopted, "size": size})

    def attachments_download(self, request: Request) -> Response:
        digest = request.params["aid"]
        try:
            path = self.blobs.path_for(digest)
        except ValueError as exc:
            raise HttpError(400, "bad_digest", str(exc)) from exc
        if not path.exists():
            raise HttpError(404, "attachment_not_found", "附件不存在")
        return Response(200, path.read_bytes(), {"Content-Type": "application/octet-stream"})

    def uploads_abandon(self, request: Request) -> Response:
        upload_id = request.params["uid"]
        self.service.uploads.abandon(upload_id)
        self.uploads.cleanup(upload_id)
        return Response(200, {"ok": True})

    # ---------- 设备 ----------
    def devices_revoke(self, request: Request) -> Response:
        ok = self.service.devices.revoke(request.params["did"], at=self.service.clock.now())
        return Response(200, {"ok": ok})

    # ---------- 辅助 ----------
    def _message_dto(self, row: MessageRow) -> dict[str, Any]:
        items = [
            {"filename": item.filename, "size": item.size, "digest": item.digest, "media_type": item.media_type}
            for item in self.service.attachments.list_by_message(row.id)
        ]
        return message_to_dict(row, attachments=items)


def _object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise HttpError(400, "bad_request", "请求体应为 JSON 对象")
    return value


def _text(body: dict[str, Any], key: str, *, required: bool = False, default: str | None = None) -> str | None:
    value = body.get(key, default)
    if value is None:
        if required:
            raise HttpError(400, "bad_request", f"缺少字段 {key}")
        return None
    if not isinstance(value, str):
        raise HttpError(400, "bad_request", f"字段 {key} 应为字符串")
    return value

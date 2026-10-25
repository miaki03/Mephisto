"""队列与守护进程的测试。

重点验三件在真实故障里会咬人的事：
1. 同一会话不会有两个 running 任务（部分唯一索引 + claim SQL 的 WHERE 条件）。
2. 进程被杀后，卡在 running 的任务能在启动时被放回队列。
3. 失败要留下一条 `kind=system` 的通知——不然你永远不知道它失败了。
"""

from __future__ import annotations

import shutil
import time
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mephisto.app.chat import ChatService
from mephisto.app.daemon import Daemon
from mephisto.blobs import BlobStore, UploadArea
from mephisto.clock import FakeClock
from mephisto.ids import new_ulid
from mephisto.ports import RunResult, Task
from mephisto.store import open_db
from mephisto.store.jobs import JobRepo

TMP_ROOT = Path(__file__).resolve().parent / "_tmp"


class FakeRunner:
    def __init__(self, *, text: str = "假回复", ok: bool = True, error: str | None = None) -> None:
        self.text = text
        self.ok = ok
        self.error = error
        self.calls: list[Task] = []

    def run(self, task: Task, *, on_event=None) -> RunResult:
        self.calls.append(task)
        return RunResult(
            ok=self.ok,
            text=self.text if self.ok else "",
            exit_code=0 if self.ok else 1,
            error=self.error,
            usage={"totalTokens": 10, "cacheReadTokens": 5},
            duration_ms=3,
        )


class DaemonTestBase(unittest.TestCase):
    def setUp(self) -> None:
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.dir = TMP_ROOT / f"daemon_{uuid.uuid4().hex[:12]}"
        self.dir.mkdir()
        self.db = open_db(self.dir)
        self.db.migrate()
        self.clock = FakeClock(datetime(2026, 10, 26, 7, 0, tzinfo=UTC))
        self.runner = FakeRunner()
        self.service = ChatService(
            self.db,
            self.runner,
            clock=self.clock,
            blob_root=self.dir / "blobs",
            upload_root=self.dir / "uploads",
        )
        self.jobs = JobRepo(self.db)
        self.daemon = Daemon(self.service, clock=self.clock)

    def tearDown(self) -> None:
        self.daemon.stop()
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def new_session(self, title: str = "任务") -> str:
        return self.service.create_session(title).id


class EnqueueTest(DaemonTestBase):
    def test_enqueue_creates_queued_job_and_message(self) -> None:
        sid = self.new_session()
        message, job, duplicate = self.service.enqueue_message(sid, "你好")
        self.assertFalse(duplicate)
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job.state, "queued")
        self.assertEqual(self.jobs.get(job.id).state, "queued")
        self.assertEqual(len(self.service.list_messages(sid)), 1, "入站消息要立刻落库")
        self.assertEqual(len(self.runner.calls), 0, "入队不该跑模型")

    def test_duplicate_client_msg_id_does_not_enqueue(self) -> None:
        sid = self.new_session()
        self.service.enqueue_message(sid, "a", client_msg_id="c_1")
        _, job, duplicate = self.service.enqueue_message(sid, "b", client_msg_id="c_1")
        self.assertTrue(duplicate)
        self.assertIsNone(job)
        self.assertEqual(self.jobs.counts()["queued"], 1, "只应有一个任务")
        self.assertEqual(len(self.service.list_messages(sid)), 1)


class DrainTest(DaemonTestBase):
    def test_drain_runs_job_and_appends_reply(self) -> None:
        sid = self.new_session()
        _, job, _ = self.service.enqueue_message(sid, "你好")
        self.assertTrue(self.daemon.drain_once())
        self.assertEqual(len(self.runner.calls), 1)
        rows = self.service.list_messages(sid)
        self.assertEqual([row.direction for row in rows], ["in", "out"])
        self.assertEqual(rows[1].text, self.runner.text)
        assert job is not None
        self.assertEqual(self.jobs.get(job.id).state, "done")

    def test_drain_returns_false_when_queue_empty(self) -> None:
        self.assertFalse(self.daemon.drain_once())

    def test_five_messages_do_not_cross_sessions(self) -> None:
        """并发提交 5 条到 3 个会话，回复必须落在各自会话里。"""
        first, second, third = (self.new_session(f"会话{i}") for i in range(3))
        targets = [first, second, third, first, second]
        for index, sid in enumerate(targets):
            self.service.enqueue_message(sid, f"消息{index}")
        while self.daemon.drain_once():
            pass
        self.assertEqual(len(self.runner.calls), 5)
        # first 与 second 各两条，third 一条；同一会话的两条都先入队（in, in），worker 再逐条回
        expected = {
            first: ["in", "in", "out", "out"],
            second: ["in", "in", "out", "out"],
            third: ["in", "out"],
        }
        for sid, want in expected.items():
            rows = self.service.list_messages(sid)
            self.assertEqual([row.direction for row in rows], want)
            self.assertTrue(all(row.session_id == sid for row in rows), "回复不能串会话")
        counts = self.jobs.counts()
        self.assertEqual(counts["queued"] + counts["running"], 0)
        self.assertEqual(counts["done"], 5)

    def test_same_session_never_runs_twice_concurrently(self) -> None:
        """claim 的 WHERE 条件必须跳过已有 running 的会话。"""
        sid = self.new_session()
        first, job1, _ = self.service.enqueue_message(sid, "一")
        _, job2, _ = self.service.enqueue_message(sid, "二")
        assert job1 is not None and job2 is not None
        claimed = self.jobs.claim_next(at=self.clock.now())
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, job1.id)
        second_claim = self.jobs.claim_next(at=self.clock.now())
        self.assertIsNone(second_claim, "同会话已有 running 时不能再抢")

        self.jobs.finish(job1.id, "done", at=self.clock.now())
        third_claim = self.jobs.claim_next(at=self.clock.now())
        self.assertIsNotNone(third_claim)
        assert third_claim is not None
        self.assertEqual(third_claim.id, job2.id)
        self.jobs.finish(job2.id, "done", at=self.clock.now())
        _ = first


class FailureTest(DaemonTestBase):
    def test_failed_run_creates_system_notice(self) -> None:
        self.service.runner = FakeRunner(ok=False, error="模型不可用")
        static_runner = self.service.runner
        sid = self.new_session()
        _, job, _ = self.service.enqueue_message(sid, "会失败的")
        self.daemon.drain_once()

        assert job is not None
        stored = self.jobs.get(job.id)
        self.assertEqual(stored.state, "failed")
        self.assertIn("模型不可用", stored.error or "")

        notices = self.service.notices.list_pending(until=self.clock.now() + timedelta(days=1))
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0].kind, "system")
        self.assertEqual(notices[0].session_id, sid)
        self.assertEqual(notices[0].origin, f"job:{job.id}:failed")
        _ = static_runner


class RecoveryTest(DaemonTestBase):
    def test_stale_running_job_is_requeued_on_start(self) -> None:
        """模拟"进程在跑任务时被杀"：job 停在 running，启动后应回到 queued。"""
        sid = self.new_session()
        _, job, _ = self.service.enqueue_message(sid, "跑到一半被杀")
        assert job is not None
        claimed = self.jobs.claim_next(at=self.clock.now())
        assert claimed is not None

        # 时间往后跳 2 小时，然后再启动
        self.clock.advance(hours=2)
        requeued = self.daemon.recover()
        self.assertEqual(requeued, 1)
        self.assertEqual(self.jobs.get(job.id).state, "queued")
        self.assertTrue(self.daemon.drain_once())
        self.assertEqual(self.jobs.get(job.id).state, "done")

    def test_fresh_running_job_is_not_requeued(self) -> None:
        sid = self.new_session()
        self.service.enqueue_message(sid, "刚开始跑")
        self.jobs.claim_next(at=self.clock.now())
        self.clock.advance(minutes=1)
        self.assertEqual(self.daemon.recover(), 0, "刚跑 1 分钟不该被判定为卡死")


class WorkerThreadTest(DaemonTestBase):
    def test_thread_drains_in_background(self) -> None:
        sid = self.new_session()
        self.service.enqueue_message(sid, "后台跑")
        self.daemon.start()
        try:
            for _ in range(100):
                if any(row.direction == "out" for row in self.service.list_messages(sid)):
                    break
                self.daemon.wake()
                time.sleep(0.02)
            rows = self.service.list_messages(sid)
            self.assertEqual([row.direction for row in rows], ["in", "out"])
        finally:
            self.daemon.stop()


if __name__ == "__main__":
    unittest.main()

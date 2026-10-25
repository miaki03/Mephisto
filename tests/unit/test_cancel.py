"""取消任务：排队中删行、运行中杀进程树。

这是三段撤销里的第②③段（第①段 0–3 秒在客户端本地，不发任何请求）。
服务端只保证一件事：**取消之后不会有残留进程**，也不会再落一条回复。
"""

from __future__ import annotations

import threading
import unittest

from tests.unit.test_daemon import DaemonTestBase


class BlockingRunner:
    """跑到一半卡住，直到 cancel() 被调用。用来模拟"正在执行时被打断"。"""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.calls = 0

    def run(self, task, *, on_event=None):
        from mephisto.ports import RunResult

        self.calls += 1
        self.started.set()
        self.cancelled.wait(timeout=10)
        return RunResult(ok=False, text="", exit_code=1, error="cancelled", duration_ms=1)

    def cancel(self) -> None:
        self.cancelled.set()


class CancelQueuedTest(DaemonTestBase):
    def test_cancel_queued_job(self) -> None:
        sid = self.new_session()
        _, job, _ = self.service.enqueue_message(sid, "还没跑")
        assert job is not None
        self.assertEqual(self.daemon.cancel(job.id), "queued")
        self.assertEqual(self.jobs.get(job.id).state, "cancelled")
        self.assertEqual(len(self.runner.calls), 0, "取消后不该再跑模型")
        self.assertFalse(self.daemon.drain_once())

    def test_cancel_unknown_job(self) -> None:
        self.assertEqual(self.daemon.cancel("01M5S7EQHFQA10MR0S5SPK779J"), "not_found")

    def test_cancel_finished_job_is_too_late(self) -> None:
        sid = self.new_session()
        _, job, _ = self.service.enqueue_message(sid, "已经跑完")
        assert job is not None
        self.daemon.drain_once()
        self.assertEqual(self.jobs.get(job.id).state, "done")
        self.assertEqual(self.daemon.cancel(job.id), "too_late")


class CancelRunningTest(DaemonTestBase):
    def test_cancel_running_job_kills_runner(self) -> None:
        runner = BlockingRunner()
        self.service.runner = runner
        sid = self.new_session()
        _, job, _ = self.service.enqueue_message(sid, "跑到一半")
        assert job is not None

        worker = threading.Thread(target=self.daemon.drain_once, daemon=True)
        worker.start()
        self.assertTrue(runner.started.wait(timeout=5), "任务应该已经开始跑")

        self.assertEqual(self.daemon.cancel(job.id), "interrupted")
        self.assertTrue(runner.cancelled.is_set(), "必须真的打断运行器")
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive(), "worker 应该已收尾")

        stored = self.jobs.get(job.id)
        self.assertEqual(stored.state, "cancelled")
        rows = self.service.list_messages(sid)
        self.assertEqual([row.direction for row in rows], ["in"], "被打断就不该有回复")

    def test_cancelled_run_creates_no_system_notice(self) -> None:
        runner = BlockingRunner()
        self.service.runner = runner
        sid = self.new_session()
        _, job, _ = self.service.enqueue_message(sid, "打断")
        assert job is not None
        worker = threading.Thread(target=self.daemon.drain_once, daemon=True)
        worker.start()
        runner.started.wait(timeout=5)
        self.daemon.cancel(job.id)
        worker.join(timeout=10)

        pending = self.service.notices.list_pending(until=self.service.clock.now().replace(year=2040))
        self.assertEqual([n for n in pending if n.kind == "system"], [], "主动取消不是故障")


if __name__ == "__main__":
    unittest.main()

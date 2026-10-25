"""守护进程：一个 worker 线程 + 一次唤醒。

设计取舍：**不用 asyncio**。SMTP/IMAP/SQLite 都是阻塞的，`ThreadingHTTPServer`
也是线程模型；混进一个事件循环只会让"哪里阻塞了"更难查。

队列的"真值"在 `job` 表里，进程内 `Event` 只负责"有新活儿了，别睡了"。
所以进程被杀不会丢任务——重启时 `requeue_stale()` 把它们放回去。
"""

from __future__ import annotations

import sys
import threading
import traceback
from datetime import datetime

from ..clock import Clock, SystemClock
from ..store import Database
from ..store.jobs import JobRepo
from .chat import ChatService

DEFAULT_POLL_SECONDS = 1.0
STALE_AFTER_SECONDS = 30 * 60


class Daemon:
    def __init__(
        self,
        service: ChatService,
        *,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        stale_after_seconds: int = STALE_AFTER_SECONDS,
        clock: Clock | None = None,
    ) -> None:
        self.service = service
        self.jobs = JobRepo(service.db)
        self.poll_seconds = poll_seconds
        self.stale_after_seconds = stale_after_seconds
        self.clock = clock or service.clock
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.processed = 0
        self.failed = 0

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self._thread is not None:
            return
        self.recover()
        self._thread = threading.Thread(target=self._loop, name="mephisto-worker", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def recover(self) -> int:
        """启动恢复：把卡住的 running 放回队列。"""
        return self.jobs.requeue_stale(older_than_seconds=self.stale_after_seconds, at=self.clock.now())

    def wake(self) -> None:
        self._wake.set()

    # ---------- 干活 ----------
    def drain_once(self) -> bool:
        """抢占并执行一个任务。返回是否真的干了活（测试用这个做确定性驱动）。"""
        job = self.jobs.claim_next(at=self.clock.now())
        if job is None:
            return False
        self.run_job(job)
        return True

    def run_job(self, job) -> None:
        try:
            self.service.execute_job(job)
            self.processed += 1
        except Exception:
            self.failed += 1
            traceback.print_exc(file=sys.stderr)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                did = self.drain_once()
            except Exception:  # 单次失败不能拖垮线程
                traceback.print_exc(file=sys.stderr)
                did = False
            if not did:
                self._wake.wait(self.poll_seconds)

    def stats(self) -> dict[str, int]:
        from ..store.jobs import counts_of

        data = dict(counts_of(self.jobs))
        data["processed"] = self.processed
        data["failed"] = self.failed
        return data

    def now(self) -> datetime:
        return self.clock.now()

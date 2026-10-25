"""调度器：到点把规则展开成 notice，并把过期未确认的标成 missed。

三条规则（对应 docs/15 §5、docs/50）：
1. **幂等靠 `origin`**：同一提醒的同一时刻只有一条 notice，扫多少次都一样。
2. **宽限 2 小时**：超时未确认的标 `missed`，并汇总成一条"你错过了 N 条提醒"，
   而不是让几十条旧提醒一起炸出来。
3. **睡眠上限 60 秒**：循环里最长睡 60 秒，这样时钟跳变/被动唤醒都能及时处理。
"""

from __future__ import annotations

import sys
import threading
import traceback
from datetime import datetime, timedelta
from typing import Any

from ..clock import Clock, SystemClock
from ..domain import Notice
from ..ids import new_ulid
from ..store import Database, NoticeRepo
from ..store.reminders import ReminderRepo
from .reminders import ReminderService

DEFAULT_TICK_SECONDS = 60.0
DEFAULT_GRACE = timedelta(hours=2)


class Scheduler:
    def __init__(
        self,
        service: ReminderService,
        *,
        clock: Clock | None = None,
        grace: timedelta = DEFAULT_GRACE,
        tick_seconds: float = DEFAULT_TICK_SECONDS,
    ) -> None:
        self.service = service
        self.clock = clock or service.clock
        self.grace = grace
        self.tick_seconds = min(tick_seconds, 60.0)
        self.reminders: ReminderRepo = service.reminders
        self.notices: NoticeRepo = service.notices
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.ticks = 0

    # ---------- 一轮 ----------
    def tick(self, *, now: datetime | None = None) -> dict[str, int]:
        """顺序很重要：**先清理旧 pending，再触发本轮到期的**。

        反过来的话，刚触发的提醒会在同一轮里被判定为"过期"——那会让一次正常的
        触发立刻变成"你错过了 N 条提醒"。
        """
        moment = now or self.clock.now()
        stale = self._expire_overdue(moment)
        fired, late = self._fire_due(moment)
        self.ticks += 1
        return {"fired": fired, "late": late, "missed": stale}

    def _fire_due(self, now: datetime) -> tuple[int, int]:
        """到点就展开成 notice。超过宽限的**不单独发**，进汇总（否则开机时会被几十条旧提醒刷屏）。"""
        fired = late = 0
        for reminder in self.reminders.list_due(until=now):
            lateness = now - reminder.due_at_utc
            if lateness > self.grace:
                self.reminders.update(reminder.fired_once())
                self._summary([], now, extra=1, sample=reminder.title)
                continue
            suffix = "（延迟）" if lateness > timedelta(minutes=1) else ""
            self.service.materialize(reminder, title_suffix=suffix)
            self.reminders.update(reminder.fired_once())
            fired += 1
            late += 1 if suffix else 0
        return fired, late

    def _expire_overdue(self, now: datetime) -> int:
        cutoff = now - self.grace
        stale = self.notices.list_pending(until=cutoff)
        if not stale:
            return 0
        for notice in stale:
            self.notices.update(notice.mark_missed(now))
        self._summary(stale, now)
        return len(stale)

    def _summary(self, stale: list[Notice], now: datetime, *, extra: int = 0, sample: str = "") -> None:
        """一天一条汇总，`origin` 带日期保证幂等。"""
        origin = f"missed:{now.date().isoformat()}"
        if self.notices.by_origin(origin) is not None:
            return
        titles = "、".join(item.title for item in stale[:5])
        if extra and sample:
            titles = f"{titles}、{sample}" if titles else sample
        total = len(stale) + extra
        suffix = "…" if total > 5 else ""
        self.notices.insert(
            Notice(
                id=new_ulid(),
                kind="system",
                title=f"你错过了 {total} 条提醒",
                body=f"（超过宽限期未确认）{titles}{suffix}",
                due_utc=now,
                created_at=now,
                origin=origin,
            )
        )
    # ---------- 线程 ----------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="mephisto-scheduler", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                traceback.print_exc(file=sys.stderr)
            self._stop.wait(self.tick_seconds)


def build_scheduler(service: ReminderService) -> Scheduler:
    return Scheduler(service)


def scheduler_stats(scheduler: Scheduler) -> dict[str, Any]:
    return {"ticks": scheduler.ticks, "pending_notices": scheduler.notices.count_pending()}

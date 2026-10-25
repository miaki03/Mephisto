"""调度器测试：幂等、宽限、错过汇总——这三件事错一件，提醒系统就不可信。"""

from __future__ import annotations

import shutil
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mephisto.app.reminders import ReminderService
from mephisto.app.scheduler import Scheduler
from mephisto.clock import FakeClock
from mephisto.store import open_db

TMP_ROOT = Path(__file__).resolve().parent / "_tmp"
NOW = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)


class SchedulerTest(unittest.TestCase):
    def setUp(self) -> None:
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.dir = TMP_ROOT / f"sched_{uuid.uuid4().hex[:12]}"
        self.dir.mkdir()
        self.db = open_db(self.dir)
        self.db.migrate()
        self.clock = FakeClock(NOW)
        self.service = ReminderService(self.db, clock=self.clock, tz="Asia/Shanghai")
        self.scheduler = Scheduler(self.service, clock=self.clock)

    def tearDown(self) -> None:
        self.scheduler.stop()
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def pending(self):
        return self.service.notices.list_pending(until=self.clock.now() + timedelta(days=365))

    def test_due_reminder_becomes_notice(self) -> None:
        self.service.create("交表", "3分钟后")
        self.assertEqual(self.scheduler.tick()["fired"], 0)
        self.clock.advance(minutes=4)
        result = self.scheduler.tick()
        self.assertEqual(result["fired"], 1)
        self.assertEqual(len(self.pending()), 1)
        self.assertEqual(self.pending()[0].title, "交表")

    def test_second_tick_does_not_duplicate(self) -> None:
        self.service.create("交表", "3分钟后")
        self.clock.advance(minutes=4)
        self.scheduler.tick()
        self.scheduler.tick()
        self.scheduler.tick()
        self.assertEqual(len(self.pending()), 1, "同一提醒只能展开一次")

    def test_fired_reminder_is_marked(self) -> None:
        reminder = self.service.create("交表", "3分钟后")
        self.clock.advance(minutes=4)
        self.scheduler.tick()
        stored = self.service.reminders.get(reminder.id)
        self.assertEqual(stored.state, "fired")
        self.assertEqual(stored.fire_count, 1)

    def test_late_within_grace_is_marked_delayed(self) -> None:
        """关机 3 小时开机：宽限内（2h）之外——本例是 1 小时，应带（延迟）。"""
        self.service.create("交表", "3分钟后")
        self.clock.advance(minutes=63)
        self.scheduler.tick()
        titles = [item.title for item in self.pending()]
        self.assertIn("交表（延迟）", titles)

    def test_beyond_grace_goes_to_summary(self) -> None:
        """超过 2 小时宽限的提醒不单独发，只进"你错过了"汇总。"""
        self.service.create("很久以前", "3分钟后")
        self.clock.advance(hours=3)
        result = self.scheduler.tick()
        self.assertEqual(result["fired"], 0)
        notices = self.pending()
        self.assertEqual(len(notices), 1)
        self.assertIn("你错过了", notices[0].title)
        self.assertIn("很久以前", notices[0].body or "")

    def test_summary_is_once_per_day(self) -> None:
        self.service.create("a", "3分钟后")
        self.clock.advance(hours=3)
        self.scheduler.tick()
        self.service.create("b", "3分钟后")
        self.clock.advance(hours=3)
        self.scheduler.tick()
        # 注意：汇总本身也是一条 notice，可能因为没人确认而被后续 tick 标成 missed，
        # 所以这里直接查库，而不是只看 pending。
        rows = self.db.query_all("SELECT title FROM notice WHERE title LIKE '%你错过了%'")
        self.assertEqual(len(rows), 1, "同一天只汇总一次")

    def test_stale_pending_notice_is_missed(self) -> None:
        self.service.create("确认过的", "3分钟后")
        self.clock.advance(minutes=4)
        self.scheduler.tick()
        self.clock.advance(hours=3)          # 一直没人确认
        result = self.scheduler.tick()
        self.assertEqual(result["missed"], 1)
        remaining = self.service.notices.list_pending(until=self.clock.now() + timedelta(days=1))
        # 原来那条已被标 missed；此刻 pending 的只有"你错过了"汇总
        self.assertEqual(len(remaining), 1)
        self.assertIn("你错过了", remaining[0].title)
        self.assertNotEqual(remaining[0].title, "确认过的")

    def test_acked_notice_is_never_missed(self) -> None:
        self.service.create("点过确认", "3分钟后")
        self.clock.advance(minutes=4)
        self.scheduler.tick()
        notice = self.pending()[0]
        self.service.notices.update(notice.ack(self.clock.now()))
        self.clock.advance(hours=3)
        self.assertEqual(self.scheduler.tick()["missed"], 0)

    def test_tick_is_safe_with_empty_db(self) -> None:
        result = self.scheduler.tick()
        self.assertEqual(result, {"fired": 0, "late": 0, "missed": 0})


if __name__ == "__main__":
    unittest.main()

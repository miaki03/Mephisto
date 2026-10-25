from __future__ import annotations

import shutil
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mephisto.app.reminders import ReminderService, split_time_and_title
from mephisto.app.timeparse import TimeParseError, parse_when
from mephisto.clock import FakeClock
from mephisto.store import open_db

TMP_ROOT = Path(__file__).resolve().parent / "_tmp"
NOW = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)      # 北京时间 15:00


class TimeParseTest(unittest.TestCase):
    def test_relative_minutes(self) -> None:
        self.assertEqual(parse_when("3分钟后", now=NOW), NOW + timedelta(minutes=3))
        self.assertEqual(parse_when("2 小时", now=NOW), NOW + timedelta(hours=2))
        self.assertEqual(parse_when("in 30 minutes", now=NOW), NOW + timedelta(minutes=30))

    def test_clock_time_tomorrow(self) -> None:
        # 北京时间 15:00 → 明天 15:00 是 +1 天
        parsed = parse_when("明天15:00", now=NOW, tz="Asia/Shanghai")
        self.assertEqual(parsed, NOW + timedelta(days=1))

    def test_clock_time_passed_rolls_to_tomorrow(self) -> None:
        parsed = parse_when("9:00", now=NOW, tz="Asia/Shanghai")   # 今天 09:00 已过
        self.assertEqual(parsed, NOW + timedelta(days=1, hours=-6))

    def test_afternoon_period(self) -> None:
        parsed = parse_when("明天下午3点", now=NOW, tz="Asia/Shanghai")
        self.assertEqual(parsed, NOW + timedelta(days=1))

    def test_half_past(self) -> None:
        parsed = parse_when("明天 15点半", now=NOW, tz="Asia/Shanghai")
        self.assertEqual(parsed, NOW + timedelta(days=1, minutes=30))

    def test_iso_with_offset(self) -> None:
        parsed = parse_when("2026-10-27T15:00+08:00", now=NOW)
        self.assertEqual(parsed, datetime(2026, 10, 27, 7, 0, tzinfo=UTC))

    def test_naive_dictated_by_tz(self) -> None:
        parsed = parse_when("2026-10-27T15:00", now=NOW, tz="Asia/Shanghai")
        self.assertEqual(parsed, datetime(2026, 10, 27, 7, 0, tzinfo=UTC))

    def test_garbage_rejected(self) -> None:
        for bad in ("随便什么时候", "", "三分钟后"):
            with self.subTest(bad=bad):
                with self.assertRaises(TimeParseError):
                    parse_when(bad, now=NOW)

    def test_unknown_zone_rejected(self) -> None:
        with self.assertRaises(TimeParseError):
            parse_when("明天15:00", now=NOW, tz="Mars/Olympus")


class SplitTest(unittest.TestCase):
    def test_splits_time_and_title(self) -> None:
        self.assertEqual(split_time_and_title("3分钟后 提醒我喝水", now=NOW), ("3分钟后", "提醒我喝水"))
        self.assertEqual(split_time_and_title("明天15:00 交表", now=NOW), ("明天15:00", "交表"))

    def test_no_time_rejected(self) -> None:
        with self.assertRaises(TimeParseError):
            split_time_and_title("提醒我喝水", now=NOW)


class ReminderServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.dir = TMP_ROOT / f"rem_{uuid.uuid4().hex[:12]}"
        self.dir.mkdir()
        self.db = open_db(self.dir)
        self.db.migrate()
        self.clock = FakeClock(NOW)
        self.service = ReminderService(self.db, clock=self.clock, tz="Asia/Shanghai")

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_create_and_materialize(self) -> None:
        reminder = self.service.create("交表", "3分钟后")
        self.assertEqual(reminder.state, "scheduled")
        notice = self.service.materialize(reminder)
        self.assertEqual(notice.kind, "reminder")
        self.assertEqual(notice.due_utc, reminder.due_at_utc)
        self.assertEqual(notice.origin, reminder.origin_for(reminder.due_at_utc))

    def test_materialize_is_idempotent(self) -> None:
        reminder = self.service.create("交表", "3分钟后")
        first = self.service.materialize(reminder)
        second = self.service.materialize(reminder)
        self.assertEqual(first.id, second.id, "同一时刻只该有一条 notice")

    def test_origin_is_unique_per_occurrence(self) -> None:
        reminder = self.service.create("交表", "3分钟后")
        moved = self.service.reschedule(reminder.id, "10分钟后")
        a = self.service.materialize(reminder)
        b = self.service.materialize(moved)
        self.assertNotEqual(a.origin, b.origin)

    def test_create_rejects_past(self) -> None:
        with self.assertRaises(TimeParseError):
            self.service.create("过去", NOW - timedelta(minutes=1))

    def test_cancel(self) -> None:
        reminder = self.service.create("取消我", "10分钟后")
        self.assertTrue(self.service.cancel(reminder.id))
        self.assertFalse(self.service.cancel(reminder.id), "重复取消返回 False")
        self.assertEqual(self.service.reminders.get(reminder.id).state, "cancelled")

    def test_reschedule_only_scheduled(self) -> None:
        reminder = self.service.create("改时间", "10分钟后")
        self.service.cancel(reminder.id)
        with self.assertRaises(ValueError):
            self.service.reschedule(reminder.id, "20分钟后")

    def test_upcoming_only_scheduled(self) -> None:
        first = self.service.create("一", "10分钟后")
        self.service.create("二", "20分钟后")
        self.service.cancel(first.id)
        titles = [item.title for item in self.service.upcoming()]
        self.assertEqual(titles, ["二"])


if __name__ == "__main__":
    unittest.main()

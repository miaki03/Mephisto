from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta, timezone

from mephisto.clock import (
    FakeClock,
    SystemClock,
    from_epoch_ms,
    to_epoch_ms,
    to_utc,
)


class FakeClockTest(unittest.TestCase):
    def test_advances(self) -> None:
        clock = FakeClock()
        start = clock.now()
        clock.advance(hours=3, minutes=30)
        self.assertEqual(clock.now() - start, timedelta(hours=3, minutes=30))

    def test_can_be_set_backwards(self) -> None:
        """模拟时钟回拨：调度器必须能扛住，测试也必须能复现。"""
        clock = FakeClock()
        clock.set(datetime(2026, 5, 1, tzinfo=UTC))
        clock.set(datetime(2026, 4, 1, tzinfo=UTC))
        self.assertEqual(clock.now(), datetime(2026, 4, 1, tzinfo=UTC))


class UtcTest(unittest.TestCase):
    def test_rejects_naive(self) -> None:
        with self.assertRaises(ValueError):
            to_utc(datetime(2026, 1, 1))

    def test_converts_other_zones(self) -> None:
        shanghai = timezone(timedelta(hours=8))
        local = datetime(2026, 10, 26, 15, 0, tzinfo=shanghai)
        self.assertEqual(to_utc(local), datetime(2026, 10, 26, 7, 0, tzinfo=UTC))

    def test_epoch_roundtrip(self) -> None:
        moment = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)
        self.assertEqual(from_epoch_ms(to_epoch_ms(moment)), moment)

    def test_system_clock_is_aware_utc(self) -> None:
        now = SystemClock().now()
        self.assertIsNotNone(now.tzinfo)
        self.assertEqual(now.utcoffset(), timedelta(0))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from mephisto.domain import Notice
from mephisto.domain.codec import DecodeError
from mephisto.ids import new_ulid

DUE = datetime(2026, 10, 26, 15, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)


def make_notice(**overrides) -> dict:
    data = {
        "id": new_ulid(),
        "kind": "reminder",
        "title": "交表",
        "body": "下周三下午三点提醒我交表",
        "due_utc": "2026-10-26T15:00:00+00:00",
        "created_at": "2026-10-26T07:00:00+00:00",
        "state": "pending",
        "origin": "reminder:abc@2026-10-26T15:00:00Z",
    }
    data.update(overrides)
    return data


class NoticeRoundtripTest(unittest.TestCase):
    def test_roundtrip_pending(self) -> None:
        notice = Notice.from_dict(make_notice())
        self.assertEqual(Notice.from_dict(notice.to_dict()), notice)
        self.assertFalse(notice.is_due(NOW))
        self.assertFalse(notice.is_terminal())

    def test_null_means_unknown_for_body(self) -> None:
        notice = Notice.from_dict(make_notice(body=None))
        self.assertIsNone(notice.body)

    def test_unknown_kind_rejected(self) -> None:
        with self.assertRaises(DecodeError):
            Notice.from_dict(make_notice(kind="alarm"))

    def test_title_required(self) -> None:
        with self.assertRaises(DecodeError):
            Notice.from_dict(make_notice(title=""))


class NoticeStateMachineTest(unittest.TestCase):
    def test_ack(self) -> None:
        notice = Notice.from_dict(make_notice())
        acked = notice.ack(NOW)
        self.assertEqual(acked.state, "acked")
        self.assertEqual(acked.acked_at, NOW)
        self.assertTrue(acked.is_terminal())

    def test_illegal_transition_from_terminal(self) -> None:
        acked = Notice.from_dict(make_notice()).ack(NOW)
        with self.assertRaises(ValueError):
            acked.ack(NOW)
        with self.assertRaises(ValueError):
            acked.dismiss(NOW)
        with self.assertRaises(ValueError):
            acked.mark_missed(NOW)

    def test_unknown_state_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Notice.from_dict(make_notice(state="running"))

    def test_terminal_requires_acked_at(self) -> None:
        with self.assertRaises(ValueError):
            Notice.from_dict(make_notice(state="acked"))

    def test_pending_must_not_have_acked_at(self) -> None:
        with self.assertRaises(ValueError):
            Notice.from_dict(make_notice(acked_at="2026-10-26T07:00:00+00:00"))


class NoticeSnoozeTest(unittest.TestCase):
    def test_snooze_shifts_due(self) -> None:
        notice = Notice.from_dict(make_notice()).snooze(DUE + timedelta(hours=1))
        self.assertFalse(notice.is_due(DUE + timedelta(minutes=1)))
        self.assertTrue(notice.is_due(DUE + timedelta(hours=2)))

    def test_snooze_must_be_later(self) -> None:
        notice = Notice.from_dict(make_notice())
        with self.assertRaises(ValueError):
            notice.snooze(DUE)
        with self.assertRaises(ValueError):
            notice.snooze(DUE - timedelta(minutes=1))

    def test_snooze_only_from_pending(self) -> None:
        dismissed = Notice.from_dict(make_notice()).dismiss(NOW)
        with self.assertRaises(ValueError):
            dismissed.snooze(DUE + timedelta(hours=1))

    def test_snooze_until_must_be_after_due_in_data(self) -> None:
        with self.assertRaises(ValueError):
            Notice.from_dict(make_notice(snooze_until="2026-10-26T14:00:00+00:00"))


class NoticeDueTest(unittest.TestCase):
    def test_due_at_exact_moment(self) -> None:
        notice = Notice.from_dict(make_notice())
        self.assertTrue(notice.is_due(DUE))

    def test_terminal_never_due(self) -> None:
        self.assertFalse(Notice.from_dict(make_notice()).ack(NOW).is_due(DUE + timedelta(days=1)))


if __name__ == "__main__":
    unittest.main()

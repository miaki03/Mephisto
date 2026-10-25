from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from mephisto.domain import Session
from mephisto.domain.codec import DecodeError
from mephisto.ids import new_ulid

T0 = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)


def make_session(**overrides) -> dict:
    data = {
        "id": new_ulid(),
        "title": "交表这件事",
        "kind": "task",
        "state": "active",
        "created_at": "2026-10-26T07:00:00+00:00",
        "last_active_at": "2026-10-26T08:00:00+00:00",
        "seq": 3,
    }
    data.update(overrides)
    return data


class SessionTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        session = Session.from_dict(make_session())
        self.assertEqual(Session.from_dict(session.to_dict()), session)
        self.assertEqual(session.seq, 3)

    def test_touch_moves_time_and_cursor(self) -> None:
        session = Session.from_dict(make_session())
        later = T0 + timedelta(hours=2)
        moved = session.touch(later, seq=9)
        self.assertEqual(moved.last_active_at, later)
        self.assertEqual(moved.seq, 9)
        self.assertEqual(session.seq, 3, "frozen：原对象不能被就地改")

    def test_touch_rejects_backwards(self) -> None:
        session = Session.from_dict(make_session())
        with self.assertRaises(ValueError):
            session.touch(T0)

    def test_archive(self) -> None:
        self.assertEqual(Session.from_dict(make_session()).archive().state, "archived")

    def test_bad_kind(self) -> None:
        with self.assertRaises(DecodeError):
            Session.from_dict(make_session(kind="chatroom"))

    def test_title_bounds(self) -> None:
        with self.assertRaises(DecodeError):
            Session.from_dict(make_session(title=""))
        with self.assertRaises(DecodeError):
            Session.from_dict(make_session(title="长" * 121))

    def test_last_active_before_created_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Session.from_dict(
                make_session(created_at="2026-10-26T08:00:00+00:00", last_active_at="2026-10-26T07:00:00+00:00")
            )

    def test_unknown_field_rejected(self) -> None:
        with self.assertRaises(DecodeError):
            Session.from_dict(make_session(owner="me"))


if __name__ == "__main__":
    unittest.main()

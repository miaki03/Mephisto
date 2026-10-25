"""提醒规则的读写。展开成 notice 的动作在 `app/reminders.py`，这里只管行。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from ..domain.reminder import Reminder
from .db import Database, from_ms, to_ms

_COLUMNS = (
    "id, owner, title, body, due_at_utc, tz, rrule, origin_session, channels,"
    " priority, state, fire_count, created_at"
)


def _to_reminder(row: sqlite3.Row) -> Reminder:
    return Reminder(
        id=row["id"],
        title=row["title"],
        due_at_utc=from_ms(row["due_at_utc"]),
        tz=row["tz"],
        created_at=from_ms(row["created_at"]),
        owner=row["owner"],
        body=row["body"],
        rrule=row["rrule"],
        origin_session=row["origin_session"],
        channels=tuple(json.loads(row["channels"])),
        priority=row["priority"],
        state=row["state"],
        fire_count=row["fire_count"],
    )


class ReminderRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, reminder: Reminder) -> None:
        self._db.execute(
            f"INSERT INTO reminder ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                reminder.id,
                reminder.owner,
                reminder.title,
                reminder.body,
                to_ms(reminder.due_at_utc),
                reminder.tz,
                reminder.rrule,
                reminder.origin_session,
                json.dumps(list(reminder.channels), ensure_ascii=False),
                reminder.priority,
                reminder.state,
                reminder.fire_count,
                to_ms(reminder.created_at),
            ),
        )

    def get(self, reminder_id: str) -> Reminder | None:
        row = self._db.query_one(f"SELECT {_COLUMNS} FROM reminder WHERE id = ?", (reminder_id,))
        return None if row is None else _to_reminder(row)

    def update(self, reminder: Reminder) -> None:
        self._db.execute(
            "UPDATE reminder SET state = ?, fire_count = ?, due_at_utc = ?, title = ?, body = ? WHERE id = ?",
            (
                reminder.state,
                reminder.fire_count,
                to_ms(reminder.due_at_utc),
                reminder.title,
                reminder.body,
                reminder.id,
            ),
        )

    def list_due(self, *, until: datetime, limit: int = 200) -> list[Reminder]:
        rows = self._db.query_all(
            f"SELECT {_COLUMNS} FROM reminder WHERE state = 'scheduled' AND due_at_utc <= ?"
            " ORDER BY due_at_utc LIMIT ?",
            (to_ms(until), limit),
        )
        return [_to_reminder(row) for row in rows]

    def list_scheduled(self, *, limit: int = 200) -> list[Reminder]:
        rows = self._db.query_all(
            f"SELECT {_COLUMNS} FROM reminder WHERE state = 'scheduled' ORDER BY due_at_utc LIMIT ?",
            (limit,),
        )
        return [_to_reminder(row) for row in rows]

    def count(self) -> int:
        return int(self._db.scalar("SELECT COUNT(*) FROM reminder") or 0)

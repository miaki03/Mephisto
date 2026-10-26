"""通知（`notice`）的读写。

幂等本体是 `origin` 上的唯一索引：同一提醒的同一时刻，无论扫描几次、重启几次，
`insert()` 只会成功一次，第二次返回 False 而不是抛异常。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from ..domain import Notice
from .db import Database, from_ms, to_ms

_COLUMNS = (
    "id, kind, session_id, title, body, due_utc, state, acked_at, snooze_until,"
    " origin, client_ref, created_at, rrule"
)


def _to_notice(row: sqlite3.Row) -> Notice:
    return Notice(
        id=row["id"],
        kind=row["kind"],
        title=row["title"],
        body=row["body"],
        due_utc=from_ms(row["due_utc"]),
        created_at=from_ms(row["created_at"]),
        state=row["state"],
        session_id=row["session_id"],
        origin=row["origin"],
        client_ref=row["client_ref"],
        rrule=row["rrule"],
        acked_at=from_ms(row["acked_at"]) if row["acked_at"] is not None else None,
        snooze_until=from_ms(row["snooze_until"]) if row["snooze_until"] is not None else None,
    )


class NoticeRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, notice: Notice) -> bool:
        """返回是否新增。`origin` 重复时返回 False（幂等，不抛异常）。"""
        cursor = self._db.execute(
            f"INSERT OR IGNORE INTO notice ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                notice.id,
                notice.kind,
                notice.session_id,
                notice.title,
                notice.body,
                to_ms(notice.due_utc),
                notice.state,
                to_ms(notice.acked_at) if notice.acked_at is not None else None,
                to_ms(notice.snooze_until) if notice.snooze_until is not None else None,
                notice.origin,
                notice.client_ref,
                to_ms(notice.created_at),
                notice.rrule,
            ),
        )
        return cursor.rowcount > 0

    def get(self, notice_id: str) -> Notice | None:
        row = self._db.query_one(f"SELECT {_COLUMNS} FROM notice WHERE id = ?", (notice_id,))
        return None if row is None else _to_notice(row)

    def by_origin(self, origin: str) -> Notice | None:
        row = self._db.query_one(f"SELECT {_COLUMNS} FROM notice WHERE origin = ?", (origin,))
        return None if row is None else _to_notice(row)

    def list_pending(self, *, until: datetime, limit: int = 200) -> list[Notice]:
        """所有 state=pending 且（原定时刻或推迟时刻）早于 `until` 的通知。"""
        rows = self._db.query_all(
            f"SELECT {_COLUMNS} FROM notice"
            " WHERE state = 'pending' AND COALESCE(snooze_until, due_utc) <= ?"
            " ORDER BY COALESCE(snooze_until, due_utc) LIMIT ?",
            (to_ms(until), limit),
        )
        return [_to_notice(row) for row in rows]

    def update(self, notice: Notice) -> None:
        self._db.execute(
            "UPDATE notice SET state = ?, acked_at = ?, snooze_until = ?, client_ref = ? WHERE id = ?",
            (
                notice.state,
                to_ms(notice.acked_at) if notice.acked_at is not None else None,
                to_ms(notice.snooze_until) if notice.snooze_until is not None else None,
                notice.client_ref,
                notice.id,
            ),
        )

    def count_pending(self) -> int:
        return int(self._db.scalar("SELECT COUNT(*) FROM notice WHERE state = 'pending'") or 0)

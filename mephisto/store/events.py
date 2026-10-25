"""事件流：SSE 的游标来源。

只追加、只按 seq 递增读取。**不在这里做业务判断**——什么时候推、推给谁，是
`app/` 层的事；这里只保证"写进去的事件不会丢、读出来是按序的"。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .db import Database, from_ms, to_ms


@dataclass(frozen=True, slots=True)
class EventRow:
    seq: int
    kind: str
    payload: dict[str, Any]
    created_at: datetime
    session_id: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> EventRow:
        return cls(
            seq=row["seq"],
            kind=row["kind"],
            payload=json.loads(row["payload"]),
            created_at=from_ms(row["created_at"]),
            session_id=row["session_id"],
        )


class EventRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def append(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        at: datetime,
        session_id: str | None = None,
    ) -> int:
        cursor = self._db.execute(
            "INSERT INTO event_log (kind, session_id, payload, created_at) VALUES (?, ?, ?, ?)",
            (kind, session_id, json.dumps(payload, ensure_ascii=False), to_ms(at)),
        )
        return int(cursor.lastrowid or 0)

    def list_since(self, since_seq: int, *, limit: int = 500) -> list[EventRow]:
        rows = self._db.query_all(
            "SELECT seq, kind, session_id, payload, created_at FROM event_log"
            " WHERE seq > ? ORDER BY seq LIMIT ?",
            (since_seq, limit),
        )
        return [EventRow.from_row(row) for row in rows]

    def last_seq(self) -> int:
        return int(self._db.scalar("SELECT COALESCE(MAX(seq), 0) FROM event_log") or 0)

    def count(self) -> int:
        return int(self._db.scalar("SELECT COUNT(*) FROM event_log") or 0)

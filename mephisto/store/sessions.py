"""会话、别名、路由审计的读写。

会话是账本的根：消息外键指向它、提醒可回跳它、别名把外部标识映射到它。
所以这一层只做一件事——**保证"会话"这一行的语义不被写坏**（时间不倒退、别名不重复）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from ..domain import Route, Session
from ..ids import new_ulid
from .db import Database, from_ms, to_ms

_COLUMNS = "id, title, kind, state, created_at, last_active_at, seq"


def _to_session(row: sqlite3.Row) -> Session:
    return Session(
        id=row["id"],
        title=row["title"],
        kind=row["kind"],
        state=row["state"],
        created_at=from_ms(row["created_at"]),
        last_active_at=from_ms(row["last_active_at"]),
        seq=row["seq"],
    )


class SessionRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, session: Session) -> Session:
        self._db.execute(
            f"INSERT INTO session ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session.id,
                session.title,
                session.kind,
                session.state,
                to_ms(session.created_at),
                to_ms(session.last_active_at),
                session.seq,
            ),
        )
        return session

    def get(self, session_id: str) -> Session | None:
        row = self._db.query_one(f"SELECT {_COLUMNS} FROM session WHERE id = ?", (session_id,))
        return None if row is None else _to_session(row)

    def list(self, *, state: str | None = None, limit: int = 100) -> list[Session]:
        if state is None:
            rows = self._db.query_all(
                f"SELECT {_COLUMNS} FROM session ORDER BY last_active_at DESC LIMIT ?", (limit,)
            )
        else:
            rows = self._db.query_all(
                f"SELECT {_COLUMNS} FROM session WHERE state = ? ORDER BY last_active_at DESC LIMIT ?",
                (state, limit),
            )
        return [_to_session(row) for row in rows]

    def update_activity(self, session: Session) -> None:
        """只改活跃时间与游标。其它字段改动应该走明确的迁移，而不是悄悄 UPDATE。"""
        self._db.execute(
            "UPDATE session SET last_active_at = ?, seq = ? WHERE id = ?",
            (to_ms(session.last_active_at), session.seq, session.id),
        )

    def archive(self, session_id: str) -> bool:
        cursor = self._db.execute("UPDATE session SET state = 'archived' WHERE id = ?", (session_id,))
        return cursor.rowcount > 0

    def count(self) -> int:
        return int(self._db.scalar("SELECT COUNT(*) FROM session") or 0)

    # ---------- 别名 ----------
    def add_alias(self, kind: str, key: str, session_id: str, *, at: datetime | None = None) -> bool:
        """返回是否真的新增。重复别名是正常情况（同一封信被投递两次），不该报错。"""
        stamp = to_ms(at) if at is not None else to_ms(_now())
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO session_alias (kind, key, session_id, created_at) VALUES (?, ?, ?, ?)",
            (kind, key, session_id, stamp),
        )
        return cursor.rowcount > 0

    def resolve_alias(self, kind: str, key: str) -> str | None:
        row = self._db.query_one(
            "SELECT session_id FROM session_alias WHERE kind = ? AND key = ?", (kind, key)
        )
        return None if row is None else row["session_id"]

    def aliases(self, session_id: str) -> list[tuple[str, str]]:
        rows = self._db.query_all(
            "SELECT kind, key FROM session_alias WHERE session_id = ? ORDER BY created_at", (session_id,)
        )
        return [(row["kind"], row["key"]) for row in rows]

    # ---------- 路由审计 ----------
    def record_decision(self, envelope_id: str, route: Route, *, at: datetime | None = None) -> str:
        decision_id = new_ulid()
        stamp = to_ms(at) if at is not None else to_ms(_now())
        self._db.execute(
            "INSERT INTO route_decision (id, envelope_id, decided_by, session_id, confidence, evidence, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                decision_id,
                envelope_id,
                route.decided_by,
                route.session_id,
                route.confidence,
                route.evidence,
                stamp,
            ),
        )
        return decision_id

    def decisions(self, *, limit: int = 100) -> list[dict[str, object]]:
        rows = self._db.query_all(
            "SELECT id, envelope_id, decided_by, session_id, confidence, evidence, created_at"
            " FROM route_decision ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in rows]


def _now() -> datetime:
    from ..clock import SystemClock

    return SystemClock().now()

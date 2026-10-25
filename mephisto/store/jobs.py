"""任务队列：真值在表里，进程内队列只做唤醒。

抢占靠**一条 SQL**（`UPDATE ... RETURNING`），不用文件锁、不用 Redis：
`claim_next()` 在同一个语句里挑出"最老的、且该会话当前没有 running"的 job。
这样多 worker 并发也不会拿到同一会话的两个任务——DDL 里那条部分唯一索引是最后一道保险。

进程被杀时 running 的 job 会留在表里，`requeue_stale()` 在启动时把它们放回队列。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ..ids import new_ulid
from .db import Database, from_ms, to_ms

JOB_STATES = ("queued", "running", "done", "failed", "cancelled")
_TERMINAL = ("done", "failed", "cancelled")


@dataclass(frozen=True, slots=True)
class JobRow:
    id: str
    session_id: str
    state: str
    attempts: int
    created_at: datetime
    message_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> JobRow:
        return cls(
            id=row["id"],
            session_id=row["session_id"],
            state=row["state"],
            attempts=row["attempts"],
            created_at=from_ms(row["created_at"]),
            message_id=row["message_id"],
            started_at=from_ms(row["started_at"]) if row["started_at"] is not None else None,
            finished_at=from_ms(row["finished_at"]) if row["finished_at"] is not None else None,
            error=row["error"],
        )

    @property
    def terminal(self) -> bool:
        return self.state in _TERMINAL


_COLUMNS = "id, session_id, message_id, state, attempts, started_at, finished_at, error, created_at"


class JobRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def enqueue(self, session_id: str, message_id: str | None, *, at: datetime) -> JobRow:
        job_id = new_ulid()
        self._db.execute(
            "INSERT INTO job (id, session_id, message_id, state, attempts, created_at)"
            " VALUES (?, ?, ?, 'queued', 0, ?)",
            (job_id, session_id, message_id, to_ms(at)),
        )
        row = self.get(job_id)
        assert row is not None
        return row

    def claim_next(self, *, at: datetime) -> JobRow | None:
        """一条 SQL 完成抢占：挑最老的可跑任务，跳过已有 running 的会话。"""
        row = self._db.query_one(
            "UPDATE job SET state = 'running', started_at = ?, attempts = attempts + 1"
            " WHERE id = ("
            "   SELECT j.id FROM job j"
            "   WHERE j.state = 'queued'"
            "     AND NOT EXISTS ("
            "       SELECT 1 FROM job r WHERE r.session_id = j.session_id AND r.state = 'running'"
            "     )"
            "   ORDER BY j.created_at, j.id LIMIT 1"
            " )"
            f" RETURNING {_COLUMNS}",
            (to_ms(at),),
        )
        return None if row is None else JobRow.from_row(row)

    def finish(self, job_id: str, state: str, *, at: datetime, error: str | None = None) -> None:
        if state not in _TERMINAL:
            raise ValueError(f"finish 只接受终态，收到 {state!r}")
        self._db.execute(
            "UPDATE job SET state = ?, finished_at = ?, error = ? WHERE id = ?",
            (state, to_ms(at), error, job_id),
        )

    def get(self, job_id: str) -> JobRow | None:
        row = self._db.query_one(f"SELECT {_COLUMNS} FROM job WHERE id = ?", (job_id,))
        return None if row is None else JobRow.from_row(row)

    def cancel_queued(self, job_id: str, *, at: datetime) -> bool:
        """只取消还没开跑的 job。已在跑的必须杀进程，那条路由 Daemon 负责。"""
        cursor = self._db.execute(
            "UPDATE job SET state = 'cancelled', finished_at = ? WHERE id = ? AND state = 'queued'",
            (to_ms(at), job_id),
        )
        return cursor.rowcount > 0

    def counts(self) -> dict[str, int]:
        return dict(counts_of(self))

    def requeue_stale(self, *, older_than_seconds: int, at: datetime) -> int:
        """把卡住的 running 放回队列（启动恢复）。返回被重排的条数。"""
        threshold = to_ms(at) - older_than_seconds * 1000
        cursor = self._db.execute(
            "UPDATE job SET state = 'queued', started_at = NULL"
            " WHERE state = 'running' AND (started_at IS NULL OR started_at <= ?)",
            (threshold,),
        )
        return cursor.rowcount


class JobCounts(dict):
    """便于 `counts()['queued']` 直接取值，缺键返回 0。"""

    def __missing__(self, key: str) -> int:  # type: ignore[override]
        return 0


def counts_of(repo: JobRepo) -> JobCounts:
    counts = JobCounts({state: 0 for state in JOB_STATES})
    for row in repo._db.query_all("SELECT state, COUNT(*) AS n FROM job GROUP BY state"):
        counts[row["state"]] = int(row["n"])
    return counts

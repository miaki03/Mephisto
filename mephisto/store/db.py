"""SQLite 访问层：连接、PRAGMA、迁移、体检。

三条约定：
1. **一条连接 + RLock**。SQLite 只有一个写者，线程池里共享一条连接比连接池省心；
   写入一律走 `transaction()`，所以锁挂在 Database 上。
2. **时间列一律 epoch 毫秒**（不是秒）。与 ULID 的时间戳同精度，跨表比较不用换算。
3. **迁移只在启动时做一次**，每步一个事务；失败回滚并保留原 `user_version`。
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Sequence

MIGRATIONS_DIR = Path(__file__).with_name("migrations")

PRAGMAS: dict[str, Any] = {
    "journal_mode": "WAL",
    "foreign_keys": "ON",
    "busy_timeout": 5000,
    "synchronous": "NORMAL",
}


def to_ms(moment: datetime) -> int:
    if moment.tzinfo is None:
        raise ValueError("naive datetime 不接受，必须带时区")
    return int(moment.astimezone(UTC).timestamp() * 1000)


def from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), timeout=10.0, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        for key, value in PRAGMAS.items():
            self._conn.execute(f"PRAGMA {key}={value}")

    # ---------- 连接与事务 ----------
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    # ---------- 查询 ----------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.query_one(sql, params)
        return None if row is None else row[0]

    # ---------- 迁移 ----------
    def current_version(self) -> int:
        return int(self.scalar("PRAGMA user_version") or 0)

    @staticmethod
    def migrations() -> list[tuple[int, Path]]:
        """迁移文件名必须是 `NNN_描述.sql`；按编号升序。"""
        found: list[tuple[int, Path]] = []
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            prefix = path.name.split("_", 1)[0]
            if not prefix.isdigit():
                raise ValueError(f"迁移文件名缺少数字前缀：{path.name}")
            found.append((int(prefix), path))
        return found

    def migrate(self) -> tuple[int, int]:
        """返回 (迁移前版本, 迁移后版本)。重复调用是幂等的。"""
        start = self.current_version()
        version = start
        for target, path in self.migrations():
            if target <= version:
                continue
            script = path.read_text(encoding="utf-8")
            # 整段脚本 + 版本号一起提交：要么全成，要么全不成。
            body = f"BEGIN IMMEDIATE;\n{script}\nPRAGMA user_version = {target};\nCOMMIT;\n"
            try:
                with self._lock:
                    self._conn.executescript(body)
            except sqlite3.Error:
                with self._lock:
                    if self._conn.in_transaction:
                        self._conn.execute("ROLLBACK")
                raise
            version = target
        return start, version

    # ---------- 体检 ----------
    def check(self) -> dict[str, Any]:
        integrity = self.scalar("PRAGMA integrity_check")
        fk_problems = self.query_all("PRAGMA foreign_key_check")
        tables = [
            row[0]
            for row in self.query_all(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        counts = {name: int(self.scalar(f'SELECT COUNT(*) FROM "{name}"') or 0) for name in tables}
        return {
            "path": str(self.path),
            "user_version": self.current_version(),
            "journal_mode": self.scalar("PRAGMA journal_mode"),
            "integrity": integrity,
            "foreign_key_problems": len(fk_problems),
            "tables": counts,
        }

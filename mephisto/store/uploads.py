"""分片上传会话的元数据（文件分片本身在 `mephisto/blobs.py` 的 UploadArea）。

`received` 用 JSON 数组存已收到的分片序号——查询频次低、体积小，
比再开一张 `upload_chunk` 表更省事。幂等靠 `(upload_id, 序号)` 覆盖写。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ..ids import new_ulid
from .db import Database, from_ms, to_ms

UPLOAD_STATES = ("open", "done", "abandoned")
UPLOAD_TTL_SECONDS = 24 * 3600


@dataclass(frozen=True, slots=True)
class UploadRow:
    id: str
    filename: str
    size: int
    sha256: str
    chunk_size: int
    chunk_count: int
    received: tuple[int, ...]
    state: str
    expires_at: datetime
    created_at: datetime
    device_id: str | None = None
    attachment_id: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> UploadRow:
        return cls(
            id=row["id"],
            filename=row["filename"],
            size=row["size"],
            sha256=row["sha256"],
            chunk_size=row["chunk_size"],
            chunk_count=row["chunk_count"],
            received=tuple(json.loads(row["received"])),
            state=row["state"],
            expires_at=from_ms(row["expires_at"]),
            created_at=from_ms(row["created_at"]),
            device_id=row["device_id"],
            attachment_id=row["attachment_id"],
        )


class UploadRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(
        self,
        *,
        filename: str,
        size: int,
        sha256: str,
        chunk_size: int,
        chunk_count: int,
        at: datetime,
        device_id: str | None = None,
    ) -> UploadRow:
        upload_id = new_ulid()
        expires = at.timestamp() + UPLOAD_TTL_SECONDS
        self._db.execute(
            "INSERT INTO upload (id, device_id, filename, size, sha256, chunk_size, chunk_count,"
            " received, state, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', 'open', ?, ?)",
            (
                upload_id,
                device_id,
                filename,
                size,
                sha256,
                chunk_size,
                chunk_count,
                int(expires * 1000),
                to_ms(at),
            ),
        )
        row = self.get(upload_id)
        assert row is not None
        return row

    def get(self, upload_id: str) -> UploadRow | None:
        row = self._db.query_one(
            "SELECT id, device_id, filename, size, sha256, chunk_size, chunk_count, received,"
            " state, attachment_id, expires_at, created_at FROM upload WHERE id = ?",
            (upload_id,),
        )
        return None if row is None else UploadRow.from_row(row)

    def find_done(self, attachment_id: str) -> UploadRow | None:
        row = self._db.query_one(
            "SELECT id, device_id, filename, size, sha256, chunk_size, chunk_count, received,"
            " state, attachment_id, expires_at, created_at FROM upload"
            " WHERE attachment_id = ? AND state = 'done' ORDER BY created_at DESC LIMIT 1",
            (attachment_id,),
        )
        return None if row is None else UploadRow.from_row(row)

    def mark_chunk(self, upload_id: str, index: int) -> UploadRow:
        row = self.get(upload_id)
        if row is None:
            raise KeyError(upload_id)
        if row.state != "open":
            raise ValueError(f"上传已结束：{row.state}")
        if not 0 <= index < row.chunk_count:
            raise ValueError(f"分片序号越界：{index}（共 {row.chunk_count} 片）")
        received = sorted({*row.received, index})
        self._db.execute(
            "UPDATE upload SET received = ? WHERE id = ?",
            (json.dumps(received), upload_id),
        )
        updated = self.get(upload_id)
        assert updated is not None
        return updated

    def finish(self, upload_id: str, attachment_id: str) -> UploadRow:
        self._db.execute(
            "UPDATE upload SET state = 'done', attachment_id = ? WHERE id = ?",
            (attachment_id, upload_id),
        )
        row = self.get(upload_id)
        assert row is not None
        return row

    def abandon(self, upload_id: str) -> bool:
        cursor = self._db.execute(
            "UPDATE upload SET state = 'abandoned' WHERE id = ? AND state = 'open'", (upload_id,)
        )
        return cursor.rowcount > 0

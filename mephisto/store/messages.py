"""消息与附件的读写。

`message.seq` 是 `INTEGER PRIMARY KEY AUTOINCREMENT`——**单调游标由数据库给**，
不靠应用层算 `max(seq)+1`（那个写法在并发下会重号，而且要额外加锁）。

去重交给唯一索引：`(channel, external_id)`、`client_msg_id`、`message_id` 三条。
`INSERT OR IGNORE` 命中冲突时返回已存在的那一行，调用方据此判断"这条是重复投递"。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ..domain import Attachment
from ..ids import new_ulid
from .db import Database, from_ms, to_ms

_COLUMNS = (
    "seq, id, session_id, direction, channel, external_id, message_id, in_reply_to,"
    " refs, text, client_msg_id, route_decided_by, created_at"
)


@dataclass(frozen=True, slots=True)
class MessageRow:
    seq: int
    id: str
    session_id: str
    direction: str
    channel: str
    text: str
    created_at: datetime
    external_id: str | None = None
    message_id: str | None = None
    in_reply_to: str | None = None
    refs: tuple[str, ...] = ()
    client_msg_id: str | None = None
    route_decided_by: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> MessageRow:
        raw_refs = row["refs"]
        return cls(
            seq=row["seq"],
            id=row["id"],
            session_id=row["session_id"],
            direction=row["direction"],
            channel=row["channel"],
            text=row["text"],
            created_at=from_ms(row["created_at"]),
            external_id=row["external_id"],
            message_id=row["message_id"],
            in_reply_to=row["in_reply_to"],
            refs=tuple(json.loads(raw_refs)) if raw_refs else (),
            client_msg_id=row["client_msg_id"],
            route_decided_by=row["route_decided_by"],
        )


class MessageRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def append(
        self,
        *,
        session_id: str,
        direction: str,
        channel: str,
        text: str,
        at: datetime,
        external_id: str | None = None,
        message_id: str | None = None,
        in_reply_to: str | None = None,
        refs: tuple[str, ...] = (),
        client_msg_id: str | None = None,
        route_decided_by: str | None = None,
        message_pk: str | None = None,
    ) -> tuple[MessageRow, bool]:
        """返回 (行, 是否新增)。重复投递时 `inserted=False` 并返回既有行。"""
        ident = message_pk or new_ulid()
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO message"
            " (id, session_id, direction, channel, external_id, message_id, in_reply_to, refs, text,"
            "  client_msg_id, route_decided_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ident,
                session_id,
                direction,
                channel,
                external_id,
                message_id,
                in_reply_to,
                json.dumps(list(refs), ensure_ascii=False) if refs else None,
                text,
                client_msg_id,
                route_decided_by,
                to_ms(at),
            ),
        )
        if cursor.rowcount > 0:
            row = self._db.query_one(f"SELECT {_COLUMNS} FROM message WHERE seq = ?", (cursor.lastrowid,))
            assert row is not None
            return MessageRow.from_row(row), True

        existing = self._find_existing(channel, external_id, client_msg_id, message_id)
        if existing is None:
            raise RuntimeError("INSERT OR IGNORE 未插入且找不到冲突行，唯一索引与查询不一致")
        return existing, False

    def _find_existing(
        self,
        channel: str,
        external_id: str | None,
        client_msg_id: str | None,
        message_id: str | None,
    ) -> MessageRow | None:
        if client_msg_id is not None:
            row = self._db.query_one(
                f"SELECT {_COLUMNS} FROM message WHERE client_msg_id = ?", (client_msg_id,)
            )
            if row is not None:
                return MessageRow.from_row(row)
        if external_id is not None:
            row = self._db.query_one(
                f"SELECT {_COLUMNS} FROM message WHERE channel = ? AND external_id = ?",
                (channel, external_id),
            )
            if row is not None:
                return MessageRow.from_row(row)
        if message_id is not None:
            row = self._db.query_one(f"SELECT {_COLUMNS} FROM message WHERE message_id = ?", (message_id,))
            if row is not None:
                return MessageRow.from_row(row)
        return None

    def get(self, message_id: str) -> MessageRow | None:
        row = self._db.query_one(f"SELECT {_COLUMNS} FROM message WHERE id = ?", (message_id,))
        return None if row is None else MessageRow.from_row(row)

    def by_client_msg_id(self, client_msg_id: str) -> MessageRow | None:
        row = self._db.query_one(
            f"SELECT {_COLUMNS} FROM message WHERE client_msg_id = ?", (client_msg_id,)
        )
        return None if row is None else MessageRow.from_row(row)

    def list_by_session(
        self, session_id: str, *, since_seq: int = 0, limit: int = 100
    ) -> list[MessageRow]:
        rows = self._db.query_all(
            f"SELECT {_COLUMNS} FROM message WHERE session_id = ? AND seq > ? ORDER BY seq LIMIT ?",
            (session_id, since_seq, limit),
        )
        return [MessageRow.from_row(row) for row in rows]

    def list_since(self, since_seq: int, *, limit: int = 100) -> list[MessageRow]:
        rows = self._db.query_all(
            f"SELECT {_COLUMNS} FROM message WHERE seq > ? ORDER BY seq LIMIT ?", (since_seq, limit)
        )
        return [MessageRow.from_row(row) for row in rows]

    def count(self, session_id: str) -> int:
        return int(self._db.scalar("SELECT COUNT(*) FROM message WHERE session_id = ?", (session_id,)) or 0)

    def last_seq(self) -> int:
        return int(self._db.scalar("SELECT COALESCE(MAX(seq), 0) FROM message") or 0)


class AttachmentRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, message_id: str, idx: int, item: Attachment) -> None:
        self._db.execute(
            "INSERT INTO attachment (message_id, idx, filename, media_type, size, digest, path)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (message_id, idx, item.filename, item.media_type, item.size, item.digest, item.path),
        )

    def list_by_message(self, message_id: str) -> list[Attachment]:
        rows = self._db.query_all(
            "SELECT filename, media_type, size, digest, path FROM attachment"
            " WHERE message_id = ? ORDER BY idx",
            (message_id,),
        )
        return [
            Attachment(
                filename=row["filename"],
                size=row["size"],
                digest=row["digest"],
                media_type=row["media_type"],
                path=row["path"],
            )
            for row in rows
        ]

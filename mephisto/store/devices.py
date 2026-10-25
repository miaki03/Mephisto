"""设备与令牌。

令牌只存 `sha256`，明文只在创建时出现一次——所以 `add()` 返回明文，之后再也拿不到。
吊销是软删（`revoked_at`），这样"谁在什么时候被踢掉"有据可查。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..ids import hash_token, new_token, new_ulid
from .db import Database, from_ms, to_ms

DEVICE_KINDS = ("android", "desktop", "mail", "system")


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    id: str
    name: str
    kind: str
    created_at: datetime
    revoked_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


class DeviceRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, name: str, *, at: datetime, kind: str = "android") -> tuple[DeviceInfo, str]:
        """返回 (设备, 明文令牌)。令牌只在这里出现一次。"""
        if kind not in DEVICE_KINDS:
            raise ValueError(f"kind 非法：{kind!r}")
        device_id = new_ulid()
        token = new_token()
        stamp = to_ms(at)
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO device (id, name, kind, created_at) VALUES (?, ?, ?, ?)",
                (device_id, name, kind, stamp),
            )
            conn.execute(
                "INSERT INTO device_token (token_hash, device_id, created_at) VALUES (?, ?, ?)",
                (hash_token(token), device_id, stamp),
            )
        return DeviceInfo(id=device_id, name=name, kind=kind, created_at=at), token

    def authenticate(self, token: str, *, at: datetime) -> DeviceInfo | None:
        row = self._db.query_one(
            "SELECT d.id, d.name, d.kind, d.created_at, d.revoked_at, t.expires_at"
            " FROM device_token t JOIN device d ON d.id = t.device_id"
            " WHERE t.token_hash = ?",
            (hash_token(token),),
        )
        if row is None or row["revoked_at"] is not None:
            return None
        if row["expires_at"] is not None and int(row["expires_at"]) <= to_ms(at):
            return None
        self.touch(row["id"], at=at)
        return DeviceInfo(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            created_at=from_ms(row["created_at"]),
            revoked_at=from_ms(row["revoked_at"]) if row["revoked_at"] is not None else None,
        )

    def touch(self, device_id: str, *, at: datetime) -> None:
        self._db.execute(
            "UPDATE device_token SET last_seen = ? WHERE device_id = ?", (to_ms(at), device_id)
        )

    def revoke(self, device_id: str, *, at: datetime) -> bool:
        cursor = self._db.execute(
            "UPDATE device SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (to_ms(at), device_id),
        )
        return cursor.rowcount > 0

    def get(self, device_id: str) -> DeviceInfo | None:
        row = self._db.query_one(
            "SELECT id, name, kind, created_at, revoked_at FROM device WHERE id = ?", (device_id,)
        )
        if row is None:
            return None
        return DeviceInfo(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            created_at=from_ms(row["created_at"]),
            revoked_at=from_ms(row["revoked_at"]) if row["revoked_at"] is not None else None,
        )

    def list(self) -> list[DeviceInfo]:
        rows = self._db.query_all(
            "SELECT id, name, kind, created_at, revoked_at FROM device ORDER BY created_at"
        )
        return [
            DeviceInfo(
                id=row["id"],
                name=row["name"],
                kind=row["kind"],
                created_at=from_ms(row["created_at"]),
                revoked_at=from_ms(row["revoked_at"]) if row["revoked_at"] is not None else None,
            )
            for row in rows
        ]

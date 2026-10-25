"""`Reminder`：提醒规则（会被展开成多条 `notice`）。

规则与投递分开的好处：改一次时间不用重建历史；同一条规则展开出的每条 notice
用 `origin = reminder:<id>@<epoch_ms>` 做幂等，数据库唯一索引兜住重复。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import codec

OWNERS = ("user", "system")
REMINDER_STATES = ("scheduled", "fired", "cancelled", "missed")
PRIORITIES = ("high", "normal")


@dataclass(frozen=True, slots=True)
class Reminder:
    id: str
    title: str
    due_at_utc: datetime
    tz: str
    created_at: datetime
    owner: str = "user"
    body: str | None = None
    rrule: str | None = None
    origin_session: str | None = None
    channels: tuple[str, ...] = ("alarm",)
    priority: str = "normal"
    state: str = "scheduled"
    fire_count: int = 0

    def __post_init__(self) -> None:
        if self.owner not in OWNERS:
            raise ValueError(f"owner 非法：{self.owner!r}")
        if self.state not in REMINDER_STATES:
            raise ValueError(f"state 非法：{self.state!r}")
        if self.priority not in PRIORITIES:
            raise ValueError(f"priority 非法：{self.priority!r}")
        if self.due_at_utc.tzinfo is None or self.created_at.tzinfo is None:
            raise ValueError("时间必须带时区")
        if not 1 <= len(self.title) <= 120:
            raise ValueError(f"title 长度应在 1..120，实际 {len(self.title)}")
        if not self.tz:
            raise ValueError("tz 必填")
        if not self.channels:
            raise ValueError("channels 至少一个")

    @property
    def origin_prefix(self) -> str:
        return f"reminder:{self.id}@"

    def origin_for(self, occurrence: datetime) -> str:
        return self.origin_prefix + str(codec.to_iso(occurrence))

    def fired_once(self) -> Reminder:
        return Reminder(**{**_as_dict(self), "state": "fired", "fire_count": self.fire_count + 1})

    def cancelled(self) -> Reminder:
        return Reminder(**{**_as_dict(self), "state": "cancelled"})

    def to_dict(self) -> dict[str, Any]:
        data = _as_dict(self)
        data["due_at_utc"] = codec.to_iso(self.due_at_utc)
        data["created_at"] = codec.to_iso(self.created_at)
        data["channels"] = list(self.channels)
        return data

    @classmethod
    def from_dict(cls, value: Any, where: str = "reminder") -> Reminder:
        data = codec.expect_mapping(value, where)
        channels = codec.as_list(codec.get(data, "channels", where, default=["alarm"]), f"{where}.channels", min_items=1)
        return cls(
            id=codec.as_ulid(codec.get(data, "id", where, required=True), f"{where}.id"),
            title=codec.as_str(codec.get(data, "title", where, required=True), f"{where}.title", min_len=1, max_len=120),
            due_at_utc=codec.as_utc(codec.get(data, "due_at_utc", where, required=True), f"{where}.due_at_utc"),
            tz=codec.as_str(codec.get(data, "tz", where, required=True), f"{where}.tz"),
            created_at=codec.as_utc(codec.get(data, "created_at", where, required=True), f"{where}.created_at"),
            owner=codec.as_enum(codec.get(data, "owner", where, default="user"), OWNERS, f"{where}.owner"),
            body=data.get("body"),
            rrule=data.get("rrule"),
            origin_session=data.get("origin_session"),
            channels=tuple(str(item) for item in channels),
            priority=codec.as_enum(codec.get(data, "priority", where, default="normal"), PRIORITIES, f"{where}.priority"),
            state=codec.as_enum(
                codec.get(data, "state", where, default="scheduled"), REMINDER_STATES, f"{where}.state"
            ),
            fire_count=codec.as_int(codec.get(data, "fire_count", where, default=0), f"{where}.fire_count", minimum=0),
        )


def _as_dict(reminder: Reminder) -> dict[str, Any]:
    return {
        "id": reminder.id,
        "title": reminder.title,
        "due_at_utc": reminder.due_at_utc,
        "tz": reminder.tz,
        "created_at": reminder.created_at,
        "owner": reminder.owner,
        "body": reminder.body,
        "rrule": reminder.rrule,
        "origin_session": reminder.origin_session,
        "channels": reminder.channels,
        "priority": reminder.priority,
        "state": reminder.state,
        "fire_count": reminder.fire_count,
    }


__all__ = ["Reminder", "OWNERS", "PRIORITIES", "REMINDER_STATES", "field"]

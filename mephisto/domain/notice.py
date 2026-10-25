"""`Notice`：唯一的投递单元（提醒、任务完成、新回复、系统消息共用一张表）。

为什么把提醒与"任务完成"合成一个模型：**能把东西送到你面前的路只有一条**，
那就只该有一个状态机。提醒只是"提前知道的时刻"，任务完成是"刚发生的时刻"。

幂等本体是 `origin`（如 `reminder:<id>@<occur_utc>`），由数据库唯一索引兜住。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from . import codec

NOTICE_KINDS = ("reminder", "job", "reply", "system")
NOTICE_STATES = ("pending", "acked", "dismissed", "missed")

# 终态集合：一旦进入就不再变化，避免"已确认的提醒又冒出来"。
TERMINAL_STATES = ("acked", "dismissed", "missed")
_ALLOWED = {
    "pending": ("acked", "dismissed", "missed"),
    "acked": (),
    "dismissed": (),
    "missed": (),
}


@dataclass(frozen=True, slots=True)
class Notice:
    id: str
    kind: str
    title: str
    due_utc: datetime
    created_at: datetime
    body: str | None = None
    state: str = "pending"
    session_id: str | None = None
    origin: str | None = None
    client_ref: str | None = None
    acked_at: datetime | None = None
    snooze_until: datetime | None = None

    def __post_init__(self) -> None:
        if self.kind not in NOTICE_KINDS:
            raise ValueError(f"kind 非法：{self.kind!r}")
        if self.state not in NOTICE_STATES:
            raise ValueError(f"state 非法：{self.state!r}")
        for name in ("due_utc", "created_at"):
            if getattr(self, name).tzinfo is None:
                raise ValueError(f"{name} 必须带时区")
        if not 1 <= len(self.title) <= 120:
            raise ValueError(f"title 长度应在 1..120，实际 {len(self.title)}")
        if self.body is not None and len(self.body) > 4000:
            raise ValueError("body 超过 4000 字")
        if self.state in TERMINAL_STATES and self.acked_at is None:
            raise ValueError(f"state={self.state} 时必须给出 acked_at，否则无法复盘")
        if self.state == "pending" and self.acked_at is not None:
            raise ValueError("state=pending 时 acked_at 应为空")
        if self.snooze_until is not None and self.snooze_until <= self.due_utc:
            raise ValueError("snooze_until 必须晚于 due_utc")

    # ---------- 状态机 ----------
    def transition(self, state: str, *, at: datetime) -> Notice:
        """按允许的跃迁改状态。非法跃迁抛 ValueError —— 这是本模块最重要的不变量。"""
        if state not in _ALLOWED[self.state]:
            raise ValueError(f"非法状态跃迁：{self.state} → {state}")
        if at.tzinfo is None:
            raise ValueError("时间必须带时区")
        return replace(self, state=state, acked_at=at)

    def ack(self, at: datetime) -> Notice:
        return self.transition("acked", at=at)

    def dismiss(self, at: datetime) -> Notice:
        return self.transition("dismissed", at=at)

    def mark_missed(self, at: datetime) -> Notice:
        return self.transition("missed", at=at)

    def snooze(self, until: datetime) -> Notice:
        """只能推迟 pending 的提醒；终态不允许复活。"""
        if self.state != "pending":
            raise ValueError(f"只有 pending 可以推迟，当前 {self.state}")
        if until.tzinfo is None:
            raise ValueError("时间必须带时区")
        if until <= self.due_utc:
            raise ValueError("推迟时间必须晚于原定时间")
        return replace(self, snooze_until=until)

    # ---------- 查询 ----------
    def is_due(self, now: datetime) -> bool:
        moment = self.snooze_until or self.due_utc
        return self.state == "pending" and moment <= now

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    # ---------- 编解码 ----------
    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "due_utc": codec.to_iso(self.due_utc),
            "created_at": codec.to_iso(self.created_at),
            "state": self.state,
        }
        for key in ("body", "session_id", "origin", "client_ref"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        if self.acked_at is not None:
            data["acked_at"] = codec.to_iso(self.acked_at)
        if self.snooze_until is not None:
            data["snooze_until"] = codec.to_iso(self.snooze_until)
        return data

    @classmethod
    def from_dict(cls, value: Any, where: str = "notice") -> Notice:
        data = codec.expect_mapping(value, where)
        codec.expect_keys(
            data,
            ("id", "kind", "title", "due_utc", "created_at", "body", "state",
             "session_id", "origin", "client_ref", "acked_at", "snooze_until", "meta"),
            where,
        )
        return cls(
            id=codec.as_ulid(codec.get(data, "id", where, required=True), f"{where}.id"),
            kind=codec.as_enum(codec.get(data, "kind", where, required=True), NOTICE_KINDS, f"{where}.kind"),
            title=codec.as_str(codec.get(data, "title", where, required=True), f"{where}.title", max_len=120, min_len=1),
            due_utc=codec.as_utc(codec.get(data, "due_utc", where, required=True), f"{where}.due_utc"),
            created_at=codec.as_utc(codec.get(data, "created_at", where, required=True), f"{where}.created_at"),
            body=data.get("body"),
            state=codec.as_enum(codec.get(data, "state", where, default="pending"), NOTICE_STATES, f"{where}.state"),
            session_id=data.get("session_id"),
            origin=data.get("origin"),
            client_ref=data.get("client_ref"),
            acked_at=(
                codec.as_utc(data["acked_at"], f"{where}.acked_at") if data.get("acked_at") is not None else None
            ),
            snooze_until=(
                codec.as_utc(data["snooze_until"], f"{where}.snooze_until")
                if data.get("snooze_until") is not None
                else None
            ),
        )

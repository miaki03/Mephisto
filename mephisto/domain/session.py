"""`Session`：我们自己的会话账本。

关键一句：**DSH 的会话是执行作用域，不是账本。**
模型的对话历史由 `message` 表 + 四段装配决定，`session` 只记"这条会话是什么、活没活"。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from . import codec

SESSION_KINDS = ("chatter", "task", "digest", "system")
SESSION_STATES = ("active", "archived")


@dataclass(frozen=True, slots=True)
class Session:
    id: str
    title: str
    created_at: datetime
    last_active_at: datetime
    kind: str = "chatter"
    state: str = "active"
    seq: int = 0          # 增量同步用的单调游标
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in SESSION_KINDS:
            raise ValueError(f"kind 非法：{self.kind!r}")
        if self.state not in SESSION_STATES:
            raise ValueError(f"state 非法：{self.state!r}")
        if self.created_at.tzinfo is None or self.last_active_at.tzinfo is None:
            raise ValueError("时间必须带时区")
        if self.last_active_at < self.created_at:
            raise ValueError("last_active_at 不能早于 created_at")
        if not 1 <= len(self.title) <= 120:
            raise ValueError(f"title 长度应在 1..120，实际 {len(self.title)}")
        if self.seq < 0:
            raise ValueError("seq 不能为负")

    def touch(self, at: datetime, *, seq: int | None = None) -> Session:
        """更新活跃时间（以及可选的游标）。返回新对象——frozen 的好处是不会被就地改。"""
        if at.tzinfo is None:
            raise ValueError("时间必须带时区")
        if at < self.last_active_at:
            raise ValueError("活跃时间不能倒退（时钟回拨请修时钟，不要污染账本）")
        return replace(self, last_active_at=at, seq=self.seq if seq is None else seq)

    def archive(self) -> Session:
        return replace(self, state="archived")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "state": self.state,
            "created_at": codec.to_iso(self.created_at),
            "last_active_at": codec.to_iso(self.last_active_at),
            "seq": self.seq,
        }
        if self.meta:
            data["meta"] = dict(self.meta)
        return data

    @classmethod
    def from_dict(cls, value: Any, where: str = "session") -> Session:
        data = codec.expect_mapping(value, where)
        codec.expect_keys(
            data,
            ("id", "title", "kind", "state", "created_at", "last_active_at", "seq", "meta"),
            where,
        )
        meta = data.get("meta") or {}
        if not isinstance(meta, dict):
            raise codec.DecodeError(f"{where}.meta 应为对象")
        return cls(
            id=codec.as_ulid(codec.get(data, "id", where, required=True), f"{where}.id"),
            title=codec.as_str(codec.get(data, "title", where, required=True), f"{where}.title", max_len=120, min_len=1),
            created_at=codec.as_utc(codec.get(data, "created_at", where, required=True), f"{where}.created_at"),
            last_active_at=codec.as_utc(
                codec.get(data, "last_active_at", where, required=True), f"{where}.last_active_at"
            ),
            kind=codec.as_enum(codec.get(data, "kind", where, default="chatter"), SESSION_KINDS, f"{where}.kind"),
            state=codec.as_enum(codec.get(data, "state", where, default="active"), SESSION_STATES, f"{where}.state"),
            seq=codec.as_int(codec.get(data, "seq", where, default=0), f"{where}.seq", minimum=0),
            meta=dict(meta),
        )

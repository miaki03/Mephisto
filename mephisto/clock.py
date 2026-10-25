"""可注入的时钟。

为什么不是直接 `datetime.now()`：调度器要能"时间旅行"。
关机 3 小时后开机、时钟回拨、跨时区，这些都要在单测里复现，
所以业务代码一律通过 `Clock` 取时间。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
    def now_ms(self) -> int: ...


def to_utc(dt: datetime) -> datetime:
    """只接受带时区的时间。naive datetime 一律报错，避免"看起来对但其实错 8 小时"。"""
    if dt.tzinfo is None:
        raise ValueError("naive datetime 不接受，必须带时区")
    return dt.astimezone(UTC)


def from_epoch_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def to_epoch_ms(dt: datetime) -> int:
    return int(to_utc(dt).timestamp() * 1000)


@dataclass(frozen=True)
class SystemClock:
    def now(self) -> datetime:
        return datetime.now(tz=UTC)

    def now_ms(self) -> int:
        return to_epoch_ms(self.now())


@dataclass
class FakeClock:
    """测试用时钟。`advance` 模拟时间流逝，`set` 模拟时钟被改。"""

    current: datetime = field(default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC))

    def now(self) -> datetime:
        return self.current

    def now_ms(self) -> int:
        return to_epoch_ms(self.current)

    def advance(
        self,
        *,
        seconds: float = 0,
        minutes: float = 0,
        hours: float = 0,
        days: float = 0,
    ) -> datetime:
        self.current = self.current + timedelta(
            seconds=seconds, minutes=minutes, hours=hours, days=days
        )
        return self.current

    def set(self, when: datetime) -> datetime:
        self.current = to_utc(when)
        return self.current

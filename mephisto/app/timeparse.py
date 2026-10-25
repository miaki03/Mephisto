"""把"三点""3 分钟后""明天 15:00"解析成**带时区的时刻**。

只支持一小撮写法，看不懂就报错——**猜时间是危险的**：猜错一次，提醒就在错误的日子响。
所以这里的策略是「宁可拒绝，不要猜」。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "Asia/Shanghai"

_RELATIVE = re.compile(
    r"^\s*(?:in\s+)?(\d+)\s*(分钟|分|小时|钟头|天|minute|minutes|min|hour|hours|h|day|days|d)\s*(?:后|之后|以后)?\s*$",
    re.IGNORECASE,
)
_CLOCK = re.compile(r"^\s*(今天|明天|后天)?\s*(上午|下午|中午|晚上)?\s*(\d{1,2})\s*[:：]\s*(\d{2})\s*$")
_HH_ONLY = re.compile(r"^\s*(今天|明天|后天)?\s*(上午|下午|中午|晚上)?\s*(\d{1,2})\s*点\s*(半|\d{1,2})?\s*$")

_UNIT_SECONDS = {
    "分钟": 60, "分": 60, "minute": 60, "minutes": 60, "min": 60,
    "小时": 3600, "钟头": 3600, "hour": 3600, "hours": 3600, "h": 3600,
    "天": 86400, "day": 86400, "days": 86400, "d": 86400,
}
_DAY_OFFSET = {"今天": 0, "明天": 1, "后天": 2}
_PERIOD = {"上午": 0, "中午": 0, "下午": 12, "晚上": 12}


class TimeParseError(ValueError):
    pass


def zone_of(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except ZoneInfoNotFoundError as exc:
        raise TimeParseError(f"未知时区：{tz}") from exc


def parse_when(text: str, *, now: datetime, tz: str = DEFAULT_TZ) -> datetime:
    """返回 UTC 感知时刻。`now` 必须是带时区的时间。"""
    if now.tzinfo is None:
        raise ValueError("now 必须带时区")
    zone = zone_of(tz)
    local_now = now.astimezone(zone)
    raw = text.strip()
    if not raw:
        raise TimeParseError("时间是空的")

    relative = _RELATIVE.match(raw)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2).lower()
        seconds = _UNIT_SECONDS.get(unit)
        if seconds is None:
            raise TimeParseError(f"看不懂单位：{unit}")
        return now + timedelta(seconds=amount * seconds)

    clock = _CLOCK.match(raw) or _HH_ONLY.match(raw)
    if clock:
        day_word, period, hour_text, minute_text = clock.groups()
        hour = int(hour_text)
        minute = _minute_of(minute_text)
        if period:
            if hour > 12:
                raise TimeParseError(f"带{period}的小时不该超过 12：{hour}")
            hour = hour % 12 + _PERIOD[period]
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise TimeParseError(f"时间越界：{hour}:{minute}")
        days = _DAY_OFFSET.get(day_word or "今天", 0)
        target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=days)
        if day_word is None and target <= local_now:
            target += timedelta(days=1)  # 没说日期、又已经过了 → 明天
        return target.astimezone(now.tzinfo)

    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise TimeParseError(f"看不懂的时间写法：{text!r}（支持：3分钟后 / 明天15:00 / 2026-10-27T15:00+08:00）") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(now.tzinfo)


def _minute_of(text: str | None) -> int:
    if not text:
        return 0
    if text == "半":
        return 30
    return int(text)

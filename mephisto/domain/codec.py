"""编解码工具：严格、无 IO、可单独测。

三条原则：
1. **对未知键报错**。契约里全是 `additionalProperties: false`，
   静默丢字段的后果是三个月后才发现数据对不上。
2. **错误信息带路径**（`envelope.thread.references[2]`），否则排查要靠猜。
3. 时间一律转成**带 UTC 时区的 datetime**；naive 输入直接拒绝。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable, Mapping, Sequence

from ..ids import is_ulid as _is_ulid


class DecodeError(ValueError):
    """字段级解码失败，消息里带路径。"""


def _path(where: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{where}[{key}]"
    return f"{where}.{key}" if where else key


def expect_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DecodeError(f"{where or '<root>'} 应为对象，实际是 {type(value).__name__}")
    return value


def expect_keys(data: Mapping[str, Any], allowed: Iterable[str], where: str) -> None:
    extra = set(data) - set(allowed)
    if extra:
        raise DecodeError(f"{where or '<root>'} 出现未知字段：{', '.join(sorted(extra))}")


def get(data: Mapping[str, Any], key: str, where: str, *, required: bool = False, default: Any = None) -> Any:
    if key in data:
        return data[key]
    if required:
        raise DecodeError(f"{_path(where, key)} 缺失（必填）")
    return default


def as_str(value: Any, where: str, *, max_len: int | None = None, min_len: int = 0) -> str:
    if not isinstance(value, str):
        raise DecodeError(f"{where} 应为字符串，实际是 {type(value).__name__}")
    if len(value) < min_len:
        raise DecodeError(f"{where} 长度 {len(value)} < {min_len}")
    if max_len is not None and len(value) > max_len:
        raise DecodeError(f"{where} 长度 {len(value)} > {max_len}")
    return value


def as_int(value: Any, where: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DecodeError(f"{where} 应为整数，实际是 {type(value).__name__}")
    if minimum is not None and value < minimum:
        raise DecodeError(f"{where} = {value} < {minimum}")
    if maximum is not None and value > maximum:
        raise DecodeError(f"{where} = {value} > {maximum}")
    return value


def as_bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise DecodeError(f"{where} 应为布尔，实际是 {type(value).__name__}")
    return value


def as_list(value: Any, where: str, *, min_items: int = 0, max_items: int | None = None) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise DecodeError(f"{where} 应为数组，实际是 {type(value).__name__}")
    items = list(value)
    if len(items) < min_items:
        raise DecodeError(f"{where} 元素数 {len(items)} < {min_items}")
    if max_items is not None and len(items) > max_items:
        raise DecodeError(f"{where} 元素数 {len(items)} > {max_items}")
    return items


def as_utc(value: Any, where: str) -> datetime:
    """解析 ISO8601。必须带偏移；`Z` 与 `+08:00` 都接受，naive 拒绝。"""
    text = as_str(value, where)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DecodeError(f"{where} 不是合法 ISO8601：{text!r}（{exc}）") from exc
    if parsed.tzinfo is None:
        raise DecodeError(f"{where} 缺少时区偏移：{text!r}")
    return parsed.astimezone(UTC)


def to_iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("naive datetime 不接受，必须带时区")
    return moment.astimezone(UTC).isoformat()


def as_ulid(value: Any, where: str) -> str:
    text = as_str(value, where)
    if not _is_ulid(text):
        raise DecodeError(f"{where} 不是 ULID：{text!r}")
    return text


def as_enum(value: Any, allowed: Sequence[str], where: str) -> str:
    text = as_str(value, where)
    if text not in allowed:
        raise DecodeError(f"{where} = {text!r} 不在允许集合 {tuple(allowed)}")
    return text

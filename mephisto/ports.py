"""端口定义：三个副作用边界之一（`Source` / `Sink` / `Runner`）。

用 `Protocol` 而不是基类继承：适配器只负责"翻译"，不该被迫继承任何行为。
测试里可以塞假实现，不必启动真进程。

Runner 的契约只有一条硬要求：**失败必须通过返回值表达，不许静默返回空字符串**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class Task:
    """一次执行请求。`prompt` 是完整文本——上下文装配在调用方完成。"""

    prompt: str
    session_id: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunEvent:
    """执行过程中的一条事件。`kind` 直接来自 dsh 的 `type` 字段。"""

    kind: str
    payload: Mapping[str, Any]
    at: datetime | None = None

    @property
    def phase(self) -> str | None:
        value = self.payload.get("phase")
        return value if isinstance(value, str) else None


@dataclass(frozen=True, slots=True)
class RunResult:
    ok: bool
    text: str
    exit_code: int
    events: Sequence[RunEvent] = field(default_factory=tuple)
    error: str | None = None
    dsh_session_id: str | None = None
    usage: Mapping[str, int] = field(default_factory=dict)
    duration_ms: int = 0

    @property
    def cache_read_tokens(self) -> int:
        return int(self.usage.get("cacheReadTokens", 0))


EventHook = Callable[[RunEvent], None]


class Runner(Protocol):
    def run(self, task: Task, *, on_event: EventHook | None = None) -> RunResult: ...

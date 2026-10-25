"""极薄的路由：`method + 正则 → handler`。

不用框架的理由：7 条路径、16 个端点，标准库足够；框架会把"依赖注入、中间件、
生命周期"这些我们不需要的概念引进来。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


@dataclass(frozen=True, slots=True)
class Request:
    method: str
    path: str
    params: Mapping[str, str]
    query: Mapping[str, str]
    headers: Mapping[str, str]
    body: Any = None
    device_id: str | None = None


@dataclass(frozen=True, slots=True)
class Response:
    status: int = 200
    body: Any = None
    headers: Mapping[str, str] = field(default_factory=dict)


class HttpError(Exception):
    """业务层抛它，server 统一转成 JSON 错误体。"""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class StreamResponse:
    """SSE 的返回值。不用哨兵 + 打补丁的方式，因为 Request 是 frozen 的。"""

    since_seq: int = 0


# 兼容旧写法：handler 只返回哨兵时按 since_seq=0 处理
STREAM = StreamResponse()


Handler = Callable[[Request], Response | object]


class Router:
    def __init__(self) -> None:
        self._routes: list[tuple[str, re.Pattern[str], Handler]] = []

    def add(self, method: str, pattern: str, handler: Handler) -> None:
        self._routes.append((method.upper(), re.compile(pattern), handler))

    def match(self, method: str, path: str) -> tuple[Handler, dict[str, str]] | None:
        for route_method, pattern, handler in self._routes:
            if route_method != method.upper():
                continue
            found = pattern.match(path)
            if found:
                return handler, {k: v for k, v in found.groupdict().items() if v is not None}
        return None

    def methods_for(self, path: str) -> list[str]:
        return [method for method, pattern, _ in self._routes if pattern.match(path)]

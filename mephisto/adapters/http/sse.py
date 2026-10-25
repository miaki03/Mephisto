"""SSE：服务端 → 客户端的事件流。

三条实现约束（对应 docs/15 §10 与 §7.3）：
1. **每 15 秒一个 `: ping` 注释行**——Cloudflare 的 Proxy Idle Timeout 是 900 秒，
   但中间设备经常更短；心跳同时是客户端判断"活着还是断了"的依据。
2. **每条事件带 `id:`**，客户端重连时用 `Last-Event-ID` 从该位置续传。
3. 不用唤醒机制，直接轮询 DB（500ms）。HTTP 层的复杂度换来了零跨线程状态；
   个人用量下这点延迟看不出来。
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Protocol


class EventSource(Protocol):
    def list_since(self, since_seq: int, *, limit: int = 500) -> list[Any]: ...


def format_event(*, seq: int, kind: str, payload: Any) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {seq}\nevent: {kind}\ndata: {data}\n\n".encode("utf-8")


def format_keepalive() -> bytes:
    return b": ping\n\n"


def stream_events(
    source: EventSource,
    write: Callable[[bytes], None],
    flush: Callable[[], None],
    *,
    since_seq: int = 0,
    poll_seconds: float = 0.5,
    keepalive_seconds: float = 15.0,
    max_seconds: float = 3600.0,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    """把 event_log 里 `seq > since_seq` 的事件推给客户端。返回推送条数。

    客户端断开时 `write` 会抛 OSError，这里吞掉并结束——那是正常退出路径，
    不是错误。
    """
    sent = 0
    last_keepalive = time.monotonic()
    deadline = last_keepalive + max_seconds
    try:
        while time.monotonic() < deadline:
            if should_stop is not None and should_stop():
                break
            events = source.list_since(since_seq, limit=200)
            for event in events:
                write(format_event(seq=event.seq, kind=event.kind, payload=event.payload))
                since_seq = event.seq
                sent += 1
            if events:
                flush()
                last_keepalive = time.monotonic()
                continue
            if time.monotonic() - last_keepalive >= keepalive_seconds:
                write(format_keepalive())
                flush()
                last_keepalive = time.monotonic()
            time.sleep(poll_seconds)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    return sent

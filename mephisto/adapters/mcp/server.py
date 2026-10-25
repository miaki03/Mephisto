"""MCP（stdio）：把 Mephisto 的能力暴露给 dsh 里的模型。

协议最小集：`initialize` / `tools/list` / `tools/call` / `ping`。
一行一条 JSON-RPC 2.0 消息；**stdout 只写协议**，日志一律去 stderr——
否则 dsh 那边的 MCP 客户端会被我们的日志呛死。

工具命名由 dsh 加壳成 `mcp__<serverName>__<tool>`，所以这里的名字保持短动词。
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, IO

from ... import __version__
from ...app.reminders import ReminderService
from ...app.timeparse import TimeParseError

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "mephisto"


class ToolError(Exception):
    """工具执行失败：回 `isError: true`，让模型看到原因，而不是假装成功。"""


_TOOLS: list[dict[str, Any]] = [
    {
        "name": "remind_add",
        "description": "在指定时刻提醒用户。只在用户明确要求『几点提醒我』时使用；不要用它表达你自己的想法。",
        "inputSchema": {
            "type": "object",
            "required": ["title", "at_local"],
            "properties": {
                "title": {"type": "string", "maxLength": 120, "description": "提醒标题，简短"},
                "at_local": {
                    "type": "string",
                    "description": "本地时刻，支持『3分钟后』『明天15:00』『2026-10-27T15:00+08:00』",
                },
                "tz": {"type": "string", "default": "Asia/Shanghai"},
                "body": {"type": "string", "maxLength": 4000},
                "session_id": {"type": "string", "description": "来源会话，用于回跳"},
            },
        },
    },
    {
        "name": "remind_list",
        "description": "列出接下来一段时间内的提醒。",
        "inputSchema": {
            "type": "object",
            "properties": {"horizon_days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 7}},
        },
    },
    {
        "name": "remind_cancel",
        "description": "取消一条尚未触发的提醒。",
        "inputSchema": {
            "type": "object",
            "required": ["reminder_id"],
            "properties": {"reminder_id": {"type": "string"}},
        },
    },
    {
        "name": "remind_reschedule",
        "description": "改一条提醒的时间。",
        "inputSchema": {
            "type": "object",
            "required": ["reminder_id", "at_local"],
            "properties": {
                "reminder_id": {"type": "string"},
                "at_local": {"type": "string"},
                "tz": {"type": "string"},
            },
        },
    },
    {
        "name": "memory_append",
        "description": "往长期记忆追加一条（只追加，不修改历史）。用于记下用户明确要求记住的事。",
        "inputSchema": {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string", "maxLength": 2000},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
]


class McpServer:
    def __init__(self, reminders: ReminderService, *, memory_path: Path, version: str = __version__) -> None:
        self.reminders = reminders
        self.memory_path = Path(memory_path)
        self.version = version
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "remind_add": self._remind_add,
            "remind_list": self._remind_list,
            "remind_cancel": self._remind_cancel,
            "remind_reschedule": self._remind_reschedule,
            "memory_append": self._memory_append,
        }

    # ---------- JSON-RPC ----------
    def handle(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict) or "method" not in message:
            return _error(None, -32600, "不是合法的 JSON-RPC 请求")
        request_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}

        if request_id is None and method != "ping":
            return None  # 通知：不回

        try:
            if method == "initialize":
                result: dict[str, Any] = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": self.version},
                }
            elif method == "tools/list":
                result = {"tools": _TOOLS}
            elif method == "tools/call":
                result = self._call_tool(params)
            elif method == "ping":
                result = {}
            else:
                return _error(request_id, -32601, f"未知方法：{method}")
        except ToolError as exc:
            return _error(request_id, -32000, str(exc))
        except Exception as exc:  # 兜底：别让 MCP 连接被一个异常带崩
            return _error(request_id, -32603, f"{type(exc).__name__}: {exc}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or name not in self._handlers:
            raise ToolError(f"没有这个工具：{name!r}")
        if not isinstance(arguments, dict):
            raise ToolError("arguments 应为对象")
        try:
            data = self._handlers[name](arguments)
        except ToolError:
            raise
        except (TimeParseError, ValueError, KeyError) as exc:
            raise ToolError(str(exc)) from exc
        return {
            "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
            "isError": False,
        }

    # ---------- 工具实现 ----------
    def _remind_add(self, args: dict[str, Any]) -> dict[str, Any]:
        title = str(args.get("title") or "").strip()
        at_local = str(args.get("at_local") or "").strip()
        if not title or not at_local:
            raise ToolError("title 与 at_local 都是必填")
        reminder = self.reminders.create(
            title,
            at_local,
            body=args.get("body"),
            tz=args.get("tz"),
            origin_session=args.get("session_id"),
            priority="high" if args.get("priority") == "high" else "normal",
        )
        notice = self.reminders.materialize(reminder)
        return {
            "ok": True,
            "reminder_id": reminder.id,
            "notice_id": notice.id,
            "due_utc": reminder.due_at_utc.isoformat(),
            "due_local": reminder.due_at_utc.astimezone().isoformat(),
            "note": "已加入提醒，到点会在手机上响",
        }

    def _remind_list(self, args: dict[str, Any]) -> dict[str, Any]:
        days = int(args.get("horizon_days") or 7)
        until = self.reminders.clock.now() + timedelta(days=days)
        items = self.reminders.upcoming(until=until)
        return {
            "count": len(items),
            "items": [
                {
                    "reminder_id": item.id,
                    "title": item.title,
                    "due_utc": item.due_at_utc.isoformat(),
                    "due_local": item.due_at_utc.astimezone().isoformat(),
                    "state": item.state,
                }
                for item in items
            ],
        }

    def _remind_cancel(self, args: dict[str, Any]) -> dict[str, Any]:
        reminder_id = str(args.get("reminder_id") or "")
        if not reminder_id:
            raise ToolError("reminder_id 必填")
        ok = self.reminders.cancel(reminder_id)
        return {"ok": ok, "reminder_id": reminder_id, "note": "已取消" if ok else "没有这条待触发的提醒"}

    def _remind_reschedule(self, args: dict[str, Any]) -> dict[str, Any]:
        reminder_id = str(args.get("reminder_id") or "")
        at_local = str(args.get("at_local") or "")
        if not reminder_id or not at_local:
            raise ToolError("reminder_id 与 at_local 必填")
        reminder = self.reminders.reschedule(reminder_id, at_local)
        self.reminders.materialize(reminder)
        return {
            "ok": True,
            "reminder_id": reminder.id,
            "due_utc": reminder.due_at_utc.isoformat(),
        }

    def _memory_append(self, args: dict[str, Any]) -> dict[str, Any]:
        text = str(args.get("text") or "").strip()
        if not text:
            raise ToolError("text 必填")
        tags = args.get("tags") or []
        if not isinstance(tags, list):
            raise ToolError("tags 应为字符串数组")
        stamp = datetime.now(tz=UTC).isoformat(timespec="seconds")
        suffix = f"  #tags:{','.join(str(t) for t in tags)}" if tags else ""
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.memory_path, "a", encoding="utf-8") as handle:
            handle.write(f"- {stamp} {text}{suffix}\n")
        return {"ok": True, "path": str(self.memory_path), "bytes": len(text)}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve_stdio(server: McpServer, *, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> int:
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    for line in input_stream:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            response: dict[str, Any] | None = _error(None, -32700, "JSON 解析失败")
        else:
            response = server.handle(message)
        if response is not None:
            output_stream.write(json.dumps(response, ensure_ascii=False) + "\n")
            output_stream.flush()
    return 0

"""MCP 协议测试：不启动 dsh，直接喂 JSON-RPC 消息。

验的是**协议形状**（initialize / tools/list / tools/call / 错误码）与
工具语义（幂等、看不懂时间要报错而不是猜）。
"""

from __future__ import annotations

import io
import json
import shutil
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path

from mephisto.adapters.mcp import McpServer, serve_stdio
from mephisto.app.reminders import ReminderService
from mephisto.clock import FakeClock
from mephisto.store import open_db

TMP_ROOT = Path(__file__).resolve().parent / "_tmp"
NOW = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)


class McpTestBase(unittest.TestCase):
    def setUp(self) -> None:
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.dir = TMP_ROOT / f"mcp_{uuid.uuid4().hex[:12]}"
        self.dir.mkdir()
        self.db = open_db(self.dir)
        self.db.migrate()
        self.clock = FakeClock(NOW)
        self.service = ReminderService(self.db, clock=self.clock, tz="Asia/Shanghai")
        self.memory = self.dir / "workspace" / "memory" / "inbox.md"
        self.server = McpServer(self.service, memory_path=self.memory)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def rpc(self, method: str, params=None, request_id: int | str = 1):
        return self.server.handle({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})

    def call(self, name: str, arguments: dict, request_id: int = 2):
        return self.rpc("tools/call", {"name": name, "arguments": arguments}, request_id)

    def payload(self, response):
        """把 tools/call 的 content[0].text 解析回 dict。"""
        return json.loads(response["result"]["content"][0]["text"])


class ProtocolTest(McpTestBase):
    def test_initialize(self) -> None:
        result = self.rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]
        self.assertIn("protocolVersion", result)
        self.assertEqual(result["serverInfo"]["name"], "mephisto")
        self.assertIn("tools", result["capabilities"])

    def test_tools_list(self) -> None:
        tools = self.rpc("tools/list")["result"]["tools"]
        names = {tool["name"] for tool in tools}
        self.assertEqual(names, {"remind_add", "remind_list", "remind_cancel", "remind_reschedule", "memory_append"})
        for tool in tools:
            self.assertIn("inputSchema", tool)
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_unknown_method(self) -> None:
        response = self.rpc("nope")
        self.assertEqual(response["error"]["code"], -32601)

    def test_unknown_tool(self) -> None:
        response = self.call("nope", {})
        self.assertEqual(response["error"]["code"], -32000)
        self.assertIn("没有这个工具", response["error"]["message"])

    def test_notification_gets_no_response(self) -> None:
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_ping(self) -> None:
        self.assertEqual(self.rpc("ping")["result"], {})


class RemindToolTest(McpTestBase):
    def test_remind_add_creates_notice(self) -> None:
        response = self.call("remind_add", {"title": "交表", "at_local": "3分钟后"})
        self.assertFalse(response["result"]["isError"])
        data = self.payload(response)
        self.assertTrue(data["ok"])
        self.assertEqual(data["reminder_id"][:2], "01")
        pending = self.service.notices.list_pending(until=self.clock.now().replace(year=2027))
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].title, "交表")

    def test_remind_add_requires_fields(self) -> None:
        response = self.call("remind_add", {"title": "只有标题"})
        self.assertEqual(response["error"]["code"], -32000)
        self.assertIn("at_local", response["error"]["message"])

    def test_remind_add_rejects_garbage_time(self) -> None:
        response = self.call("remind_add", {"title": "x", "at_local": "月圆之夜"})
        self.assertEqual(response["error"]["code"], -32000)
        self.assertIn("看不懂", response["error"]["message"])

    def test_remind_add_rejects_past(self) -> None:
        response = self.call("remind_add", {"title": "x", "at_local": "2020-01-01T00:00+08:00"})
        self.assertIn("过去", response["error"]["message"])

    def test_remind_list_and_cancel(self) -> None:
        added = self.payload(self.call("remind_add", {"title": "交表", "at_local": "30分钟后"}))
        listing = self.payload(self.call("remind_list", {"horizon_days": 1}))
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["items"][0]["reminder_id"], added["reminder_id"])

        cancelled = self.payload(self.call("remind_cancel", {"reminder_id": added["reminder_id"]}))
        self.assertTrue(cancelled["ok"])
        after = self.payload(self.call("remind_list", {"horizon_days": 1}))
        self.assertEqual(after["count"], 0)

    def test_remind_reschedule(self) -> None:
        added = self.payload(self.call("remind_add", {"title": "交表", "at_local": "30分钟后"}))
        moved = self.payload(self.call("remind_reschedule", {
            "reminder_id": added["reminder_id"], "at_local": "2小时后",
        }))
        self.assertTrue(moved["ok"])
        self.assertNotEqual(moved["due_utc"], added["due_utc"])


class MemoryToolTest(McpTestBase):
    def test_memory_append_creates_file(self) -> None:
        response = self.call("memory_append", {"text": "用户在准备秋游报名", "tags": ["preference"]})
        self.assertFalse(response["result"]["isError"])
        text = self.memory.read_text(encoding="utf-8")
        self.assertIn("秋游报名", text)
        self.assertIn("#tags:preference", text)

    def test_memory_append_is_append_only(self) -> None:
        self.call("memory_append", {"text": "第一条"})
        self.call("memory_append", {"text": "第二条"})
        lines = self.memory.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("第一条", lines[0])
        self.assertIn("第二条", lines[1])

    def test_empty_text_rejected(self) -> None:
        response = self.call("memory_append", {"text": "   "})
        self.assertEqual(response["error"]["code"], -32000)


class StdioLoopTest(McpTestBase):
    def test_roundtrip_lines(self) -> None:
        stdin = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
            + "这不是 JSON\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n"
        )
        stdout = io.StringIO()
        self.assertEqual(serve_stdio(self.server, stdin=stdin, stdout=stdout), 0)
        lines = [json.loads(line) for line in stdout.getvalue().strip().splitlines()]
        self.assertEqual(len(lines), 3, "两条有 id 的请求 + 一条解析错误")
        self.assertEqual(lines[0]["id"], 1)
        self.assertEqual(lines[1]["error"]["code"], -32700)
        self.assertEqual(lines[2]["id"], 2)


if __name__ == "__main__":
    unittest.main()

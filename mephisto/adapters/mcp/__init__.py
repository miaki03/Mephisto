"""MCP 适配器：让 dsh 里的模型调用 Mephisto 的工具。"""

from .server import PROTOCOL_VERSION, McpServer, ToolError, serve_stdio

__all__ = ["PROTOCOL_VERSION", "McpServer", "ToolError", "serve_stdio"]

"""MCP: клиент на официальном SDK (день 16), свои серверы (день 17+) и вызов инструмента без модели (день 18)."""

from .client import (
    GITHUB_SERVER,
    OWN_SERVERS,
    RATES_SERVER,
    SERVERS,
    Inspection,
    McpServer,
    ToolInfo,
    ToolParam,
    ToolResult,
    WireMessage,
    call_server_tool,
    get_server,
    inspect_server,
)

__all__ = [
    "GITHUB_SERVER",
    "Inspection",
    "McpServer",
    "OWN_SERVERS",
    "RATES_SERVER",
    "SERVERS",
    "ToolInfo",
    "ToolParam",
    "ToolResult",
    "WireMessage",
    "call_server_tool",
    "get_server",
    "inspect_server",
]

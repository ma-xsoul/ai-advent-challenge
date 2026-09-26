"""MCP: клиент, который подключается к серверу инструментов (день 16)."""

from .client import (
    Inspection,
    McpServer,
    ToolInfo,
    ToolParam,
    WireMessage,
    get_server,
    inspect_server,
    SERVERS,
)

__all__ = [
    "Inspection",
    "McpServer",
    "SERVERS",
    "ToolInfo",
    "ToolParam",
    "WireMessage",
    "get_server",
    "inspect_server",
]

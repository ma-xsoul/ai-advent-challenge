"""MCP: клиент на официальном SDK (день 16) и свои серверы (день 17+)."""

from .client import (
    GITHUB_SERVER,
    OWN_SERVERS,
    SERVERS,
    Inspection,
    McpServer,
    ToolInfo,
    ToolParam,
    WireMessage,
    get_server,
    inspect_server,
)

__all__ = [
    "GITHUB_SERVER",
    "Inspection",
    "McpServer",
    "OWN_SERVERS",
    "SERVERS",
    "ToolInfo",
    "ToolParam",
    "WireMessage",
    "get_server",
    "inspect_server",
]

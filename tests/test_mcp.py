import unittest
from contextlib import asynccontextmanager

import anyio
import mcp_types
from mcp.client._memory import InMemoryTransport
from mcp.server import MCPServer
from mcp.shared.message import SessionMessage

from advent.mcp import McpServer, SERVERS, get_server, inspect_server
from advent.mcp.client import _param_type, _summary


SPEC = McpServer(key="test", title="Тест", transport="stdio", target="unused", note="")

LEGACY_TOOLS = [
    {
        "name": "search",
        "description": "Найти документы",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Что искать"},
                "limit": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            },
            "required": ["query"],
        },
    },
    {"name": "summarize", "description": "Сжать текст", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "save", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
]


def modern_server() -> MCPServer:
    """Сервер SDK в памяти процесса — говорит на новом протоколе 2026-07-28."""
    server = MCPServer("lab-test", version="1.0")

    @server.tool(title="Сложить")
    def add(a: int, b: int = 0) -> int:
        """Сложить два числа."""
        return a + b

    @server.tool()
    def ping() -> str:
        """Ответить pong."""
        return "pong"

    return server


def legacy_transport(tools=LEGACY_TOOLS, page_size=2):
    """Транспорт к серверу старой школы: server/discover не знает, список отдаёт страницами.

    Так себя ведут оба публичных сервера лаборатории — DeepWiki и Everything.
    """

    @asynccontextmanager
    async def transport():
        client_send, server_receive = anyio.create_memory_object_stream(16)
        server_send, client_receive = anyio.create_memory_object_stream(16)

        async def reply(body):
            await server_send.send(SessionMessage(mcp_types.jsonrpc_message_adapter.validate_python(body)))

        async def serve():
            async with server_receive, server_send:
                async for item in server_receive:
                    body = item.message.model_dump(by_alias=True, exclude_none=True, mode="json")
                    method, message_id = body.get("method"), body.get("id")
                    if message_id is None:
                        continue
                    if method == "initialize":
                        result = {
                            "protocolVersion": "2025-11-25",
                            "capabilities": {"tools": {}, "experimental": {}},
                            "serverInfo": {"name": "legacy", "version": "0.1"},
                            "instructions": "Сначала search, потом summarize.",
                        }
                        await reply({"jsonrpc": "2.0", "id": message_id, "result": result})
                    elif method == "tools/list":
                        cursor = (body.get("params") or {}).get("cursor")
                        start = int(cursor or 0)
                        page = tools[start : start + page_size]
                        result = {"tools": page}
                        if start + page_size < len(tools):
                            result["nextCursor"] = str(start + page_size)
                        await reply({"jsonrpc": "2.0", "id": message_id, "result": result})
                    else:
                        error = {"code": -32601, "message": "Method not found"}
                        await reply({"jsonrpc": "2.0", "id": message_id, "error": error})

        async with anyio.create_task_group() as group:
            group.start_soon(serve)
            async with client_receive, client_send:
                yield client_receive, client_send
            group.cancel_scope.cancel()

    return lambda server, errlog: transport()


def modern_transport(server=None):
    server = server or modern_server()
    return lambda spec, errlog: InMemoryTransport(server)


class RegistryTestCase(unittest.TestCase):
    def test_registry_has_one_public_server_per_transport(self):
        self.assertEqual({server.transport for server in SERVERS}, {"http", "stdio"})
        self.assertEqual(get_server("deepwiki").target, "https://mcp.deepwiki.com/mcp")
        self.assertIn("server-everything", get_server("everything").target)
        self.assertIsNone(get_server("nope"))


class InspectTestCase(unittest.TestCase):
    def test_modern_server_answers_discover_and_lists_its_tools(self):
        result = inspect_server(SPEC, transport_factory=modern_transport())

        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.protocol_version, "2026-07-28")
        self.assertEqual(result.server_name, "lab-test")
        self.assertEqual([tool.name for tool in result.tools], ["add", "ping"])

        add = result.tools[0]
        self.assertEqual(add.title, "Сложить")
        self.assertEqual(add.description, "Сложить два числа.")
        self.assertEqual([(p.name, p.type, p.required) for p in add.params], [("a", "integer", True), ("b", "integer", False)])
        self.assertEqual(result.tools[1].params, ())
        self.assertGreater(add.tokens, 0)
        self.assertEqual(result.schema_tokens, sum(tool.tokens for tool in result.tools))

        methods = [(message.direction, message.kind, message.method) for message in result.wire]
        self.assertEqual(
            methods,
            [
                ("out", "request", "server/discover"),
                ("in", "response", "server/discover"),
                ("out", "request", "tools/list"),
                ("in", "response", "tools/list"),
            ],
        )
        self.assertIn("lab-test 1.0", result.wire[1].summary)
        self.assertIn("инструментов: 2", result.wire[3].summary)

    def test_legacy_server_refuses_discover_and_client_falls_back_to_initialize(self):
        result = inspect_server(SPEC, transport_factory=legacy_transport())

        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.protocol_version, "2025-11-25")
        self.assertEqual((result.server_name, result.server_version), ("legacy", "0.1"))
        self.assertEqual(result.instructions, "Сначала search, потом summarize.")
        self.assertEqual(result.capabilities, ("tools",))

        methods = [(message.direction, message.kind, message.method) for message in result.wire]
        self.assertEqual(methods[0], ("out", "request", "server/discover"))
        self.assertEqual(methods[1], ("in", "error", "server/discover"))
        self.assertEqual(methods[2], ("out", "request", "initialize"))
        self.assertEqual(methods[3], ("in", "response", "initialize"))
        self.assertEqual(methods[4], ("out", "notification", "notifications/initialized"))
        self.assertIn("отказ на server/discover: -32601", result.wire[1].summary)
        self.assertIn("legacy 0.1", result.wire[3].summary)

    def test_paged_tool_list_is_collected_in_full(self):
        result = inspect_server(SPEC, transport_factory=legacy_transport(page_size=2))

        self.assertEqual([tool.name for tool in result.tools], ["search", "summarize", "save"])
        self.assertEqual(result.pages, 2)
        pages = [message for message in result.wire if message.method == "tools/list" and message.kind == "response"]
        self.assertIn("есть ещё страница", pages[0].summary)
        self.assertNotIn("есть ещё страница", pages[1].summary)

        search = result.tools[0]
        self.assertEqual([(p.name, p.type, p.required) for p in search.params], [("query", "string", True), ("limit", "integer", False)])
        self.assertEqual(search.params[0].description, "Что искать")
        self.assertEqual(result.tools[2].description, "")
        self.assertEqual(result.as_dict()["required_params"], 2)

    def test_failure_is_reported_not_raised(self):
        def broken(server, errlog):
            raise ConnectionRefusedError("порт закрыт")

        result = inspect_server(SPEC, transport_factory=broken)

        self.assertFalse(result.ok)
        self.assertIn("порт закрыт", result.error)
        self.assertEqual(result.tools, ())
        self.assertEqual(result.wire, ())

    def test_missing_stdio_command_names_the_command(self):
        spec = McpServer(key="x", title="X", transport="stdio", target="no-such-binary-xyz --flag", note="")
        result = inspect_server(spec)

        self.assertFalse(result.ok)
        self.assertIn("«no-such-binary-xyz» не найден в PATH", result.error)

    def test_as_dict_is_json_ready(self):
        import json

        data = inspect_server(SPEC, transport_factory=legacy_transport()).as_dict()
        text = json.dumps(data, ensure_ascii=False)

        self.assertIn('"tools/list"', text)
        self.assertEqual(data["server"]["key"], "test")
        self.assertEqual(len(data["tools"]), 3)
        self.assertEqual(data["tools"][0]["params"][0]["name"], "query")


class HelpersTestCase(unittest.TestCase):
    def test_param_type_reads_common_schema_shapes(self):
        self.assertEqual(_param_type({"type": "string"}), "string")
        self.assertEqual(_param_type({"type": ["string", "null"]}), "string | null")
        self.assertEqual(_param_type({"type": "array", "items": {"type": "string"}}), "string[]")
        self.assertEqual(_param_type({"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]}), "string | string[]")
        self.assertEqual(_param_type({"anyOf": [{"type": "integer"}, {"type": "null"}]}), "integer")
        self.assertEqual(_param_type({"enum": ["a", "b"]}), "enum")
        self.assertEqual(_param_type({}), "any")

    def test_summary_names_the_protocol_version_of_the_handshake(self):
        discover = {"method": "server/discover", "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}}}
        initialize = {"method": "initialize", "params": {"protocolVersion": "2025-11-25"}}

        self.assertEqual(_summary("request", "server/discover", discover), "server/discover · протокол 2026-07-28")
        self.assertEqual(_summary("request", "initialize", initialize), "initialize · протокол 2025-11-25")
        self.assertEqual(_summary("notification", "notifications/initialized", {}), "notifications/initialized")


if __name__ == "__main__":
    unittest.main()

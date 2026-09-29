import json
import logging
import unittest
from contextlib import asynccontextmanager

import anyio
import mcp_types
from mcp.client._memory import InMemoryTransport
from mcp.server import MCPServer
from mcp.shared.message import SessionMessage

from advent.mcp import McpServer, SERVERS, get_server, inspect_server
from advent.mcp.client import _param_type, _summary


# Сервер SDK пишет в журнал отказы инструментов — в выводе тестов это шум.
logging.getLogger("mcp").setLevel(logging.ERROR)

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


# ── День 17: свой сервер GitHub и цикл вызова инструментов ────────────


REPO = {
    "full_name": "modelcontextprotocol/python-sdk",
    "description": "The official Python SDK for MCP",
    "html_url": "https://github.com/modelcontextprotocol/python-sdk",
    "stargazers_count": 24410,
    "forks_count": 3980,
    "open_issues_count": 456,
    "language": "Python",
    "topics": ["mcp", "python", "sdk", "llm", "ai", "protocol", "extra"],
    "license": {"spdx_id": "MIT"},
    "archived": False,
    "pushed_at": "2026-09-26T10:00:00Z",
    "created_at": "2024-09-24T12:00:00Z",
    "owner": {"login": "modelcontextprotocol"},
    "node_id": "лишнее поле, в выжимку не попадает",
}


class FakeGitHub:
    """Вместо api.github.com: отдаёт заготовки и запоминает, куда ходили."""

    def __init__(self, error=None):
        self.requests = []
        self._error = error

    def get(self, path, params=None):
        self.requests.append((path, params))
        if self._error is not None:
            raise self._error
        if path == "/search/repositories":
            return {"total_count": 159000, "items": [REPO, {**REPO, "full_name": "other/mcp"}]}
        if path.startswith("/users/"):
            return [REPO]
        return REPO


def github_transport(api=None):
    from advent.mcp.servers.github import build_server

    server = build_server(api or FakeGitHub())
    return lambda spec, errlog: InMemoryTransport(server)


class ScriptedModel:
    """Модель по сценарию: по очереди отдаёт заготовленные ответы и запоминает запросы."""

    def __init__(self, *replies, error_at=None):
        self.payloads = []
        self._replies = list(replies)
        self._error_at = error_at

    def send(self, payload):
        self.payloads.append(payload)
        if self._error_at is not None and len(self.payloads) == self._error_at:
            from advent.agent import TransportError

            raise TransportError("DeepSeek недоступен")
        message = self._replies.pop(0) if self._replies else text_reply("ок")
        finish = "tool_calls" if message.get("tool_calls") else "stop"
        return {"choices": [{"finish_reason": finish, "message": message}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}

    def stream(self, payload):  # pragma: no cover — день 17 не стримит
        raise NotImplementedError


def text_reply(text):
    return {"role": "assistant", "content": text}


def call_reply(name, arguments, call_id="call_1", content=""):
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": raw}}],
    }


def turn(dialog, question, model, api=None):
    from advent.agent import AgentConfig
    from advent.mcp import GITHUB_SERVER
    from advent.mcp.agent import run_turn

    return run_turn(
        dialog,
        question,
        config=AgentConfig(name="t", system_prompt="Ты помощник."),
        transport=model,
        server=GITHUB_SERVER,
        factory=github_transport(api),
    )


class GitHubServerTestCase(unittest.TestCase):
    def test_tools_are_registered_with_described_parameters(self):
        from advent.mcp import GITHUB_SERVER

        result = inspect_server(GITHUB_SERVER, transport_factory=github_transport())
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.server_name, "advent-github")
        tools = {tool.name: tool for tool in result.tools}
        self.assertEqual(set(tools), {"search_repositories", "get_repository", "list_user_repositories"})

        search = {param.name: param for param in tools["search_repositories"].params}
        self.assertTrue(search["query"].required)
        self.assertIn("квалификаторы", search["query"].description)
        self.assertFalse(search["limit"].required)
        self.assertEqual(tools["search_repositories"].schema["properties"]["limit"]["maximum"], 10)

        repo = tools["get_repository"]
        self.assertEqual([(p.name, p.required) for p in repo.params], [("owner", True), ("repo", True)])
        self.assertIn("pattern", repo.schema["properties"]["owner"])
        self.assertIn("лицензия", repo.description)
        self.assertIn("get_repository", result.instructions)

    def test_repo_card_keeps_only_what_is_asked_about(self):
        from advent.mcp.servers.github import repo_card

        card = repo_card(REPO)
        self.assertEqual(card["stars"], 24410)
        self.assertEqual(card["license"], "MIT")
        self.assertEqual(card["pushed"], "2026-09-26")
        self.assertEqual(len(card["topics"]), 6)
        self.assertNotIn("node_id", card)
        self.assertNotIn("owner", card)

    def test_refusal_names_the_reason(self):
        from unittest.mock import Mock

        from advent.mcp.servers.github import _refusal

        missing = Mock(status_code=404, headers={}, json=lambda: {"message": "Not Found"})
        limited = Mock(status_code=403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1790530865"}, json=lambda: {})
        other = Mock(status_code=422, headers={}, json=lambda: {"message": "Validation Failed"})

        self.assertIn("нет такого", _refusal(missing, "/repos/a/b"))
        self.assertIn("Лимит GitHub API исчерпан", _refusal(limited, "/search"))
        self.assertIn("422: Validation Failed", _refusal(other, "/search"))


class ToolLoopTestCase(unittest.TestCase):
    def test_model_asks_agent_calls_result_goes_back_to_model(self):
        from advent.mcp.agent import ToolDialog

        dialog = ToolDialog()
        api = FakeGitHub()
        model = ScriptedModel(
            call_reply("get_repository", {"owner": "modelcontextprotocol", "repo": "python-sdk"}, content="Смотрю карточку."),
            text_reply("У python-sdk 24 410 звёзд."),
        )
        events = turn(dialog, "Сколько звёзд у python-sdk?", model, api)

        self.assertEqual([event["type"] for event in events], ["connected", "model", "tool_call", "tool_result", "answer", "done"])
        self.assertEqual(api.requests, [("/repos/modelcontextprotocol/python-sdk", None)])
        self.assertEqual([m["role"] for m in dialog.messages], ["user", "assistant", "tool", "assistant"])
        self.assertEqual(dialog.messages[1]["tool_calls"][0]["function"]["name"], "get_repository")
        self.assertEqual(dialog.messages[2]["tool_call_id"], "call_1")
        self.assertIn('"stars": 24410', dialog.messages[2]["content"])

        first, second = model.payloads
        self.assertEqual(len(first["tools"]), 3)
        self.assertEqual(first["tool_choice"], "auto")
        self.assertEqual(first["thinking"], {"type": "disabled"})
        self.assertIn("Инструкции MCP-сервера", first["messages"][0]["content"])
        self.assertEqual(second["messages"][-1]["role"], "tool")

        result = events[3]
        self.assertTrue(result["ok"])
        self.assertIn("24410", result["preview"])
        self.assertNotIn("for_model", result)
        stats = events[-1]["stats"]
        self.assertEqual((stats["llm_calls"], stats["tool_calls"], stats["tool_errors"]), (2, 1, 0))
        self.assertEqual(stats["prompt_tokens"], 200)
        self.assertEqual(stats["schema_tokens_sent"], stats["schema_tokens"] * 2)
        self.assertEqual(dialog.calls[0]["arguments"], {"owner": "modelcontextprotocol", "repo": "python-sdk"})

    def test_question_without_github_needs_no_tool(self):
        from advent.mcp.agent import ToolDialog

        dialog = ToolDialog()
        api = FakeGitHub()
        model = ScriptedModel(text_reply("MCP — протокол инструментов."))
        events = turn(dialog, "Что такое MCP?", model, api)

        self.assertEqual([event["type"] for event in events], ["connected", "answer", "done"])
        self.assertEqual(api.requests, [])
        self.assertEqual(len(model.payloads), 1)
        self.assertEqual(dialog.calls, [])

    def test_tool_failure_reaches_the_model_as_text(self):
        from advent.mcp.agent import ToolDialog
        from advent.mcp.servers.github import GitHubError

        dialog = ToolDialog()
        model = ScriptedModel(call_reply("get_repository", {"owner": "a", "repo": "b"}), text_reply("Такого репозитория нет."))
        events = turn(dialog, "Про a/b", model, FakeGitHub(error=GitHubError("На GitHub нет такого: /repos/a/b")))

        result = next(event for event in events if event["type"] == "tool_result")
        self.assertFalse(result["ok"])
        self.assertIn("На GitHub нет такого", dialog.messages[2]["content"])
        self.assertEqual(events[-1]["stats"]["tool_errors"], 1)

    def test_invalid_arguments_are_refused_before_github(self):
        from advent.mcp.agent import ToolDialog

        api = FakeGitHub()
        dialog = ToolDialog()
        model = ScriptedModel(
            call_reply("get_repository", "{не json", call_id="bad"),
            call_reply("get_repository", {"owner": "../etc", "repo": "passwd"}, call_id="path"),
            text_reply("Не вышло."),
        )
        events = turn(dialog, "Сломай", model, api)

        results = [event for event in events if event["type"] == "tool_result"]
        self.assertEqual([result["ok"] for result in results], [False, False])
        self.assertIn("не разобраны как JSON", dialog.messages[2]["content"])
        self.assertIn("pattern", dialog.messages[4]["content"])
        self.assertEqual(api.requests, [])

    def test_rounds_are_capped_and_the_last_one_forbids_tools(self):
        from advent.mcp.agent import MAX_ROUNDS, ToolDialog

        dialog = ToolDialog()
        replies = [call_reply("get_repository", {"owner": "a", "repo": "b"}, call_id=f"c{i}") for i in range(MAX_ROUNDS)]
        model = ScriptedModel(*replies)
        events = turn(dialog, "Зациклись", model)

        self.assertEqual(len(model.payloads), MAX_ROUNDS)
        self.assertEqual([payload["tool_choice"] for payload in model.payloads], ["auto"] * (MAX_ROUNDS - 1) + ["none"])
        answer = next(event for event in events if event["type"] == "answer")
        self.assertIn(f"Не уложился в {MAX_ROUNDS}", answer["content"])
        self.assertEqual(dialog.messages[-1]["role"], "assistant")

    def test_failed_turn_leaves_no_tail_in_history(self):
        from advent.mcp.agent import ToolDialog

        dialog = ToolDialog()
        turn(dialog, "Первый", ScriptedModel(text_reply("ответ")))
        self.assertEqual(len(dialog.messages), 2)

        model = ScriptedModel(call_reply("get_repository", {"owner": "a", "repo": "b"}), error_at=2)
        events = turn(dialog, "Второй", model)

        self.assertEqual(events[-1], {"type": "error", "error": "DeepSeek недоступен"})
        self.assertEqual([m["content"] for m in dialog.messages], ["Первый", "ответ"])
        self.assertEqual(len(dialog.turns), 1)

    def test_store_roundtrip_and_safe_ids(self):
        import tempfile

        from advent.mcp.agent import DialogError, ToolDialog, ToolDialogStore

        with tempfile.TemporaryDirectory() as directory:
            store = ToolDialogStore(directory, "d17")
            dialog = ToolDialog()
            turn(dialog, "Сколько звёзд?", ScriptedModel(call_reply("get_repository", {"owner": "a", "repo": "b"}), text_reply("много")))
            store.save("s1", dialog)

            loaded = store.load("s1")
            self.assertEqual(loaded.messages, dialog.messages)
            self.assertEqual(loaded.calls[0]["name"], "get_repository")
            self.assertTrue(store.describe("s1")["exists"])
            self.assertEqual(store.load("nobody").messages, [])
            self.assertTrue(store.delete("s1"))
            with self.assertRaises(DialogError):
                store.load("../etc")


if __name__ == "__main__":
    unittest.main()

import json
import logging
import pathlib
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
        return {**REPO, "full_name": path.removeprefix("/repos/")}


def github_transport(api=None, reports_dir=None):
    """Сервер GitHub в памяти. Отчёты — только во временную папку, не в настоящую data/day19."""
    import tempfile

    from advent.mcp.servers.github import build_server

    server = build_server(api or FakeGitHub(), reports_dir=reports_dir or tempfile.mkdtemp())
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
        self.assertEqual(
            set(tools),
            {"search_repositories", "get_repository", "list_user_repositories", "compare_repositories", "save_report"},
        )

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
        self.assertEqual(len(first["tools"]), 5)
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


# ── День 19: сравнение, отчёт и сверка цепочки ───────────────────────


def search_call(names, round_number=1, query="mcp"):
    items = [{"full_name": name} for name in names]
    text = json.dumps({"query": query, "total": 100, "items": items}, ensure_ascii=False)
    return {"name": "search_repositories", "ok": True, "round": round_number, "arguments": {"query": query}, "for_model": text}


def compare_call(names, table, round_number=2):
    text = json.dumps({"compared": len(names), "leaders": {"stars": names[0]}, "table": table}, ensure_ascii=False)
    return {"name": "compare_repositories", "ok": True, "round": round_number, "arguments": {"full_names": names}, "for_model": text}


def save_call(file, markdown, round_number=3):
    text = json.dumps({"file": file, "bytes": len(markdown.encode())}, ensure_ascii=False)
    return {"name": "save_report", "ok": True, "round": round_number, "arguments": {"title": "т", "markdown": markdown}, "for_model": text}


TABLE = "| # | Репозиторий | ★ |\n|---|---|---|\n| 1 | a/a | 10 |\n| 2 | b/b | 5 |"


class ComposeToolsTestCase(unittest.TestCase):
    def test_compare_ranks_and_builds_the_table(self):
        from datetime import datetime

        from advent.mcp.servers.github import compare, repo_card

        cards = [
            repo_card({**REPO, "full_name": "a/small", "stargazers_count": 5, "pushed_at": "2026-09-29T00:00:00Z", "open_issues_count": 900}),
            repo_card({**REPO, "full_name": "b/big", "stargazers_count": 500, "pushed_at": "2026-09-01T00:00:00Z"}),
        ]
        result = compare(cards, datetime(2026, 9, 30, 12, 0))

        self.assertEqual([row["full_name"] for row in result["repositories"]], ["b/big", "a/small"])
        self.assertEqual(result["repositories"][0]["rank"], 1)
        self.assertEqual(result["repositories"][1]["days_since_push"], 1)
        self.assertEqual(result["leaders"], {"stars": "b/big", "freshest_push": "a/small", "most_open_issues": "a/small"})
        lines = result["table"].splitlines()
        self.assertEqual(len(lines), 4)
        self.assertIn("[b/big](", lines[2])

    def test_report_filename_cannot_escape_the_folder(self):
        from datetime import datetime

        from advent.mcp.servers.github import report_filename

        name = report_filename("../../etc/passwd: Топ", datetime(2026, 9, 30, 12, 0, 0))
        self.assertEqual(name, "2026-09-30-120000-etc-passwd-топ.md")
        self.assertNotIn("/", name)
        self.assertEqual(report_filename("!!!", datetime(2026, 9, 30)), "2026-09-30-000000-report.md")

    def test_compare_and_save_through_mcp(self):
        import tempfile

        from advent.mcp import GITHUB_SERVER, call_server_tool

        with tempfile.TemporaryDirectory() as directory:
            api = FakeGitHub()
            factory = github_transport(api, directory)

            compared = call_server_tool(GITHUB_SERVER, "compare_repositories", {"full_names": ["a/one", "b/two", "a/one"]}, transport_factory=factory)
            self.assertTrue(compared.ok, compared.error)
            self.assertEqual(compared.data["compared"], 2)
            self.assertEqual(api.requests, [("/repos/a/one", None), ("/repos/b/two", None)])

            saved = call_server_tool(GITHUB_SERVER, "save_report", {"title": "Отчёт", "markdown": "# Отчёт\n" + compared.data["table"]}, transport_factory=factory)
            self.assertTrue(saved.ok, saved.error)
            path = pathlib.Path(directory) / saved.data["file"]
            self.assertEqual(path.read_text(encoding="utf-8"), "# Отчёт\n" + compared.data["table"])

            for bad in ({"full_names": ["only/one"]}, {"full_names": ["no-slash", "a/b"]}, {"full_names": ["../x/y", "a/b"]}):
                with self.subTest(arguments=bad):
                    self.assertFalse(call_server_tool(GITHUB_SERVER, "compare_repositories", bad, transport_factory=factory).ok)
            too_long = {"title": "т", "markdown": "x" * 20001}
            self.assertFalse(call_server_tool(GITHUB_SERVER, "save_report", too_long, transport_factory=factory).ok)


class ChainCheckTestCase(unittest.TestCase):
    def _saved(self, directory, markdown, name="r.md"):
        (pathlib.Path(directory) / name).write_text(markdown, encoding="utf-8")
        return name

    def test_clean_chain_passes_every_check(self):
        import tempfile

        from advent.mcp.chain import check_chain

        with tempfile.TemporaryDirectory() as directory:
            report = "# Итог\n\n" + TABLE + "\n\nВывод."
            calls = [search_call(["a/a", "b/b", "c/c"]), compare_call(["a/a", "b/b"], TABLE), save_call(self._saved(directory, report), report)]
            result = check_chain(calls, directory)

        self.assertTrue(result["ok"], result["checks"])
        self.assertTrue(result["complete"])
        self.assertEqual([step["name"] for step in result["steps"]], ["search_repositories", "compare_repositories", "save_report"])
        self.assertIn("2 из 3 найденных", result["checks"][1]["detail"])

    def test_each_break_in_the_chain_is_caught(self):
        import tempfile

        from advent.mcp.chain import check_chain

        with tempfile.TemporaryDirectory() as directory:
            report = "# Итог\n\n" + TABLE
            invented = check_chain([search_call(["a/a"]), compare_call(["a/a", "x/invented"], TABLE)], directory)
            self.assertFalse(invented["checks"][1]["ok"])
            self.assertIn("x/invented", invented["checks"][1]["detail"])
            self.assertFalse(invented["checks"][0]["ok"])

            retold = "# Итог\n\nЛидер — a/a, у него 10 звёзд."
            paraphrased = check_chain(
                [search_call(["a/a", "b/b"]), compare_call(["a/a", "b/b"], TABLE), save_call(self._saved(directory, retold), retold)],
                directory,
            )
            self.assertFalse(paraphrased["checks"][2]["ok"])
            self.assertIn("пересказала", paraphrased["checks"][2]["detail"])

            self._saved(directory, "другой текст", "changed.md")
            changed = check_chain(
                [search_call(["a/a", "b/b"]), compare_call(["a/a", "b/b"], TABLE), save_call("changed.md", report)],
                directory,
            )
            self.assertFalse(changed["checks"][3]["ok"])

            parallel = check_chain(
                [search_call(["a/a", "b/b"], 1), compare_call(["a/a", "b/b"], TABLE, 1), save_call(self._saved(directory, report), report, 2)],
                directory,
            )
            self.assertFalse(parallel["checks"][0]["ok"])
            self.assertIn("не по очереди", parallel["checks"][0]["detail"])

    def test_refined_search_is_the_one_that_counts(self):
        import tempfile

        from advent.mcp.chain import check_chain

        with tempfile.TemporaryDirectory() as directory:
            report = TABLE
            calls = [
                search_call(["old/one", "old/two"], 1, "mcp"),
                search_call(["a/a", "b/b"], 2, "topic:mcp"),
                compare_call(["a/a", "b/b"], TABLE, 3),
                save_call(self._saved(directory, report), report, 4),
            ]
            result = check_chain(calls, directory)
            self.assertTrue(result["ok"], result["checks"])
            self.assertIn("2 → 3 → 4", result["checks"][0]["detail"])

    def test_agent_builds_the_whole_chain_itself(self):
        import tempfile

        from advent.agent import AgentConfig
        from advent.mcp import GITHUB_SERVER
        from advent.mcp.agent import ToolDialog, run_turn
        from advent.mcp.chain import check_chain
        from advent.mcp.servers.github import compare, repo_card

        with tempfile.TemporaryDirectory() as directory:
            names = ["modelcontextprotocol/python-sdk", "other/mcp"]
            from datetime import datetime, timezone

            table = compare([repo_card({**REPO, "full_name": name}) for name in names], datetime.now(timezone.utc))["table"]
            model = ScriptedModel(
                call_reply("search_repositories", {"query": "mcp", "limit": 2}, "c1"),
                call_reply("compare_repositories", {"full_names": names}, "c2"),
                call_reply("save_report", {"title": "MCP", "markdown": "# MCP\n\n" + table}, "c3"),
                text_reply("Готово, отчёт сохранён."),
            )
            dialog = ToolDialog()
            events = run_turn(
                dialog,
                "Найди, сравни, сохрани",
                config=AgentConfig(name="t", system_prompt="т"),
                transport=model,
                server=GITHUB_SERVER,
                factory=github_transport(FakeGitHub(), directory),
                max_rounds=6,
            )

            self.assertEqual(events[-1]["stats"]["tool_calls"], 3)
            self.assertEqual(events[-1]["stats"]["llm_calls"], 4)
            self.assertEqual([payload["tool_choice"] for payload in model.payloads], ["auto"] * 4)
            result = check_chain(dialog.calls, directory)
            self.assertTrue(result["ok"], result["checks"])
            self.assertEqual(len(list(pathlib.Path(directory).glob("*.md"))), 1)


# ── День 18: сервер курса, планировщик, crontab ──────────────────────


class FakeRates:
    """Вместо Yahoo и ЦБ: курсы по очереди, счётчик обращений, сбои по заказу."""

    def __init__(self, prices=(84.10, 84.20, 84.05), official=84.43, market_error=None, official_error=None):
        self.prices = list(prices)
        self.official_value = official
        self.market_calls = 0
        self.official_calls = 0
        self.market_error = market_error
        self.official_error = official_error

    def market(self):
        self.market_calls += 1
        if self.market_error:
            raise self.market_error
        price = self.prices[min(self.market_calls - 1, len(self.prices) - 1)]
        return price, 1790757000 + self.market_calls * 60

    def official(self):
        self.official_calls += 1
        if self.official_error:
            raise self.official_error
        return "2026-09-30", self.official_value


class Clock:
    """Ручные часы: тесты двигают время сами."""

    def __init__(self, start=1790757000.0):
        self.value = start

    def __call__(self):
        return self.value

    def advance(self, minutes):
        self.value += minutes * 60


def rates_transport(directory, source=None, clock=None):
    from advent.mcp.servers.rates import RatesStore, build_server

    store = RatesStore(pathlib.Path(directory) / "rates.sqlite3")
    server = build_server(store, source or FakeRates(), now=clock or Clock())
    return lambda spec, errlog: InMemoryTransport(server)


class RatesServerTestCase(unittest.TestCase):
    def test_aggregate_reads_the_period(self):
        from advent.mcp.servers.rates import aggregate

        points = [{"ts": 1790757000 + i * 120, "price": price} for i, price in enumerate((84.10, 84.30, 84.00, 84.20))]
        result = aggregate(points, 10, {"date": "2026-09-30", "value": 84.50})

        self.assertEqual((result["points"], result["first"], result["last"]), (4, 84.10, 84.20))
        self.assertAlmostEqual(result["change"], 0.10)
        self.assertEqual(result["min"]["price"], 84.00)
        self.assertEqual(result["max"]["price"], 84.30)
        self.assertAlmostEqual(result["last_vs_official"], -0.30)
        self.assertEqual(aggregate([], 10, None)["points"], 0)
        self.assertIn("не срабатывало", aggregate([], 10, None)["note"])

    def test_thin_keeps_the_ends(self):
        from advent.mcp.servers.rates import thin

        points = [{"ts": i, "price": i} for i in range(100)]
        thinned = thin(points, 5)
        self.assertEqual(len(thinned), 5)
        self.assertEqual((thinned[0]["ts"], thinned[-1]["ts"]), (0, 99))
        self.assertEqual(thin(points[:3], 5), points[:3])

    def test_collect_saves_snapshots_and_caches_the_official_rate(self):
        import tempfile

        from advent.mcp import RATES_SERVER, call_server_tool

        with tempfile.TemporaryDirectory() as directory:
            source, clock = FakeRates(), Clock()
            factory = rates_transport(directory, source, clock)

            first = call_server_tool(RATES_SERVER, "collect_rate", transport_factory=factory)
            clock.advance(2)
            second = call_server_tool(RATES_SERVER, "collect_rate", transport_factory=factory)

            self.assertTrue(first.ok, first.error)
            self.assertEqual(first.data["saved"]["price"], 84.10)
            self.assertEqual(second.data["snapshots_total"], 2)
            self.assertEqual(second.data["official"]["value"], 84.43)
            self.assertEqual(source.official_calls, 1)

            summary = call_server_tool(RATES_SERVER, "rate_summary", {"minutes": 10}, transport_factory=factory)
            self.assertEqual(summary.data["points"], 2)
            self.assertAlmostEqual(summary.data["change"], 0.10)

            history = call_server_tool(RATES_SERVER, "rate_history", {"minutes": 10, "limit": 2}, transport_factory=factory)
            self.assertEqual([point["price"] for point in history.data["points"]], [84.10, 84.20])

    def test_market_failure_is_a_tool_error_and_official_failure_is_not(self):
        import tempfile

        from advent.mcp import RATES_SERVER, call_server_tool
        from advent.mcp.servers.rates import RateError

        with tempfile.TemporaryDirectory() as directory:
            down = FakeRates(market_error=RateError("Нет связи с источником курса"))
            result = call_server_tool(RATES_SERVER, "collect_rate", transport_factory=rates_transport(directory, down))
            self.assertFalse(result.ok)
            self.assertIn("Нет связи с источником курса", result.error)

            cbr_down = FakeRates(official_error=RateError("ЦБ молчит"))
            result = call_server_tool(RATES_SERVER, "collect_rate", transport_factory=rates_transport(directory, cbr_down))
            self.assertTrue(result.ok)
            self.assertIsNone(result.data["official"])

    def test_summary_arguments_are_bounded(self):
        import tempfile

        from advent.mcp import RATES_SERVER, call_server_tool

        with tempfile.TemporaryDirectory() as directory:
            result = call_server_tool(RATES_SERVER, "rate_summary", {"minutes": 0}, transport_factory=rates_transport(directory))
            self.assertFalse(result.ok)


class SchedulerTestCase(unittest.TestCase):
    def _inbox(self, directory):
        from advent.scheduler import Paths
        from advent.scheduler.inbox import Inbox

        return Inbox(Paths.under(directory).inbox)

    def test_first_tick_only_collects_and_a_later_one_sends_a_summary(self):
        import tempfile

        from advent.scheduler import tick

        with tempfile.TemporaryDirectory() as directory:
            clock = Clock()
            factory = rates_transport(directory, FakeRates(), clock)
            inbox = self._inbox(directory)
            model = ScriptedModel(call_reply("rate_summary", {"minutes": 10}), text_reply("Доллар вырос на 0,10 ₽."))

            first = tick(inbox, summary_every=10, factory=factory, transport=model, now=clock)
            self.assertTrue(first["tick"]["ok"])
            self.assertEqual(first["tick"]["price"], 84.10)
            self.assertIsNone(first["summary"])
            self.assertEqual(model.payloads, [])

            clock.advance(2)
            self.assertIsNone(tick(inbox, summary_every=10, factory=factory, transport=model, now=clock)["summary"])

            clock.advance(8)
            third = tick(inbox, summary_every=10, factory=factory, transport=model, now=clock)
            summary = third["summary"]
            self.assertTrue(summary["ok"])
            self.assertEqual(summary["text"], "Доллар вырос на 0,10 ₽.")
            self.assertEqual(summary["trigger"], "cron")
            self.assertEqual(summary["calls"][0]["name"], "rate_summary")
            self.assertIn('"points": 3', summary["calls"][0]["preview"])
            self.assertEqual(summary["stats"]["llm_calls"], 2)
            self.assertIn("за последние 10 минут", model.payloads[0]["messages"][1]["content"])
            self.assertIn("без советов", model.payloads[0]["messages"][0]["content"].lower())

            self.assertEqual(inbox.ticks(1)[0]["summary_id"], summary["id"])
            self.assertEqual(len(inbox.summaries()), 1)

            clock.advance(2)
            self.assertIsNone(tick(inbox, summary_every=10, factory=factory, transport=model, now=clock)["summary"])

    def test_failed_summary_still_lands_in_the_inbox(self):
        import tempfile

        from advent.scheduler import summarize

        with tempfile.TemporaryDirectory() as directory:
            inbox = self._inbox(directory)
            item = summarize(inbox, minutes=10, trigger="manual", factory=rates_transport(directory), transport=ScriptedModel(error_at=1))

            self.assertFalse(item["ok"])
            self.assertIn("DeepSeek недоступен", item["text"])
            self.assertEqual(inbox.summaries()[0]["id"], item["id"])

    def test_failed_collect_is_a_red_tick(self):
        import tempfile

        from advent.mcp.servers.rates import RateError
        from advent.scheduler import collect

        with tempfile.TemporaryDirectory() as directory:
            inbox = self._inbox(directory)
            down = FakeRates(market_error=RateError("Источник курса ответил 429"))
            record = collect(inbox, trigger="cron", factory=rates_transport(directory, down))

            self.assertFalse(record["ok"])
            self.assertIn("429", record["error"])
            self.assertIsNone(record["price"])

    def test_clearing_the_inbox_keeps_ticks(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            inbox = self._inbox(directory)
            inbox.add_tick(ts=1, trigger="cron", ok=True, price=84.0, error=None, ms=5)
            inbox.add_summary(ts=2, trigger="cron", minutes=10, ok=True, text="т", stats={}, calls=[])

            self.assertEqual(inbox.clear_summaries(), 1)
            self.assertEqual(inbox.summaries(), [])
            self.assertEqual(len(inbox.ticks()), 1)


class FakeCrontab:
    """Вместо настоящего crontab — строка в памяти."""

    def __init__(self, text=None):
        self.text = text

    def __call__(self, command, input=None, capture_output=True, text=True):
        import subprocess

        if command == ["crontab", "-l"]:
            if self.text is None:
                return subprocess.CompletedProcess(command, 1, "", "crontab: no crontab for tester\n")
            return subprocess.CompletedProcess(command, 0, self.text, "")
        if command == ["crontab", "-"]:
            self.text = input
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)


class CronTestCase(unittest.TestCase):
    def test_install_replaces_our_line_and_keeps_others(self):
        from advent.scheduler import cron

        crontab = FakeCrontab("0 9 * * * backup.sh\n")
        log = pathlib.Path("/tmp/cron.log")

        line = cron.install(2, 10, log, crontab)
        self.assertTrue(line.startswith("*/2 * * * * cd "))
        self.assertIn("-m advent.scheduler tick --summary-every 10", line)
        self.assertTrue(line.endswith(cron.MARKER))

        cron.install(5, 30, log, crontab)
        lines = crontab.text.splitlines()
        self.assertEqual(lines[0], "0 9 * * * backup.sh")
        self.assertEqual(sum(line.endswith(cron.MARKER) for line in lines), 1)

        state = cron.status(crontab)
        self.assertEqual((state["installed"], state["every"], state["summary_every"]), (True, 5, 30))

        self.assertTrue(cron.uninstall(crontab))
        self.assertEqual(crontab.text, "0 9 * * * backup.sh\n")
        self.assertFalse(cron.uninstall(crontab))

    def test_empty_crontab_is_not_an_error(self):
        from advent.scheduler import cron

        crontab = FakeCrontab(None)
        self.assertEqual(cron.status(crontab)["installed"], False)
        self.assertIsNone(cron.status(crontab)["error"])
        cron.install(2, 10, pathlib.Path("/tmp/cron.log"), crontab)
        self.assertEqual(len(crontab.text.splitlines()), 1)


# ── День 20: несколько серверов, маршрутизация, проверка флоу ─────────


def fake_gitmcp():
    """Вместо настоящего GitMCP — сервер в памяти с теми же инструментами."""
    server = MCPServer("GitMCP", version="test")

    @server.tool()
    def fetch_generic_documentation(owner: str, repo: str) -> str:
        """Fetch documentation for any GitHub repository by providing owner and project name."""
        return f"# {owner}/{repo}\n\nАрхитектура: фронтенд, бэкенд, хранилище."

    @server.tool()
    def search_generic_documentation(owner: str, repo: str, query: str) -> str:
        """Semantically search in documentation for any GitHub repository."""
        return f"## {query}\n\n{owner}/{repo}: слой API и слой данных."

    return server


def orchestra_transport(directory, api=None, down=()):
    """Три сервера в памяти, как в реестре дня 20; ключи из down «не отвечают»."""
    from advent.mcp.servers.github import build_server as build_github
    from advent.mcp.servers.rates import RatesStore, build_server as build_rates

    servers = {
        "github": build_github(api or FakeGitHub(), reports_dir=pathlib.Path(directory) / "reports"),
        "rates": build_rates(RatesStore(pathlib.Path(directory) / "rates.sqlite3"), FakeRates(), now=Clock()),
        "gitmcp": fake_gitmcp(),
    }

    def factory(spec, errlog):
        if spec.key in down:
            raise ConnectionRefusedError(f"{spec.key} недоступен")
        return InMemoryTransport(servers[spec.key])

    return factory


def orchestra_servers():
    from advent.mcp import GITHUB_SERVER, GITMCP_SERVER, RATES_SERVER

    return [GITMCP_SERVER, GITHUB_SERVER, RATES_SERVER]


LONG_QUESTION = "Найди 2 звёздных MCP-репозитория, сравни, по документации лидера из GitMCP разберись в архитектуре и сохрани отчёт."


class OrchestrationTestCase(unittest.TestCase):
    def _turn(self, directory, model, question=LONG_QUESTION, down=(), max_rounds=12):
        from advent.agent import AgentConfig
        from advent.mcp.agent import ToolDialog, run_turn

        dialog = ToolDialog()
        events = run_turn(
            dialog,
            question,
            config=AgentConfig(name="t", system_prompt="т"),
            transport=model,
            servers=orchestra_servers(),
            factory=orchestra_transport(directory, down=down),
            max_rounds=max_rounds,
        )
        return dialog, events

    def _long_flow_model(self):
        names = ["modelcontextprotocol/python-sdk", "other/mcp"]
        return ScriptedModel(
            call_reply("github__search_repositories", {"query": "mcp", "limit": 2}, "c1"),
            call_reply("github__compare_repositories", {"full_names": names}, "c2"),
            call_reply("gitmcp__fetch_generic_documentation", {"owner": "modelcontextprotocol", "repo": "python-sdk"}, "c3"),
            call_reply("github__save_report", {"title": "Отчёт", "markdown": "# Отчёт\n\nАрхитектура: фронтенд."}, "c4"),
            text_reply("Готово."),
        )

    def test_tools_of_every_server_are_prefixed_and_calls_are_routed(self):
        import tempfile

        from advent.mcp.orchestration import check_flow

        with tempfile.TemporaryDirectory() as directory:
            model = self._long_flow_model()
            dialog, events = self._turn(directory, model)

            names = [spec["function"]["name"] for spec in model.payloads[0]["tools"]]
            self.assertIn("github__search_repositories", names)
            self.assertIn("gitmcp__fetch_generic_documentation", names)
            self.assertIn("rates__rate_summary", names)
            self.assertEqual(len(names), 5 + 2 + 3)
            system = model.payloads[0]["messages"][0]["content"]
            self.assertIn("«сервер__инструмент»", system)
            self.assertIn("Инструкции MCP-сервера «GitHub · наш сервер»", system)

            connected = events[0]
            self.assertEqual([card["key"] for card in connected["servers"]], ["gitmcp", "github", "rates"])
            self.assertTrue(all(card["ok"] for card in connected["servers"]))

            self.assertEqual([(call["server"], call["tool"]) for call in dialog.calls], [
                ("github", "search_repositories"),
                ("github", "compare_repositories"),
                ("gitmcp", "fetch_generic_documentation"),
                ("github", "save_report"),
            ])
            self.assertIn("# modelcontextprotocol/python-sdk", dialog.calls[2]["for_model"])
            self.assertEqual(len(list((pathlib.Path(directory) / "reports").glob("*.md"))), 1)

            flow = check_flow(dialog.calls, dialog.turns[-1]["servers"], LONG_QUESTION)
            self.assertTrue(flow["ok"], flow["checks"])
            self.assertEqual(len(flow["checks"]), 4)
            self.assertEqual(flow["path"], ["GitHub · наш сервер", "GitMCP", "GitHub · наш сервер"])
            self.assertEqual(flow["idle"], ["Курс доллара · наш сервер"])

    def test_a_dead_server_does_not_stop_the_turn(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            model = ScriptedModel(call_reply("rates__rate_summary", {"minutes": 60}), text_reply("Курс не менялся."))
            dialog, events = self._turn(directory, model, "Какой курс доллара?", down=("gitmcp",))

            cards = {card["key"]: card for card in events[0]["servers"]}
            self.assertFalse(cards["gitmcp"]["ok"])
            self.assertIn("gitmcp недоступен", cards["gitmcp"]["error"])
            names = [spec["function"]["name"] for spec in model.payloads[0]["tools"]]
            self.assertFalse(any(name.startswith("gitmcp__") for name in names))
            self.assertEqual(events[-1]["type"], "done")
            self.assertEqual(dialog.calls[0]["server"], "rates")

    def test_all_servers_down_is_an_error(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            dialog, events = self._turn(directory, ScriptedModel(), down=("gitmcp", "github", "rates"))
            self.assertEqual(events[-1]["type"], "error")
            self.assertIn("Ни один MCP-сервер не ответил", events[-1]["error"])
            self.assertEqual(dialog.messages, [])

    def test_unknown_tool_is_refused_without_a_call(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            model = ScriptedModel(call_reply("search_repositories", {"query": "mcp"}), text_reply("Ой."))
            dialog, _ = self._turn(directory, model)

            call = dialog.calls[0]
            self.assertIsNone(call["server"])
            self.assertFalse(call["ok"])
            self.assertIn("Нет такого инструмента: search_repositories", dialog.messages[2]["content"])
            self.assertIn("github__search_repositories", dialog.messages[2]["content"])

    def test_leaked_call_markup_is_cut_from_the_last_answer(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            leaked = "Сейчас сохраню отчёт.\n<｜｜DSML｜｜ calls> <｜｜DSML｜｜ invoke name=\"github__save_report\">"
            replies = [call_reply("rates__rate_summary", {"minutes": 60}, "c1"), text_reply(leaked)]
            _, events = self._turn(directory, ScriptedModel(*replies), "курс доллара", max_rounds=2)
            answer = next(event for event in events if event["type"] == "answer")["content"]
            self.assertEqual(answer, "Сейчас сохраню отчёт.")

            only_markup = [call_reply("rates__rate_summary", {"minutes": 60}, "c1"), text_reply("<｜｜DSML｜｜ calls>")]
            _, events = self._turn(directory, ScriptedModel(*only_markup), "курс доллара", max_rounds=2)
            answer = next(event for event in events if event["type"] == "answer")["content"]
            self.assertIn("круги кончились", answer)


class FlowCheckTestCase(unittest.TestCase):
    SERVERS = [
        {"key": "gitmcp", "title": "GitMCP", "ok": True},
        {"key": "github", "title": "GitHub", "ok": True},
        {"key": "rates", "title": "Курс", "ok": True},
    ]

    def _call(self, server, tool, round_number, arguments=None, result=None, ok=True):
        return {
            "server": server,
            "tool": tool,
            "name": f"{server}__{tool}",
            "round": round_number,
            "ok": ok,
            "arguments": arguments or {},
            "for_model": json.dumps(result, ensure_ascii=False) if result is not None else "текст",
        }

    def test_expected_servers_come_from_the_words_of_the_request(self):
        from advent.mcp.orchestration import expected_servers

        keys = ["gitmcp", "github", "rates"]
        self.assertEqual(expected_servers(LONG_QUESTION, keys), ["gitmcp", "github"])
        self.assertEqual(expected_servers("А какой сейчас курс доллара?", keys), ["rates"])
        self.assertEqual(expected_servers("Привет!", keys), [])

    def test_extra_and_missing_servers_are_named(self):
        from advent.mcp.orchestration import check_flow

        search = self._call("github", "search_repositories", 1, result={"items": [{"full_name": "a/b"}]})
        rates = self._call("rates", "collect_rate", 2)
        flow = check_flow([search, rates], self.SERVERS, LONG_QUESTION)
        choice = flow["checks"][1]
        self.assertFalse(choice["ok"])
        self.assertIn("лишние: Курс", choice["detail"])
        self.assertIn("не спросили: GitMCP", choice["detail"])
        self.assertEqual(len(flow["checks"]), 2)

    def test_repository_for_gitmcp_must_come_from_github(self):
        from advent.mcp.orchestration import check_flow

        search = self._call("github", "search_repositories", 1, result={"items": [{"full_name": "a/b"}]})
        invented = self._call("gitmcp", "fetch_generic_documentation", 2, {"owner": "x", "repo": "invented"})
        flow = check_flow([search, invented], self.SERVERS, LONG_QUESTION)
        self.assertFalse(flow["checks"][2]["ok"])
        self.assertIn("x/invented", flow["checks"][2]["detail"])

        too_early = self._call("gitmcp", "fetch_generic_documentation", 1, {"owner": "a", "repo": "b"})
        flow = check_flow([too_early, search], self.SERVERS, LONG_QUESTION)
        self.assertFalse(flow["checks"][2]["ok"])

    def test_repository_is_read_from_owner_repo_name_or_url(self):
        from advent.mcp.orchestration import _repo_names

        self.assertEqual(_repo_names({"owner": "a", "repo": "b"}), ["a/b"])
        self.assertEqual(_repo_names({"repoName": "a/b"}), ["a/b"])
        self.assertEqual(_repo_names({"url": "https://raw.githubusercontent.com/a/b/main/README.md"}), ["a/b"])
        self.assertEqual(_repo_names({"url": "https://example.com/x"}), [])
        self.assertEqual(_repo_names({"library": "fastmcp"}), [])

    def test_report_must_follow_the_gitmcp_answer(self):
        from advent.mcp.orchestration import check_flow

        search = self._call("github", "search_repositories", 1, result={"items": [{"full_name": "a/b"}]})
        ask = self._call("gitmcp", "search_generic_documentation", 3, {"owner": "a", "repo": "b", "query": "architecture"})
        early_save = self._call("github", "save_report", 3, result={"file": "r.md"})
        flow = check_flow([search, ask, early_save], self.SERVERS, LONG_QUESTION)
        self.assertFalse(flow["checks"][3]["ok"])

        late_save = self._call("github", "save_report", 4, result={"file": "r.md"})
        flow = check_flow([search, ask, late_save], self.SERVERS, LONG_QUESTION)
        self.assertTrue(flow["ok"], flow["checks"])
        self.assertIn("файл r.md", flow["checks"][3]["detail"])

    def test_simple_turn_gets_only_the_checks_that_apply(self):
        from advent.mcp.orchestration import check_flow

        flow = check_flow([self._call("rates", "rate_summary", 1)], self.SERVERS, "Какой курс доллара?")
        self.assertTrue(flow["ok"], flow["checks"])
        self.assertEqual([check["title"] for check in flow["checks"]], ["Каждый вызов нашёл свой сервер", "Выбраны нужные серверы"])
        self.assertEqual(flow["idle"], ["GitMCP", "GitHub"])

        silent = check_flow([], self.SERVERS, "Привет!")
        self.assertTrue(silent["ok"])
        self.assertIn("модель ответила сама", silent["checks"][0]["detail"])


if __name__ == "__main__":
    unittest.main()

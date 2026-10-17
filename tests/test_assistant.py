import json
import pathlib
import subprocess
import tempfile
import unittest
import unittest.mock

import anyio
from mcp import Client
from mcp.client._memory import InMemoryTransport

from advent import create_app
from advent.rag import HashingEmbedder, RagIndex
from advent.rag.assistant import STRATEGY, build_index, cited, command, context_message, help_turn
from advent.rag.corpus import Document
from advent.rag.index import Hit
from advent.mcp.servers.git import GitError, Repo, build_server, safe_path


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def make_repo(root):
    """Временный репозиторий: main с двумя файлами, ветка day-2 с коммитом и правками поверх."""
    root = pathlib.Path(root)
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("# Проект\n", encoding="utf-8")
    (root / "app").mkdir()
    (root / "app" / "core.py").write_text("x = 1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "начало")
    git(root, "checkout", "-q", "-b", "day-2")
    (root / "app" / "core.py").write_text("x = 2\n", encoding="utf-8")
    git(root, "commit", "-qam", "день 2")
    (root / "README.md").write_text("# Проект\n\nНовая строка\n", encoding="utf-8")
    (root / "app" / "new.py").write_text("y = 1\n", encoding="utf-8")
    return root


def call(server, name, arguments=None):
    async def run():
        async with Client(InMemoryTransport(server)) as client:
            result = await client.call_tool(name, arguments or {})
            text = "\n".join(block.text for block in result.content if getattr(block, "type", None) == "text")
            return bool(result.is_error), text

    error, text = anyio.run(run)
    return error, (text if error else json.loads(text))


class GitServerTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = make_repo(directory.name)
        self.server = build_server(Repo(self.root))

    def test_branch_knows_head_changes_and_distance_from_main(self):
        error, data = call(self.server, "git_branch")

        self.assertFalse(error)
        self.assertEqual(data["branch"], "day-2")
        self.assertEqual(data["head"]["subject"], "день 2")
        self.assertEqual(data["uncommitted_files"], 2)
        self.assertEqual((data["base"], data["ahead_of_base"], data["behind_base"]), ("main", 1, 0))

    def test_status_lists_changed_and_untracked_files(self):
        _, data = call(self.server, "git_status")

        self.assertEqual({(item["path"], item["state"]) for item in data["files"]}, {("README.md", "изменён"), ("app/new.py", "не в git")})

    def test_diff_of_working_copy_and_of_branch(self):
        _, working = call(self.server, "git_diff")
        _, branch = call(self.server, "git_diff", {"scope": "branch"})
        _, one = call(self.server, "git_diff", {"path": "app"})

        self.assertEqual(working["files"], [{"path": "README.md", "added": 2, "removed": 0}])
        self.assertIn("+Новая строка", working["diff"])
        self.assertEqual(working["untracked"], ["app/new.py"])
        self.assertEqual(branch["files"], [{"path": "app/core.py", "added": 1, "removed": 1}])
        self.assertEqual(one["files"], [])
        self.assertEqual(one["untracked"], ["app/new.py"])

    def test_log_and_files(self):
        _, log = call(self.server, "git_log", {"limit": 5})
        _, files = call(self.server, "list_files", {"pattern": "*.py"})

        self.assertEqual([item["subject"] for item in log["commits"]], ["день 2", "начало"])
        self.assertEqual(files["files"], ["app/core.py"])
        self.assertEqual(files["folders"], {"app/": 1})

    def test_paths_cannot_leave_the_project_or_become_flags(self):
        for bad in ("../secret", "/etc/passwd", "--output=x"):
            with self.assertRaises(GitError):
                safe_path(bad)
        error, text = call(self.server, "git_diff", {"path": "../x"})
        self.assertTrue(error)
        self.assertIn("Недопустимый путь", text)


class ScriptedModel:
    def __init__(self, *replies):
        self.payloads = []
        self.replies = list(replies)

    def send(self, payload):
        self.payloads.append(payload)
        message = self.replies.pop(0)
        finish = "tool_calls" if message.get("tool_calls") else "stop"
        return {"choices": [{"finish_reason": finish, "message": message}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}


def tool_call(name, arguments=None):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments or {})}}]}


DOCS = [
    Document("README.md", "doc", "Проект", "# Проект\n\n## Память\n\nПамять агента хранится в JSON-файлах, три слоя: chat, task, user.\n"),
    Document("docs/architecture.md", "doc", "Архитектура", "# Архитектура\n\n## Слои\n\nКаждый день — отдельный blueprint в advent/days.\n"),
]


class AssistantTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = pathlib.Path(directory.name)
        self.root = make_repo(self.folder / "repo")
        server = build_server(Repo(self.root))
        self.factory = lambda spec, errlog: InMemoryTransport(server)
        self.index = RagIndex(self.folder / "index.sqlite3")
        self.embedder = HashingEmbedder()
        self.index.build(DOCS, self.embedder, strategies=(STRATEGY,))

    def test_context_numbers_chunks_and_citations_are_checked(self):
        hits = self.index.search("память", self.embedder, STRATEGY, k=2)
        message = context_message("Где память?", hits)

        self.assertIn("[1] ", message)
        self.assertTrue(message.endswith("Вопрос: Где память?"))
        self.assertEqual(cited("см. [2] и [1], ещё [1] и [9]", 2), [1, 2])

    def test_help_retrieves_docs_then_agent_asks_git(self):
        model = ScriptedModel(tool_call("git_branch"), {"role": "assistant", "content": "Вы на ветке `day-2`, память — в JSON [1]."})
        events = list(help_turn("Над чем я работаю и где память?", index=self.index, embedder=self.embedder, transport=model, factory=self.factory))
        kinds = [event["type"] for event in events]

        self.assertEqual(kinds[0], "retrieved")
        self.assertEqual(len(events[0]["hits"]), 2)
        self.assertIn("connected", kinds)
        result = next(event for event in events if event["type"] == "tool_result")
        self.assertTrue(result["ok"])
        self.assertIn("day-2", result["preview"])
        answer = next(event for event in events if event["type"] == "answer")
        self.assertEqual(answer["cited"], [1])
        self.assertEqual(kinds[-1], "done")
        first = model.payloads[0]
        self.assertIn("ассистент разработчика", first["messages"][0]["content"])
        self.assertIn("Фрагменты документации и кода проекта", first["messages"][1]["content"])
        self.assertEqual({spec["function"]["name"] for spec in first["tools"]}, {"git_branch", "git_status", "git_diff", "git_log", "list_files"})

    def test_commands_answer_from_git_without_a_model(self):
        branch = command("/branch", factory=self.factory)
        log = command("/log", factory=self.factory)

        self.assertTrue(branch["ok"])
        self.assertEqual(branch["data"]["branch"], "day-2")
        self.assertEqual(len(log["data"]["commits"]), 2)
        with self.assertRaisesRegex(ValueError, "Нет такой команды"):
            command("/push", factory=self.factory)

    def test_build_index_uses_only_structure(self):
        index = RagIndex(self.folder / "fresh.sqlite3")
        with unittest.mock.patch("advent.rag.assistant.collect", return_value=DOCS):
            info = build_index(index, self.embedder, lambda event: None)

        self.assertEqual(list(info["strategies"]), ["structure"])


class DayThirtyOneTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        folder = pathlib.Path(directory.name)
        root = make_repo(folder / "repo")
        server = build_server(Repo(root))
        self.model = ScriptedModel({"role": "assistant", "content": "Каждый день — blueprint [1]."})
        self.app = create_app({
            "TESTING": True,
            "AGENT_STORE_DIR": str(folder / "agents"),
            "DAY31_INDEX_PATH": str(folder / "day31" / "index.sqlite3"),
            "DAY31_MCP_FACTORY": lambda spec, errlog: InMemoryTransport(server),
            "RAG_EMBEDDER": HashingEmbedder(),
            "AGENT_TRANSPORT": self.model,
        })
        self.client = self.app.test_client()

    def stream(self, path, body=None):
        response = self.client.post(path, json=body)
        return response, [json.loads(line) for line in response.get_data(as_text=True).splitlines() if line]

    def test_page_asks_to_build_index_first(self):
        html = self.client.get("/days/31/").get_data(as_text=True)

        self.assertIn("Собрать индекс", html)
        self.assertIn("js/day31.js", html)
        self.assertIn('data-run="/branch"', html)
        self.assertIn("Над чем я сейчас работаю?", html)
        response = self.client.post("/days/31/api/help", json={"question": "Как устроен проект?"})
        self.assertEqual(response.status_code, 409)

    def test_index_is_built_and_help_answers_with_sources(self):
        with unittest.mock.patch("advent.rag.assistant.collect", return_value=DOCS):
            _, events = self.stream("/days/31/api/index")
        state = events[-1]
        self.assertEqual(state["type"], "state")
        self.assertTrue(state["ready"])
        self.assertEqual(state["docs"], ["README.md", "docs/architecture.md"])
        self.assertIn("Пересобрать индекс", self.client.get("/days/31/").get_data(as_text=True))

        response, events = self.stream("/days/31/api/help", {"question": "Как устроен проект?"})
        self.assertEqual(response.mimetype, "application/x-ndjson")
        self.assertEqual(events[0]["type"], "retrieved")
        answer = next(event for event in events if event["type"] == "answer")
        self.assertEqual(answer["cited"], [1])

    def test_help_and_command_validation(self):
        self.assertEqual(self.client.post("/days/31/api/help", json={"question": " "}).status_code, 400)
        self.assertEqual(self.client.post("/days/31/api/help", json={"question": "x" * 501}).status_code, 400)
        self.assertEqual(self.client.post("/days/31/api/command", json={"command": "/rm"}).status_code, 400)

        response = self.client.post("/days/31/api/command", json={"command": "/files"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["count"], 2)


if __name__ == "__main__":
    unittest.main()

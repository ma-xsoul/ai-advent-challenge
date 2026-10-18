import json
import pathlib
import tempfile
import unittest

from mcp.client._memory import InMemoryTransport

from advent import create_app
from advent.mcp.agent import FINAL_ROUND
from advent.mcp.files_agent import MAX_ROUNDS, SCENARIOS, run_goal
from advent.mcp.servers.files import READ_CHARS, Workspace, WorkspaceError, build_server


def make_project(root):
    root = pathlib.Path(root)
    (root / "advent" / "days" / "day01").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "data").mkdir()
    (root / ".git").mkdir()
    (root / "README.md").write_text("# Проект\n\n## Day 1\n\nПервый день.\n", encoding="utf-8")
    (root / "docs" / "api.md").write_text("# API\n\n| День | Адрес |\n|---|---|\n| 1 | `POST /days/1/api/chat` |\n", encoding="utf-8")
    (root / "advent" / "days" / "day01" / "routes.py").write_text("from ..rag_pages import ndjson_stream\n\ndef ask():\n    return ndjson_stream(work)\n", encoding="utf-8")
    (root / "advent" / "rag_pages.py").write_text("def ndjson_stream(work):\n    return work\n" + "\n".join(f"# строка {n} " + "x" * 80 for n in range(200)) + "\n", encoding="utf-8")
    (root / ".env").write_text("DEEPSEEK_API_KEY=secret\n", encoding="utf-8")
    (root / "data" / "keys.json").write_text("{}", encoding="utf-8")
    (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    return root


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = make_project(pathlib.Path(directory.name) / "project")
        self.workspace = Workspace(self.root, pathlib.Path(directory.name) / "draft")

    def test_only_project_text_files_are_visible(self):
        files = self.workspace.list()["files"]

        self.assertEqual(files, ["README.md", "advent/days/day01/routes.py", "advent/rag_pages.py", "docs/api.md"])
        for hidden in (".env", "data/keys.json", ".git/config", "../etc/passwd", "HANDOFF.md", "~/x"):
            with self.assertRaises(WorkspaceError):
                self.workspace.read(hidden)

    def test_search_finds_lines_across_files(self):
        found = self.workspace.search("ndjson_stream")
        regex = self.workspace.search(r"def \w+\(", regex=True, glob="*.py")

        self.assertEqual(found["files"], 2)
        self.assertEqual({(item["file"], item["line"]) for item in found["matches"]},
                         {("advent/days/day01/routes.py", 1), ("advent/days/day01/routes.py", 4), ("advent/rag_pages.py", 1)})
        self.assertEqual(regex["total"], 2)
        with self.assertRaises(WorkspaceError):
            self.workspace.search("(", regex=True)

    def test_read_is_numbered_and_capped_by_characters(self):
        part = self.workspace.read("advent/rag_pages.py")

        self.assertTrue(part["text"].startswith("   1 def ndjson_stream(work):"))
        self.assertLessEqual(len(part["text"]), READ_CHARS + 120)
        self.assertEqual(part["next_start"], part["end"] + 1)
        tail = self.workspace.read("advent/rag_pages.py", part["next_start"])
        self.assertEqual(tail["start"], part["end"] + 1)

    def test_writes_go_to_draft_and_only_into_docs_and_root_markdown(self):
        with self.assertRaisesRegex(WorkspaceError, "только в docs"):
            self.workspace.write("advent/new.py", "x = 1")
        with self.assertRaisesRegex(WorkspaceError, "только в docs"):
            self.workspace.write("docs/script.py", "x = 1")
        self.workspace.write("CHANGELOG.md", "# Changelog")
        self.workspace.edit("docs/api.md", "| 1 | `POST /days/1/api/chat` |", "| 1 | `POST /days/1/api/chat` |\n| 2 | `POST /days/2/api/compare` |")

        self.assertFalse((self.root / "CHANGELOG.md").exists())
        self.assertNotIn("days/2", (self.root / "docs" / "api.md").read_text(encoding="utf-8"))
        self.assertIn("days/2", self.workspace.read("docs/api.md")["text"])
        self.assertTrue(self.workspace.read("docs/api.md")["in_draft"])
        self.assertIn("CHANGELOG.md", self.workspace.list()["in_draft"])
        changes = {item["path"]: item for item in self.workspace.changes()}
        self.assertTrue(changes["CHANGELOG.md"]["new"])
        self.assertEqual((changes["docs/api.md"]["added"], changes["docs/api.md"]["removed"]), (1, 0))
        self.assertIn("+| 2 | `POST /days/2/api/compare` |", changes["docs/api.md"]["diff"])

    def test_edit_needs_a_unique_fragment(self):
        with self.assertRaisesRegex(WorkspaceError, "встречается 0 раз"):
            self.workspace.edit("docs/api.md", "нет такого", "x")
        with self.assertRaisesRegex(WorkspaceError, "встречается 2 раз"):
            self.workspace.edit("docs/api.md", "---", "x")
        with self.assertRaisesRegex(WorkspaceError, "write_file"):
            self.workspace.edit("docs/new.md", "a", "b")

    def test_apply_and_discard(self):
        self.workspace.write("CHANGELOG.md", "# Changelog")
        self.workspace.write("docs/adr.md", "# ADR")

        self.assertEqual(self.workspace.apply(["CHANGELOG.md"]), ["CHANGELOG.md"])
        self.assertEqual((self.root / "CHANGELOG.md").read_text(encoding="utf-8"), "# Changelog\n")
        self.assertEqual([item["path"] for item in self.workspace.changes()], ["docs/adr.md"])
        self.assertEqual(self.workspace.discard(), 1)
        self.assertEqual(self.workspace.changes(), [])
        self.assertFalse((self.root / "docs" / "adr.md").exists())


class ScriptedModel:
    def __init__(self, *replies):
        self.payloads = []
        self.replies = list(replies)

    def send(self, payload):
        self.payloads.append(payload)
        message = self.replies.pop(0) if self.replies else {"role": "assistant", "content": ""}
        finish = "tool_calls" if message.get("tool_calls") else "stop"
        return {"choices": [{"finish_reason": finish, "message": message}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}


def tool_call(name, arguments, call_id="call_1"):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]}


REPORT = {"role": "assistant", "content": "## Отчёт\n\nНашёл `ndjson_stream` в двух файлах и подготовил CHANGELOG.md."}


class FilesAgentTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = make_project(pathlib.Path(directory.name) / "project")
        self.workspace = Workspace(root, pathlib.Path(directory.name) / "draft")
        server = build_server(self.workspace)
        self.factory = lambda spec, errlog: InMemoryTransport(server)

    def test_goal_run_searches_reads_writes_draft_and_records_run(self):
        self.workspace.write("docs/old.md", "# Старый черновик")
        model = ScriptedModel(
            tool_call("search", {"query": "ndjson_stream"}),
            tool_call("read_file", {"path": "advent/days/day01/routes.py"}),
            tool_call("write_file", {"path": "CHANGELOG.md", "content": "# Changelog\n\n- день 1"}),
            REPORT,
        )
        events = list(run_goal("Найди ndjson_stream и составь changelog", workspace=self.workspace, transport=model, factory=self.factory))
        kinds = [event["type"] for event in events]

        self.assertEqual(kinds[0], "start")
        self.assertEqual(events[0]["discarded"], 1)
        self.assertEqual(kinds[-2:], ["changes", "run"])
        self.assertEqual([item["path"] for item in events[-2]["files"]], ["CHANGELOG.md"])
        run = events[-1]["run"]
        self.assertEqual([call["name"] for call in run["calls"]], ["search", "read_file", "write_file"])
        self.assertEqual(run["files_read"], ["advent/days/day01/routes.py"])
        self.assertEqual(run["answer"], REPORT["content"])
        self.assertIn("+- день 1", run["diff"])
        self.assertEqual(run["stats"]["tool_calls"], 3)
        self.assertEqual(model.payloads[0]["temperature"], 0.0)

    def test_last_round_tells_the_model_to_answer_now(self):
        replies = [tool_call("list_files", {}, call_id=f"c{n}") for n in range(MAX_ROUNDS)]
        model = ScriptedModel(*replies)
        list(run_goal("Зациклись", workspace=self.workspace, transport=model, factory=self.factory))

        self.assertEqual(len(model.payloads), MAX_ROUNDS)
        self.assertEqual(model.payloads[-1]["tool_choice"], "none")
        self.assertEqual(model.payloads[-1]["messages"][-1], {"role": "user", "content": FINAL_ROUND})
        self.assertNotEqual(model.payloads[-2]["messages"][-1].get("content"), FINAL_ROUND)


class DayThirtyFourTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        folder = pathlib.Path(directory.name)
        self.root = make_project(folder / "project")
        draft = folder / "draft"
        server = build_server(Workspace(self.root, draft))
        self.model = ScriptedModel(tool_call("write_file", {"path": "CHANGELOG.md", "content": "# Changelog"}), REPORT)
        self.app = create_app({
            "TESTING": True,
            "AGENT_STORE_DIR": str(folder / "agents"),
            "DAY34_ROOT": str(self.root),
            "DAY34_DRAFT": str(draft),
            "DAY34_RUNS_PATH": str(folder / "runs.json"),
            "DAY34_MCP_FACTORY": lambda spec, errlog: InMemoryTransport(server),
            "AGENT_TRANSPORT": self.model,
        })
        self.client = self.app.test_client()

    def test_page_offers_four_scenarios(self):
        html = self.client.get("/days/34/").get_data(as_text=True)

        self.assertEqual(html.count('class="day34-scenario"'), len(SCENARIOS))
        self.assertIn("Проверить правила проекта", html)
        self.assertIn("js/day34.js", html)
        self.assertIn("Черновик пуст", html)

    def test_run_then_apply_and_runs_are_saved(self):
        response = self.client.post("/days/34/api/run", json={"goal": "Сгенерируй CHANGELOG.md"})
        events = [json.loads(line) for line in response.get_data(as_text=True).splitlines() if line]
        run = next(event for event in events if event["type"] == "run")["run"]

        self.assertEqual(run["changes"][0]["path"], "CHANGELOG.md")
        self.assertNotIn("diff", run)
        self.assertEqual(self.client.get("/days/34/api/changes").get_json()["files"][0]["path"], "CHANGELOG.md")
        self.assertEqual(self.client.get("/days/34/api/runs").get_json()[0]["goal"], "Сгенерируй CHANGELOG.md")
        detail = self.client.get(f"/days/34/api/runs/{run['id']}").get_json()
        self.assertEqual(detail["calls"][0]["name"], "write_file")

        applied = self.client.post("/days/34/api/changes/apply", json={}).get_json()
        self.assertEqual(applied["applied"], ["CHANGELOG.md"])
        self.assertEqual((self.root / "CHANGELOG.md").read_text(encoding="utf-8"), "# Changelog\n")
        self.assertEqual(self.client.post("/days/34/api/changes/discard").get_json()["discarded"], 0)

    def test_validation(self):
        self.assertEqual(self.client.post("/days/34/api/run", json={"goal": " "}).status_code, 400)
        self.assertEqual(self.client.post("/days/34/api/run", json={"goal": "x" * 1501}).status_code, 400)
        self.assertEqual(self.client.post("/days/34/api/changes/apply", json={"paths": "README.md"}).status_code, 400)
        self.assertEqual(self.client.get("/days/34/api/runs/nope").status_code, 404)


if __name__ == "__main__":
    unittest.main()

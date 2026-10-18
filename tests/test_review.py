import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from advent import create_app
from advent.rag.corpus import Document
from advent.rag.chunking import chunk
from advent.rag.review import (
    MARKER, LexicalRetriever, ReviewError, collect_diff, generate, queries, retrieve, run, to_markdown,
)


ROOT = pathlib.Path(__file__).resolve().parents[1]


def git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def make_repo(root):
    """main: README, docs и модуль; ветка feature: новый модуль, правка старого, тест, картинка."""
    root = pathlib.Path(root)
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("# Проект\n\n## Тесты\n\nТесты пишут во временные папки, без сети.\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "architecture.md").write_text("# Архитектура\n\n## Конфиг\n\nЗависимости берутся из app.config, чтобы тесты подменяли их.\n", encoding="utf-8")
    (root / "advent").mkdir()
    (root / "advent" / "store.py").write_text('"""Хранилище ключей на диске."""\n\ndef load_keys(path):\n    return open(path).read()\n', encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "начало")
    git(root, "checkout", "-q", "-b", "feature")
    (root / "advent" / "store.py").write_text('"""Хранилище ключей на диске."""\n\ndef load_keys(path):\n    with open(path) as f:\n        return f.read()\n', encoding="utf-8")
    (root / "advent" / "limits.py").write_text('"""Лимиты запросов."""\n\nclass RateLimiter:\n    def hit(self, client):\n        return None\n', encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_limits.py").write_text("def test_hit():\n    assert True\n", encoding="utf-8")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "лимиты")
    git(root, "checkout", "-q", "main")
    return root


REVIEW = {
    "summary": "PR добавляет лимитер.",
    "bugs": [{"file": "advent/limits.py", "line": 4, "title": "hit всегда пропускает", "why": "нет счётчика", "fix": "считать запросы", "severity": "high", "sources": [1, 99]}],
    "architecture": [{"file": "advent/limits.py", "line": "x", "title": "лимит не из app.config", "why": "", "fix": "", "severity": "???", "sources": []}],
    "recommendations": [{"title": ""}],
}


class Model:
    def __init__(self, content=None):
        self.content = json.dumps(REVIEW, ensure_ascii=False) if content is None else content
        self.payloads = []

    def send(self, payload):
        self.payloads.append(payload)
        return {"choices": [{"finish_reason": "stop", "message": {"content": self.content}}], "usage": {"prompt_tokens": 900, "completion_tokens": 100}}


class ReviewCoreTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = make_repo(pathlib.Path(directory.name) / "repo")

    def test_diff_takes_only_branch_changes_in_priority_order(self):
        review = collect_diff("main", "feature", root=self.root)

        self.assertEqual([item.path for item in review.files], ["advent/limits.py", "advent/store.py", "tests/test_limits.py", "logo.png"])
        self.assertEqual(review.included, ["advent/limits.py", "advent/store.py", "tests/test_limits.py"])
        self.assertEqual(review.skipped, ["logo.png"])
        self.assertEqual(review.commits[0].split(" ", 1)[1], "лимиты")
        limits = review.files[0]
        self.assertEqual((limits.status, limits.added, limits.removed), ("A", 5, 0))
        self.assertIn("   3 class RateLimiter:", limits.content)
        self.assertIn("+    with open(path) as f:", review.files[1].diff)

    def test_diff_errors(self):
        with self.assertRaisesRegex(ReviewError, "нет изменений"):
            collect_diff("main", "main", root=self.root)
        with self.assertRaisesRegex(ReviewError, "Недопустимое"):
            collect_diff("--output=x", "feature", root=self.root)
        with self.assertRaises(ReviewError):
            collect_diff("main", "nope", root=self.root)

    def test_queries_name_new_functions_and_classes(self):
        asked = queries(collect_diff("main", "feature", root=self.root))

        self.assertTrue(asked[0].startswith("advent/limits.py limits"))
        self.assertIn("RateLimiter", asked[0])
        self.assertIn("hit", asked[0])

    def test_lexical_search_and_retrieve_skip_changed_code_but_keep_docs(self):
        documents = [
            Document("README.md", "doc", "Проект", "# Проект\n\n## Тесты\n\nТесты пишут во временные папки, без сети.\n"),
            Document("docs/architecture.md", "doc", "Архитектура", "# Архитектура\n\n## Конфиг\n\nЗависимости берутся из app.config.\n"),
            Document("advent/limits.py", "code", "limits", "class RateLimiter:\n    def hit(self, client):\n        return None\n"),
            Document("advent/service.py", "code", "service", "from .limits import RateLimiter\n\ndef admit(client):\n    return RateLimiter().hit(client)\n"),
        ]
        retriever = LexicalRetriever(chunk(documents, "structure"))

        self.assertEqual(retriever.search("RateLimiter hit", 1)[0]["source"], "advent/limits.py")
        self.assertTrue(all(card["kind"] == "doc" for card in retriever.search("тесты config", 5, docs_only=True)))
        asked, sources = retrieve(collect_diff("main", "feature", root=self.root), retriever)
        found = [card["source"] for card in sources]
        self.assertNotIn("advent/limits.py", found)
        self.assertIn("advent/service.py", found)
        self.assertGreaterEqual(sum(card["kind"] == "doc" for card in sources), 2)
        self.assertEqual([card["n"] for card in sources], list(range(1, len(sources) + 1)))

    def test_generate_cleans_items_and_rejects_non_json(self):
        review = collect_diff("main", "feature", root=self.root)
        model = Model()
        result = generate(review, [{"n": 1, "source": "README.md", "section": "Тесты", "lines": "1–5", "text": "Тесты во временных папках"}], model)

        self.assertEqual(result["summary"], "PR добавляет лимитер.")
        self.assertEqual(result["bugs"][0]["sources"], [1])
        self.assertEqual(result["architecture"][0]["line"], 0)
        self.assertEqual(result["architecture"][0]["severity"], "low")
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["stats"]["prompt_tokens"], 900)
        payload = model.payloads[0]
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertIn("Не вошли в ревью", payload["messages"][1]["content"])
        self.assertIn("[1] README.md", payload["messages"][1]["content"])
        with self.assertRaisesRegex(ReviewError, "не JSON"):
            generate(review, [], Model("извините"))

    def test_run_emits_steps_and_markdown_has_sections_and_links(self):
        events = []
        retriever = LexicalRetriever(chunk([Document("README.md", "doc", "Проект", "# Проект\n\n## Тесты\n\nТесты без сети.\n")], "structure"))
        result = run("main", "feature", transport=Model(), retriever=retriever, root=self.root, emit=events.append)
        text = to_markdown(result, link=lambda file, line: f"https://x/{file}#L{line}")

        self.assertEqual([event["type"] for event in events], ["diff", "retrieved", "review"])
        self.assertTrue(text.startswith(MARKER))
        self.assertIn("### Потенциальные баги · 1", text)
        self.assertIn("[`advent/limits.py:4`](https://x/advent/limits.py#L4)", text)
        self.assertIn("### Рекомендации · 0\nНе нашёл.", text)
        self.assertIn("не вошли: logo.png", text)


class WorkflowAndScriptTest(unittest.TestCase):
    def test_workflow_runs_review_on_pull_requests_and_comments(self):
        text = (ROOT / ".github" / "workflows" / "ai-review.yml").read_text(encoding="utf-8")

        self.assertIn("pull_request:", text)
        self.assertIn("pull-requests: write", text)
        self.assertIn("fetch-depth: 0", text)
        self.assertIn("secrets.DEEPSEEK_API_KEY", text)
        self.assertIn("python scripts/ai_review.py --base \"origin/${{ github.base_ref }}\"", text)
        self.assertIn("<!-- ai-review -->", text)

    def test_script_without_key_writes_a_comment_about_the_secret(self):
        spec = importlib.util.spec_from_file_location("ai_review", ROOT / "scripts" / "ai_review.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "review.md"
            env = {key: value for key, value in os.environ.items() if key not in ("DEEPSEEK_API_KEY", "GITHUB_STEP_SUMMARY")}
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(module, "load_dotenv"), \
                    mock.patch("sys.argv", ["ai_review.py", "--base", "main", "--output", str(output)]), mock.patch("builtins.print"):
                self.assertEqual(module.main(), 0)
            body = output.read_text(encoding="utf-8")
        self.assertTrue(body.startswith(MARKER))
        self.assertIn("DEEPSEEK_API_KEY", body)


class FakeHttp:
    def __init__(self, status=200, body=None):
        self.status, self.body, self.calls = status, body, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        return mock.Mock(status_code=self.status, json=lambda: self.body)


class DayThirtyTwoTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        folder = pathlib.Path(directory.name)
        root = make_repo(folder / "repo")
        git(root, "branch", "day-1", "main")
        git(root, "branch", "day-2", "feature")
        (root / ".github" / "workflows").mkdir(parents=True)
        (root / ".github" / "workflows" / "ai-review.yml").write_text("name: AI review\non:\n  pull_request:\n", encoding="utf-8")
        self.http = FakeHttp(body={"workflow_runs": [
            {"name": "AI review", "run_number": 3, "status": "completed", "conclusion": "success", "display_title": "День 2",
             "head_branch": "day-2", "pull_requests": [{"number": 7}], "html_url": "https://github.com/x/y/actions/runs/1", "created_at": "2026-10-17T20:00:00Z"},
            {"name": "CI", "run_number": 9},
        ]})
        self.app = create_app({
            "TESTING": True,
            "AGENT_STORE_DIR": str(folder / "agents"),
            "DAY31_INDEX_PATH": str(folder / "day31" / "index.sqlite3"),
            "DAY32_ROOT": str(root),
            "DAY32_REPO": "x/y",
            "DAY32_HTTP": self.http,
            "AGENT_TRANSPORT": Model(),
        })
        self.client = self.app.test_client()

    def test_page_offers_branches_with_latest_day_first(self):
        html = self.client.get("/days/32/").get_data(as_text=True)

        self.assertIn("<option selected>day-2</option>", html)
        self.assertIn("<option selected>day-1</option>", html)
        self.assertIn("name: AI review", html)
        self.assertIn("x/y · pull requests", html)
        self.assertIn("(нет индекса дня 31)", html)

    def test_review_validation(self):
        cases = [
            ({"base": "day-1", "head": "nope"}, 400),
            ({"base": "day-1", "head": "day-1"}, 400),
            ({"base": "day-1", "head": "day-2", "retriever": "magic"}, 400),
            ({"base": "day-1", "head": "day-2", "retriever": "vector"}, 409),
        ]
        for body, status in cases:
            self.assertEqual(self.client.post("/days/32/api/review", json=body).status_code, status, body)

    def test_review_streams_steps_and_comment(self):
        response = self.client.post("/days/32/api/review", json={"base": "day-1", "head": "day-2"})
        events = [json.loads(line) for line in response.get_data(as_text=True).splitlines() if line]

        self.assertEqual([event["type"] for event in events], ["diff", "retrieved", "review", "markdown", "done"])
        self.assertIn("https://github.com/x/y/blob/day-2/advent/limits.py#L4", events[3]["text"])

    def test_runs_come_from_github_and_keep_only_this_workflow(self):
        data = self.client.get("/days/32/api/runs").get_json()

        self.assertEqual(self.http.calls, ["https://api.github.com/repos/x/y/actions/runs"])
        self.assertEqual(len(data["runs"]), 1)
        self.assertEqual(data["runs"][0]["pr"], 7)
        self.app.config["DAY32_HTTP"] = FakeHttp(status=403, body={})
        self.assertEqual(self.client.get("/days/32/api/runs").status_code, 502)


if __name__ == "__main__":
    unittest.main()

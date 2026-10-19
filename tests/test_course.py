import json
import pathlib
import random
import tempfile
import unittest

from advent import create_app
from advent.course.sources import chat_sources, collect, page_sources, readme_sources, talk_sources
from advent.course.tutor import CourseError, CourseStore, ask, build, grade, pick
from advent.rag import HashingEmbedder


CHAT = """<html><body>
<div class="message default clearfix" id="message1"><div class="body"><div class="pull_right date details" title="01.10.2026 14:00:00 UTC+03:00">14:00</div>
<div class="from_name">Mobile Developer Manager</div><div class="text">🔥 День 1. Первый запрос<br>Сделайте запрос к LLM</div></div></div>
<div class="message default clearfix" id="message2"><div class="body"><div class="pull_right date details" title="01.10.2026 14:05:00 UTC+03:00">14:05</div>
<div class="from_name">Алексей Гладков</div><div class="text">Модель можно брать любую, главное понять API</div></div></div>
<div class="message default clearfix joined" id="message3"><div class="body"><div class="pull_right date details" title="01.10.2026 14:06:00 UTC+03:00">14:06</div>
<div class="text">И обязательно покажите ответ в интерфейсе</div></div></div>
<div class="message default clearfix" id="message4"><div class="body"><div class="pull_right date details" title="01.10.2026 15:00:00 UTC+03:00">15:00</div>
<div class="from_name">Другой участник</div><div class="text">А можно на Python? Мой личный вопрос не должен попасть в курс</div></div></div>
<div class="message default clearfix" id="message5"><div class="body"><div class="pull_right date details" title="02.10.2026 12:00:00 UTC+03:00">12:00</div>
<div class="from_name">AI Advent Alumni Bot</div><div class="text">📢 Что обсуждали: задание 1 и выбор модели</div></div></div>
<div class="message default clearfix" id="message6"><div class="body"><div class="pull_right date details" title="02.10.2026 14:00:00 UTC+03:00">14:00</div>
<div class="from_name">Mobile Developer Manager</div><div class="text">🔥 День 2. Формат ответа</div></div></div>
</body></html>"""


def talk_lines():
    rows = [
        {"type": "user", "gitBranch": "day-1", "timestamp": "2026-10-01T10:00:00Z", "message": {"role": "user", "content": "Объясни, зачем нужен этот день?"}},
        {"type": "assistant", "gitBranch": "day-1", "message": {"role": "assistant", "content": [{"type": "text", "text": "Чтобы научиться делать первый запрос."}]}},
        {"type": "user", "gitBranch": "day-1", "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
        {"type": "assistant", "gitBranch": "day-1", "message": {"role": "assistant", "content": [{"type": "text", "text": "И показать ответ."}]}},
        {"type": "user", "gitBranch": "day-1", "message": {"role": "user", "content": "коммит"}},
        {"type": "assistant", "gitBranch": "day-1", "message": {"role": "assistant", "content": [{"type": "text", "text": "Готово."}]}},
        {"type": "user", "gitBranch": "main", "message": {"role": "user", "content": "Почему небо голубое и при чём тут курс?"}},
        {"type": "assistant", "gitBranch": "main", "message": {"role": "assistant", "content": [{"type": "text", "text": "Не про курс."}]}},
    ]
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\nне json\n"


def make_root(root):
    root = pathlib.Path(root)
    days = root / "advent" / "templates" / "days"
    days.mkdir(parents=True)
    (days / "day01.html").write_text("{% block content %}<h1>День 1. Первый запрос к LLM</h1><p>Сделайте запрос к LLM через API.</p>{% endblock %}", encoding="utf-8")
    (days / "day02.html").write_text("<h1>День 2. Формат ответа</h1><p>JSON-ответ модели.</p>", encoding="utf-8")
    (root / "README.md").write_text("# Проект\n\n- Day 1 — первый запрос.\n  Страница с формой.\n- Day 2 — формат ответа.\n\n## Дальше\n", encoding="utf-8")
    return root


CHAPTER = {
    "title": "Первый запрос",
    "summary": "Сделали первый запрос к модели и показали ответ.",
    "ideas": ["LLM вызывается по HTTP API", "Ответ нужно показать"],
    "built": ["Страница дня 1"],
    "curator": ["Модель любая"],
    "questions": [{"q": "Зачем показывать ответ в интерфейсе?", "answer": "Чтобы видеть, что модель ответила."}, {"q": "Как вызвать модель?", "answer": "Через HTTP API."}, {"q": "", "answer": "пустой вопрос выбрасывается"}],
}


class Model:
    def __init__(self):
        self.payloads = []

    def send(self, payload):
        self.payloads.append(payload)
        system = payload["messages"][0]["content"]
        if "конспект" in system:
            content = json.dumps(CHAPTER, ensure_ascii=False)
        elif "проверяешь ответ" in system:
            content = json.dumps({"verdict": "almost", "feedback": "Близко.", "hint": "Скажи про API."}, ensure_ascii=False)
        else:
            content = "Модель вызывают через HTTP API [1]."
        return {"choices": [{"message": {"content": content}}], "usage": {}}


class SourcesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = pathlib.Path(directory.name)
        self.root = make_root(self.folder / "project")
        (self.folder / "messages.html").write_text(CHAT, encoding="utf-8")
        (self.folder / "talk.jsonl").write_text(talk_lines(), encoding="utf-8")

    def test_pages_and_readme_paragraphs(self):
        pages = page_sources(self.root)
        readme = readme_sources(self.root)

        self.assertEqual([source.day for source in pages], [1, 2])
        self.assertIn("Сделайте запрос к LLM через API.", pages[0].text)
        self.assertNotIn("{%", pages[0].text)
        self.assertEqual([(source.day, source.text) for source in readme], [(1, "первый запрос. Страница с формой."), (2, "формат ответа.")])

    def test_chat_keeps_posts_curator_and_summaries_but_not_other_people(self):
        sources = chat_sources([self.folder / "messages.html"])
        texts = " | ".join(source.text for source in sources)

        self.assertEqual([(source.day, source.kind) for source in sources], [(1, "task"), (2, "task"), (1, "chat"), (1, "chat")])
        self.assertIn("Модель можно брать любую, главное понять API И обязательно покажите ответ в интерфейсе", texts)
        self.assertIn("Что обсуждали: задание 1", texts)
        self.assertNotIn("личный вопрос", texts)

    def test_talks_pair_questions_with_answers_by_branch(self):
        sources = talk_sources([self.folder / "talk.jsonl"])

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].day, 1)
        self.assertIn("Вопрос: Объясни, зачем нужен этот день?", sources[0].text)
        self.assertIn("Чтобы научиться делать первый запрос.\nИ показать ответ.", sources[0].text)
        self.assertNotIn("Готово", sources[0].text)


class TutorTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        folder = pathlib.Path(directory.name)
        root = make_root(folder / "project")
        (folder / "messages.html").write_text(CHAT, encoding="utf-8")
        self.sources = collect(root=root, chat=[folder / "messages.html"], talks=[])
        self.store = CourseStore(folder / "day35")
        self.embedder = HashingEmbedder()
        self.model = Model()

    def test_build_writes_chapters_and_reuses_unchanged_ones(self):
        events = []
        course = build(self.sources, store=self.store, embedder=self.embedder, transport=self.model, emit=events.append)
        again = []
        build(self.sources, store=self.store, embedder=self.embedder, transport=self.model, emit=again.append)

        self.assertEqual(sorted(course["days"]), ["1", "2"])
        self.assertEqual(len(course["days"]["1"]["questions"]), 2)
        self.assertEqual(course["days"]["1"]["sources"]["task"], 1)
        self.assertEqual(events[-1]["fresh"], 2)
        self.assertEqual(again[-1]["fresh"], 0)
        self.assertEqual(len([payload for payload in self.model.payloads if "конспект" in payload["messages"][0]["content"]]), 2)

    def test_ask_cites_sources_and_can_stay_within_a_day(self):
        build(self.sources, store=self.store, embedder=self.embedder, transport=self.model)
        anywhere = ask("Как вызвать модель?", store=self.store, embedder=self.embedder, transport=self.model)
        one_day = ask("Как вызвать модель?", store=self.store, embedder=self.embedder, transport=self.model, day=2)

        self.assertEqual(anywhere["cited"], [1])
        self.assertTrue(anywhere["sources"])
        self.assertTrue(all(source["day"] == 2 for source in one_day["sources"]))

    def test_quiz_picks_grades_and_counts(self):
        course = build(self.sources, store=self.store, embedder=self.embedder, transport=self.model)
        question = pick(course, 1, rng=random.Random(1))
        result = grade(course, 1, question["index"], "Через API", transport=self.model)
        progress = self.store.record(1, question["index"], result["verdict"])

        self.assertEqual(question["day"], 1)
        self.assertEqual(result["verdict"], "wrong")
        self.assertEqual(result["hint"], "Скажи про API.")
        self.assertEqual(progress["1"]["answers"], 1)
        with self.assertRaises(CourseError):
            grade(course, 1, 7, "?", transport=self.model)
        with self.assertRaises(CourseError):
            pick({}, None)


class DayThirtyFiveTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        folder = pathlib.Path(directory.name)
        root = make_root(folder / "project")
        (folder / "chat").mkdir()
        (folder / "chat" / "messages.html").write_text(CHAT, encoding="utf-8")
        self.app = create_app({
            "TESTING": True,
            "AGENT_STORE_DIR": str(folder / "agents"),
            "DAY35_DATA": str(folder / "day35"),
            "DAY35_CHAT_DIR": str(folder / "chat"),
            "DAY35_TALKS": [],
            "DAY35_ROOT": str(root),
            "RAG_EMBEDDER": HashingEmbedder(),
            "AGENT_TRANSPORT": Model(),
        })
        self.client = self.app.test_client()

    def test_flow_build_read_ask_and_train(self):
        self.assertIn("Собрать курс", self.client.get("/days/35/").get_data(as_text=True))
        self.assertEqual(self.client.post("/days/35/api/ask", json={"question": "Что было?"}).status_code, 409)
        self.assertEqual(self.client.get("/days/35/api/quiz").status_code, 409)

        events = [json.loads(line) for line in self.client.post("/days/35/api/build").get_data(as_text=True).splitlines() if line]
        self.assertEqual(events[0]["type"], "sources")
        self.assertEqual(events[-1], {"type": "done"})
        html = self.client.get("/days/35/").get_data(as_text=True)
        self.assertIn('data-day="1"', html)
        self.assertIn("Пересобрать", html)

        self.assertEqual(self.client.get("/days/35/api/days/1").get_json()["title"], "Первый запрос")
        self.assertEqual(self.client.get("/days/35/api/days/9").status_code, 404)
        self.assertEqual(self.client.post("/days/35/api/ask", json={"question": "Как вызвать модель?", "day": 1}).get_json()["cited"], [1])

        question = self.client.get("/days/35/api/quiz?day=1").get_json()
        checked = self.client.post("/days/35/api/quiz", json={"day": 1, "index": question["index"], "answer": "Через API"}).get_json()
        self.assertEqual(checked["progress"]["answers"], 1)

    def test_validation(self):
        self.assertEqual(self.client.post("/days/35/api/ask", json={"question": " "}).status_code, 400)
        self.assertEqual(self.client.post("/days/35/api/quiz", json={"day": 99, "answer": "x"}).status_code, 400)
        self.assertEqual(self.client.post("/days/35/api/quiz", json={"day": 1, "index": 0, "answer": " "}).status_code, 400)


if __name__ == "__main__":
    unittest.main()

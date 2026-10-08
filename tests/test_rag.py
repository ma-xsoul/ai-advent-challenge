import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import requests

from advent.rag import HashingEmbedder, IndexNotReady, OllamaEmbedder, RagIndex, chunk, collect
from advent.rag.chunking import FIXED_OVERLAP, FIXED_SIZE, STRUCTURE_MAX, _label, stats
from advent.rag.corpus import Document, page_text
from advent.rag.embeddings import EmbeddingError
from advent.rag.answer import MODES, ask, augment, citations, find_probe, grade, iter_compare, messages
from advent.rag.probes import PROBES, Fact, Probe, evaluate


# Тела подлиннее: короткие соседние куски структурная нарезка склеивает в один.
CODE = '''"""Модуль про курс доллара."""

import json

RATE = 84


def collect_rate():
    """Снять курс доллара и сохранить снимок."""
    # снимок курса доллара сохраняется в базу для сводки
    # снимок курса доллара сохраняется в базу для сводки
    # снимок курса доллара сохраняется в базу для сводки
    # снимок курса доллара сохраняется в базу для сводки
    # снимок курса доллара сохраняется в базу для сводки
    # снимок курса доллара сохраняется в базу для сводки
    # снимок курса доллара сохраняется в базу для сводки
    # снимок курса доллара сохраняется в базу для сводки
    return RATE


class Store:
    """Хранилище снимков."""
    # хранилище снимков курса: сохранить, прочитать, очистить
    # хранилище снимков курса: сохранить, прочитать, очистить
    # хранилище снимков курса: сохранить, прочитать, очистить
    # хранилище снимков курса: сохранить, прочитать, очистить
    # хранилище снимков курса: сохранить, прочитать, очистить
    # хранилище снимков курса: сохранить, прочитать, очистить
    # хранилище снимков курса: сохранить, прочитать, очистить
    # хранилище снимков курса: сохранить, прочитать, очистить

    def save(self, value):
        return json.dumps(value)
'''

MARKDOWN = """# Проект

Вступление про проект.

## Память

Три слоя памяти: краткосрочная, рабочая и долговременная. Каждый слой живёт своё время.

## Расписание

Cron будит агента раз в минуту, тик снимает курс и присылает сводку.
"""


def documents():
    return [
        Document("advent/rates.py", "code", "Модуль про курс доллара.", CODE),
        Document("README.md", "doc", "Проект", MARKDOWN),
    ]


class CorpusTestCase(unittest.TestCase):
    def test_project_corpus_has_docs_code_and_pages(self):
        corpus = collect()
        kinds = {document.kind for document in corpus}
        sources = {document.source for document in corpus}

        self.assertEqual(kinds, {"doc", "code", "page"})
        self.assertIn("README.md", sources)
        self.assertIn("advent/mcp/agent.py", sources)
        self.assertIn("advent/templates/days/day18.html", sources)
        self.assertNotIn("advent/rag/probes.py", sources)
        self.assertNotIn("advent/days/day03/__init__.py", sources)
        self.assertGreater(sum(document.pages for document in corpus), 30)

    def test_page_text_drops_markup_and_keeps_headings(self):
        source = """{% extends "base.html" %}
<h1>День 9.<br><span>Сжатие</span></h1>
<p>Текст {{ url_for('x') }} &laquo;в кавычках&raquo;</p>
<ul><li>Пункт</li></ul>
<script>alert(1)</script>"""
        text = page_text(source)

        self.assertIn("# День 9. Сжатие", text)
        self.assertIn("Текст «в кавычках»", text)
        self.assertIn("- Пункт", text)
        self.assertNotIn("extends", text)
        self.assertNotIn("url_for", text)
        self.assertNotIn("alert", text)
        self.assertNotIn("<", text)


class ChunkingTestCase(unittest.TestCase):
    def test_fixed_windows_are_bounded_and_overlap(self):
        text = " ".join(f"слово{i}" for i in range(1200))
        pieces = chunk([Document("a.md", "doc", "a", text)], "fixed")

        self.assertGreater(len(pieces), 3)
        self.assertTrue(all(len(piece.text) <= FIXED_SIZE for piece in pieces))
        tail = pieces[0].text[-FIXED_OVERLAP // 2 :]
        self.assertIn(tail.split()[-1], pieces[1].text)
        self.assertEqual(pieces[0].chunk_id, "fixed:a.md#1")

    def test_structure_cuts_code_by_functions_and_classes(self):
        pieces = chunk([documents()[0]], "structure")
        sections = " | ".join(piece.section for piece in pieces)

        self.assertIn("def collect_rate", sections)
        self.assertIn("class Store", sections)
        joined = "\n".join(piece.text for piece in pieces)
        self.assertIn("def save(self, value):", joined)
        self.assertTrue(all(piece.kind == "code" for piece in pieces))

    def test_structure_cuts_markdown_by_headings_with_the_chain(self):
        long = MARKDOWN + "\n## Длинный раздел\n\n" + "\n\n".join(f"Абзац {i}. " + "текст " * 60 for i in range(20))
        pieces = chunk([Document("README.md", "doc", "Проект", long)], "structure")

        self.assertTrue(any("Проект › Память" in piece.section for piece in pieces))
        self.assertTrue(any("Длинный раздел" in piece.section for piece in pieces))
        self.assertTrue(all(len(piece.text) <= STRUCTURE_MAX for piece in pieces))
        self.assertTrue(all(piece.start_line <= piece.end_line for piece in pieces))

    def test_fixed_chunks_know_their_structural_section(self):
        pieces = chunk([documents()[1]], "fixed")
        self.assertEqual(pieces[0].section.split(" › ")[0], "Проект")

    def test_merged_labels_name_the_common_path_once(self):
        self.assertEqual(_label("A › B\x1fA › B › x\x1fA › B › y"), "A › B › x, y")
        self.assertEqual(_label("def a\x1fdef a\x1fdef b"), "def a, def b")
        self.assertEqual(_label("код модуля"), "код модуля")

    def test_stats(self):
        result = stats(chunk(documents(), "structure"))
        self.assertEqual(result["sources"], 2)
        self.assertGreaterEqual(result["max_chars"], result["avg_chars"])


class EmbeddingsTestCase(unittest.TestCase):
    def test_hashing_vectors_are_normalized_and_meaningful_enough(self):
        vectors = HashingEmbedder().embed(["курс доллара сегодня", "курс доллара вчера", "слои памяти агента"])

        np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.0, rtol=1e-5)
        self.assertGreater(float(vectors[0] @ vectors[1]), float(vectors[0] @ vectors[2]))

    def test_ollama_failures_are_explained(self):
        embedder = OllamaEmbedder(url="http://ollama.test")
        with patch("advent.rag.embeddings.requests.post", side_effect=requests.ConnectionError("refused")):
            with self.assertRaisesRegex(EmbeddingError, "Ollama недоступен"):
                embedder.embed(["текст"])
        with patch("advent.rag.embeddings.requests.post", return_value=Mock(status_code=404, ok=False)):
            with self.assertRaisesRegex(EmbeddingError, "ollama pull bge-m3"):
                embedder.embed(["текст"])

    def test_ollama_batches_and_normalizes(self):
        calls = []

        def post(url, json, timeout):
            calls.append(len(json["input"]))
            return Mock(status_code=200, ok=True, json=lambda: {"embeddings": [[3.0, 4.0]] * len(json["input"])})

        with patch("advent.rag.embeddings.requests.post", side_effect=post):
            vectors = OllamaEmbedder(url="http://ollama.test").embed(["t"] * 40)

        self.assertEqual(calls, [32, 8])
        np.testing.assert_allclose(vectors[0], [0.6, 0.8], rtol=1e-5)


class IndexTestCase(unittest.TestCase):
    def test_build_search_and_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            index = RagIndex(Path(directory) / "index.sqlite3")
            embedder = HashingEmbedder()
            events = []
            info = index.build(documents(), embedder, progress=events.append)

            self.assertTrue(info["built"])
            self.assertEqual(info["model"], "hashing:256")
            self.assertEqual(info["dims"], 256)
            self.assertEqual(set(info["strategies"]), {"fixed", "structure"})
            self.assertEqual([event["type"] for event in events][0], "corpus")
            self.assertEqual(events[-1]["type"], "done")

            for strategy in ("fixed", "structure"):
                hits = index.search("слои памяти краткосрочная рабочая", embedder, strategy, k=2)
                self.assertEqual(hits[0].source, "README.md")
                self.assertEqual(hits[0].rank, 1)
                self.assertGreaterEqual(hits[0].score, hits[-1].score)
            hit = index.search("снять курс доллара", embedder, "structure", k=1)[0]
            self.assertEqual(hit.source, "advent/rates.py")
            self.assertIn("collect_rate", hit.section)

            again = index.build(documents(), embedder)
            self.assertTrue(all(stats["embedded"] == 0 for stats in again["strategies"].values()))
            self.assertEqual(index.sources(), ["README.md", "advent/rates.py"])
            self.assertTrue(index.sample("structure", "advent/rates.py"))

    def test_search_needs_the_same_model(self):
        with tempfile.TemporaryDirectory() as directory:
            index = RagIndex(Path(directory) / "index.sqlite3")
            with self.assertRaisesRegex(IndexNotReady, "не собран"):
                index.search("вопрос", HashingEmbedder(), "fixed")
            index.build(documents(), HashingEmbedder())
            with self.assertRaisesRegex(IndexNotReady, "несравнимы"):
                index.search("вопрос", HashingEmbedder(dims=64), "fixed")

    def test_probes_measure_where_the_right_file_lands(self):
        with tempfile.TemporaryDirectory() as directory:
            index = RagIndex(Path(directory) / "index.sqlite3")
            embedder = HashingEmbedder()
            index.build(documents(), embedder)
            probes = (
                Probe("три слоя памяти краткосрочная рабочая долговременная", ("README.md",)),
                Probe("снять курс доллара сохранить снимок", ("advent/rates.py",)),
            )
            result = evaluate(index, embedder, probes=probes)

            self.assertEqual(result["summary"]["structure"]["hit1"], 1.0)
            self.assertEqual(result["summary"]["structure"]["mrr"], 1.0)
            self.assertEqual(result["questions"][0]["structure"]["rank"], 1)
            self.assertEqual(result["questions"][1]["fixed"]["top"]["source"], "advent/rates.py")


class RagAnswerTransport:
    """Отвечает по-разному с кусками и без: с ними — фактами и ссылкой [1]."""

    def __init__(self):
        self.payloads = []

    def _answer(self, payload):
        self.payloads.append(payload)
        question = payload["messages"][-1]["content"]
        if "Фрагменты:" in question:
            return "Три слоя: chat, task и user [1]. Chat живёт до «Нового диалога» [1]."
        return "Обычно у агентов есть краткосрочная и долговременная память."

    def send(self, payload):
        return {"choices": [{"message": {"content": self._answer(payload)}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    def stream(self, payload):
        text = self._answer(payload)
        for start in range(0, len(text), 7):
            yield {"choices": [{"delta": {"content": text[start : start + 7]}}]}
        yield {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


MEMORY_PROBE = Probe(
    "Какие слои памяти есть у агента и сколько живёт каждый?",
    ("README.md",),
    "Три слоя: chat, task, user.",
    (
        Fact("три слоя: chat, task, user", ("chat & task & user",)),
        Fact("chat живёт до «Нового диалога»", ("новому диалогу", "нового диалога")),
        Fact("user живёт, пока не удалят руками", ("руками",)),
    ),
)


class RagAnswerTestCase(unittest.TestCase):
    def _index(self, directory):
        index = RagIndex(Path(directory) / "index.sqlite3")
        index.build(documents(), HashingEmbedder())
        return index

    def test_control_questions_carry_expectations_and_facts(self):
        self.assertEqual(len(PROBES), 10)
        for probe in PROBES:
            with self.subTest(question=probe.question):
                self.assertTrue(probe.expected)
                self.assertGreaterEqual(len(probe.facts), 3)
                self.assertTrue(probe.sources)
                self.assertEqual(probe.as_dict()["facts"], [fact.label for fact in probe.facts])
        self.assertIs(find_probe("  " + PROBES[2].question.upper() + " "), PROBES[2])
        self.assertIsNone(find_probe("что-то своё"))

    def test_fact_variants_any_and_all_parts(self):
        fact = Fact("слои", ("chat & task & user", "три слоя"))
        self.assertTrue(fact.found_in("Слои `chat`, task, USER"))
        self.assertTrue(fact.found_in("Есть три слоя"))
        self.assertFalse(fact.found_in("chat и task"))
        self.assertTrue(Fact("ё", ("всё",)).found_in("Все равно"))

    def test_prompt_differs_only_by_fragments(self):
        with tempfile.TemporaryDirectory() as directory:
            hits = self._index(directory).search("слои памяти", HashingEmbedder(), "structure", k=2)
        plain, rag = messages("Вопрос?", None), messages("Вопрос?", hits)

        self.assertEqual(plain[0], rag[0])
        self.assertEqual(plain[1]["content"], "Вопрос?")
        self.assertTrue(rag[1]["content"].endswith("Вопрос: Вопрос?"))
        self.assertIn(f"[1] {hits[0].source} · {hits[0].section}", augment("Вопрос?", hits))
        self.assertIn(hits[1].text, rag[1]["content"])

    def test_citations_map_numbers_to_files(self):
        with tempfile.TemporaryDirectory() as directory:
            hits = self._index(directory).search("слои памяти", HashingEmbedder(), "structure", k=2)
        cited = citations("Факт [2]. Ещё [1, 2]; чужое [7] и [x].", hits)

        self.assertEqual([item["n"] for item in cited], [1, 2])
        self.assertEqual(cited[0]["source"], hits[0].source)

    def test_grade_counts_facts_and_sources(self):
        good = grade(MEMORY_PROBE, "chat, task, user; chat до нового диалога [1]", [{"n": 1, "source": "README.md", "section": ""}])
        self.assertEqual((good["found"], good["total"], good["need"]), (2, 3, 2))
        self.assertTrue(good["passed"])
        self.assertTrue(good["sources_ok"])

        bad = grade(MEMORY_PROBE, "краткосрочная и долговременная", None)
        self.assertFalse(bad["passed"])
        self.assertIsNone(bad["sources_ok"])

    def test_ask_runs_both_modes_and_streams(self):
        with tempfile.TemporaryDirectory() as directory:
            index = self._index(directory)
            transport = RagAnswerTransport()
            events = []
            outcome = ask(MEMORY_PROBE.question, index=index, embedder=HashingEmbedder(), transport=transport, emit=events.append, k=2, probe=MEMORY_PROBE)

        types = [event["type"] for event in events]
        self.assertEqual(types[0], "retrieved")
        self.assertIn("delta", types)
        answers = {event["mode"]: event for event in events if event["type"] == "answer"}
        self.assertEqual(set(answers), set(MODES))
        self.assertEqual(answers["rag"]["cited"][0]["n"], 1)
        self.assertTrue(answers["rag"]["grade"]["passed"])
        self.assertFalse(answers["plain"]["grade"]["passed"])
        self.assertIsNone(answers["plain"]["cited"])
        self.assertEqual(answers["rag"]["prompt_tokens"], 10)
        self.assertEqual(len(events[0]["hits"]), 2)
        self.assertTrue(all(payload["stream"] and payload["thinking"] == {"type": "disabled"} for payload in transport.payloads))
        self.assertEqual(len(outcome["results"]), 2)

    def test_ask_reports_a_failed_mode_without_dropping_the_other(self):
        class HalfBroken(RagAnswerTransport):
            def send(self, payload):
                if "Фрагменты:" not in payload["messages"][-1]["content"]:
                    from advent.agent.transport import TransportError

                    raise TransportError("лимит")
                return super().send(payload)

        with tempfile.TemporaryDirectory() as directory:
            events = []
            ask("вопрос", index=self._index(directory), embedder=HashingEmbedder(), transport=HalfBroken(), emit=events.append, stream=False)
        answers = {event["mode"]: event for event in events if event["type"] == "answer"}
        self.assertEqual(answers["plain"]["error"], "лимит")
        self.assertIn("answer", answers["rag"])

    def test_compare_grades_every_question_and_sums_up(self):
        with tempfile.TemporaryDirectory() as directory:
            events = list(iter_compare(index=self._index(directory), embedder=HashingEmbedder(), transport=RagAnswerTransport(), probes=(MEMORY_PROBE, MEMORY_PROBE)))

        self.assertEqual(events[0]["type"], "compare_start")
        self.assertEqual(sum(event["type"] == "answer" for event in events), 4)
        self.assertTrue(all("index" in event for event in events if event["type"] == "answer"))
        summary = events[-1]
        self.assertEqual(summary["type"], "compare_summary")
        self.assertEqual(summary["summary"]["rag"]["passed"], 2)
        self.assertEqual(summary["summary"]["plain"]["passed"], 0)
        self.assertEqual(summary["summary"]["rag"]["facts"], 0.67)
        self.assertIsNone(summary["summary"]["plain"]["sources"])
        self.assertEqual(summary["summary"]["rag"]["tokens"], 30)


if __name__ == "__main__":
    unittest.main()

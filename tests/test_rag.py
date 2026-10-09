import json
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
from advent.rag.chat import (
    CONDENSE_PROMPT,
    MEMORY_PROMPT,
    SCENARIOS,
    ChatStore,
    RagChat,
    Scenario,
    TaskMemory,
    scenario_summary,
    turn as chat_turn,
    update_memory,
)
from advent.rag.grounded import (
    ANSWER_PROMPT,
    CLARIFY_PROMPT,
    JUDGE_PROMPT,
    UNKNOWN,
    ask as ask_grounded,
    check as check_grounded,
    iter_compare as iter_grounded_compare,
    sources_of,
    verify_quote,
)
from advent.rag.probes import NEGATIVE_PROBES, PROBES, REFUSAL, Fact, Probe, evaluate
from advent.rag.rerank import (
    NOTHING_FOUND,
    REWRITE_PROMPT,
    RERANK_PROMPT,
    Settings,
    funnel,
    iter_compare as iter_rerank_compare,
    parse_scores,
    run_question,
)


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


class RerankTransport(RagAnswerTransport):
    """Переписывает вопрос, оценивает куски «про память» на 9, прочие — на 1; отвечает как RagAnswerTransport."""

    def __init__(self, rerank_reply=None):
        super().__init__()
        self.rerank_reply = rerank_reply
        self.calls = {"rewrite": 0, "rerank": 0, "answer": 0}

    def send(self, payload):
        system = payload["messages"][0]["content"]
        if system == REWRITE_PROMPT:
            self.calls["rewrite"] += 1
            return {"choices": [{"message": {"content": "«слои памяти chat task user краткосрочная»"}}], "usage": {"prompt_tokens": 4, "completion_tokens": 3}}
        if system == RERANK_PROMPT:
            self.calls["rerank"] += 1
            self.payloads.append(payload)
            if self.rerank_reply is not None:
                return {"choices": [{"message": {"content": self.rerank_reply}}], "usage": {}}
            fragments = payload["messages"][1]["content"].split("\n\n---\n\n")
            scores = {str(n): (9 if "памят" in fragment.lower() else 1) for n, fragment in enumerate(fragments, start=1)}
            return {"choices": [{"message": {"content": json.dumps({"scores": scores})}}], "usage": {"prompt_tokens": 20, "completion_tokens": 5}}
        self.calls["answer"] += 1
        return super().send(payload)

    def stream(self, payload):
        self.calls["answer"] += 1
        yield from super().stream(payload)


class RerankTestCase(unittest.TestCase):
    def _index(self, directory):
        index = RagIndex(Path(directory) / "index.sqlite3")
        index.build(documents(), HashingEmbedder())
        return index

    def test_settings_are_validated(self):
        self.assertEqual(Settings.parse(None), Settings())
        self.assertEqual(Settings.parse({"threshold": "0.6", "top_k": 3}).threshold, 0.6)
        for bad, message in (({"threshold": 2}, "threshold"), ({"top_k": "x"}, "число"), ({"candidates": 3, "top_k": 5}, "больше кандидатов")):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, message):
                    Settings.parse(bad)

    def test_rerank_scores_are_parsed_defensively(self):
        self.assertEqual(parse_scores('{"scores": {"1": 9, "3": "4"}}', 3), [9, 0, 4])
        self.assertEqual(parse_scores('ответ: {"scores": [{"n": 2, "score": 12}]}', 2), [0, 10])
        self.assertEqual(parse_scores('{"1": 3, "2": "плохо"}', 2), [3, 0])
        self.assertIsNone(parse_scores("не JSON", 2))
        self.assertIsNone(parse_scores('{"scores": 5}', 2))

    def test_negative_probes_expect_an_honest_refusal(self):
        self.assertEqual(len(NEGATIVE_PROBES), 3)
        for probe in NEGATIVE_PROBES:
            self.assertEqual(probe.sources, ())
            self.assertEqual(probe.facts, (REFUSAL,))
        verdict = grade(NEGATIVE_PROBES[0], NOTHING_FOUND, [])
        self.assertTrue(verdict["passed"])
        self.assertIsNone(verdict["sources_ok"])
        self.assertFalse(grade(NEGATIVE_PROBES[0], "Создайте Deployment и Service.", [])["passed"])

    def test_funnel_marks_where_each_candidate_dropped(self):
        with tempfile.TemporaryDirectory() as directory:
            index = self._index(directory)
            transport = RerankTransport()
            settings = Settings(threshold=0.0, candidates=6, top_k=1, rerank_min=5)
            base = funnel("слои памяти", "base", index=index, embedder=HashingEmbedder(), transport=transport, settings=settings)
            filtered = funnel("слои памяти", "filter", index=index, embedder=HashingEmbedder(), transport=transport, settings=settings)
            rewritten = funnel("слои памяти", "rewrite", index=index, embedder=HashingEmbedder(), transport=transport, settings=settings)
            strict = funnel("слои памяти", "filter", index=index, embedder=HashingEmbedder(), transport=transport, settings=Settings(threshold=0.99, candidates=6, top_k=1))

        self.assertEqual(len(base["context"]), 1)
        self.assertEqual(base["cost"], {"rewrite": None, "rerank": None})
        stages = [row["stage"] for row in filtered["candidates"]]
        self.assertEqual(stages.count("context"), 1)
        self.assertIn("rerank", stages)
        context_row = next(row for row in filtered["candidates"] if row["stage"] == "context")
        self.assertEqual(context_row["rerank"], 9)
        self.assertEqual(filtered["context"][0].rank, 1)
        self.assertIn("памят", filtered["context"][0].text.lower())
        self.assertEqual(rewritten["query"], "слои памяти chat task user краткосрочная")
        self.assertTrue({row["via"] for row in rewritten["candidates"]} <= {"question", "rewrite", "both"})
        self.assertEqual(rewritten["cost"]["rewrite"]["tokens"], 7)
        self.assertEqual({row["stage"] for row in strict["candidates"]}, {"threshold"})
        self.assertEqual(strict["context"], [])
        self.assertIsNone(strict["cost"]["rerank"])
        self.assertEqual(transport.calls["rerank"], 2)

    def test_broken_reranker_keeps_vector_order(self):
        with tempfile.TemporaryDirectory() as directory:
            result = funnel(
                "слои памяти", "filter", index=self._index(directory), embedder=HashingEmbedder(),
                transport=RerankTransport(rerank_reply="не скажу"), settings=Settings(threshold=0.0, candidates=4, top_k=2),
            )
        self.assertEqual(len(result["context"]), 2)
        self.assertEqual(result["cost"]["rerank"]["error"], "реранкер вернул не JSON")
        self.assertTrue(all(row["rerank"] is None for row in result["candidates"]))

    def test_run_question_skips_the_model_when_nothing_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            index = self._index(directory)
            transport = RerankTransport()
            events = []
            results = run_question(
                NEGATIVE_PROBES[2].question, index=index, embedder=HashingEmbedder(), transport=transport,
                settings=Settings(threshold=0.99), emit=events.append, probe=NEGATIVE_PROBES[2],
            )

        self.assertEqual(sum(event["type"] == "funnel" for event in events), 3)
        self.assertEqual(results["filter"]["answer"], NOTHING_FOUND)
        self.assertTrue(results["filter"]["skipped"])
        self.assertTrue(results["filter"]["grade"]["passed"])
        self.assertTrue(results["rewrite"]["skipped"])
        self.assertFalse(results["base"].get("skipped"))
        self.assertEqual(transport.calls["answer"], 1)
        self.assertEqual(results["rewrite"]["tokens"], 7)

    def test_compare_counts_answers_refusals_and_cost(self):
        probes = (MEMORY_PROBE, Probe("Что такое Kubernetes?", (), "нет", (REFUSAL,)))
        with tempfile.TemporaryDirectory() as directory:
            events = list(iter_rerank_compare(
                index=self._index(directory), embedder=HashingEmbedder(), transport=RerankTransport(),
                settings=Settings(threshold=0.0, candidates=6, top_k=2), probes=probes,
            ))

        self.assertEqual(events[0]["type"], "compare_start")
        rows = [event for event in events if event["type"] == "compare_row"]
        self.assertEqual(sorted(row["index"] for row in rows), [1, 2])
        self.assertTrue(next(row for row in rows if row["index"] == 2)["negative"])
        summary = events[-1]
        self.assertEqual((summary["on_topic"], summary["off_topic"]), (1, 1))
        self.assertEqual(summary["summary"]["filter"]["passed"], 1)
        self.assertGreater(summary["summary"]["rewrite"]["tokens"], summary["summary"]["base"]["tokens"])
        # Без фильтра в контекст попадает и посторонний rates.py, реранкер его выкидывает.
        self.assertEqual(summary["summary"]["base"]["precision"], 0.5)
        self.assertEqual(summary["summary"]["filter"]["precision"], 1.0)
        row = next(row for row in rows if row["index"] == 1)
        self.assertEqual((row["modes"]["base"]["relevant"], row["modes"]["base"]["context"]), (1, 2))
        self.assertIsNone(next(row for row in rows if row["index"] == 2)["modes"]["base"]["relevant"])


class GroundedTransport:
    """Реранкер: на вопрос про память куски про память — 9, прочее — 1. Ответ — JSON с цитатой из первого куска. Судья — «yes»."""

    def __init__(self, answer=None, verdict="yes"):
        self.answer = answer
        self.verdict = verdict
        self.calls = {"rerank": 0, "answer": 0, "judge": 0, "clarify": 0}

    def send(self, payload):
        system, user = payload["messages"][0]["content"], payload["messages"][1]["content"]
        if system == RERANK_PROMPT:
            self.calls["rerank"] += 1
            fragments = user.split("\n\n---\n\n")
            about_memory = "памят" in user.split("\n", 1)[0].lower()
            text = json.dumps({"scores": {str(n): (9 if about_memory and "памят" in f.lower() else 1) for n, f in enumerate(fragments, start=1)}})
        elif system == ANSWER_PROMPT:
            self.calls["answer"] += 1
            # Первый фрагмент: «Фрагменты:», пустая строка, заголовок [1] …, дальше текст куска.
            lines = user.split("\n\n---\n\n")[0].splitlines()[3:]
            quote = max(lines, key=len)
            text = self.answer if self.answer is not None else json.dumps(
                {"status": "answer", "answer": "Слои chat, task и user [1].", "quotes": [{"n": 1, "text": quote}], "clarify": ""}
            )
        elif system == JUDGE_PROMPT:
            self.calls["judge"] += 1
            text = json.dumps({"verdict": self.verdict, "unsupported": [], "comment": "цитаты подтверждают ответ"})
        else:
            self.calls["clarify"] += 1
            self.clarify_system = system
            text = "Уточните вопрос: например, про память агента (день 11)."
        return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 2, "completion_tokens": 1}}


class GroundedTestCase(unittest.TestCase):
    def _index(self, directory):
        index = RagIndex(Path(directory) / "index.sqlite3")
        index.build(documents(), HashingEmbedder())
        return index

    def _context(self, directory):
        return self._index(directory).search("слои памяти", HashingEmbedder(), "structure", k=2)

    def test_quotes_are_checked_against_their_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            context = self._context(directory)
        own, other = context[0], context[1]
        line = max(own.text.splitlines(), key=len)

        self.assertEqual(verify_quote({"n": own.rank, "text": line}, context)["status"], "exact")
        self.assertEqual(verify_quote({"n": own.rank, "text": "  " + line.upper().replace(" ", "   ")}, context)["status"], "near")
        moved = verify_quote({"n": own.rank, "text": max(other.text.splitlines(), key=len)}, context)
        self.assertEqual((moved["status"], moved["n"], moved["claimed"]), ("elsewhere", other.rank, own.rank))
        self.assertEqual(verify_quote({"n": own.rank, "text": line + " Ещё."}, context)["status"], "partial")
        self.assertEqual(verify_quote({"n": own.rank, "text": "Слоёв памяти у агента семь, все вечные."}, context)["status"], "missing")
        self.assertEqual(verify_quote({"n": 9, "text": "нет такого куска"}, context)["status"], "missing")

    def test_sources_come_from_code_not_from_the_model(self):
        with tempfile.TemporaryDirectory() as directory:
            context = self._context(directory)
        quotes = [{"n": 2, "status": "exact"}, {"n": 1, "status": "missing"}]
        sources = sources_of("Ответ [1]. И ещё [7].", quotes, context)

        self.assertEqual([source["n"] for source in sources], [1, 2])
        self.assertEqual(sources[0]["chunk_id"], context[0].chunk_id)
        self.assertEqual((sources[0]["cited"], sources[0]["quoted"]), (True, False))
        self.assertEqual((sources[1]["cited"], sources[1]["quoted"]), (False, True))

    def test_grounded_answer_with_sources_quotes_and_judge(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = GroundedTransport()
            events = []
            result = ask_grounded(
                MEMORY_PROBE.question, index=self._index(directory), embedder=HashingEmbedder(), transport=transport,
                settings=Settings(threshold=0.0, candidates=4, top_k=2), emit=events.append, probe=MEMORY_PROBE,
            )

        self.assertEqual([event["type"] for event in events], ["retrieved", "answer", "judge", "checks"])
        self.assertEqual(result["status"], "answer")
        self.assertEqual(result["relevance"], 9)
        self.assertEqual(result["quotes"][0]["status"], "exact")
        self.assertIn("chunk", result["quotes"][0])
        self.assertEqual(result["sources"][0]["n"], 1)
        self.assertEqual(result["judge"]["verdict"], "yes")
        self.assertTrue(result["checks"]["passed"])
        self.assertEqual(transport.calls, {"rerank": 1, "answer": 1, "judge": 1, "clarify": 0})
        json.dumps(events)  # всё, что уходит на страницу, сериализуется

    def test_low_relevance_means_i_dont_know_and_a_clarifying_question(self):
        with tempfile.TemporaryDirectory() as directory:
            transport = GroundedTransport()
            result = ask_grounded(
                NEGATIVE_PROBES[0].question, index=self._index(directory), embedder=HashingEmbedder(), transport=transport,
                settings=Settings(threshold=0.0, rerank_min=10), emit=lambda event: None, probe=NEGATIVE_PROBES[0],
            )

        self.assertEqual((result["status"], result["reason"], result["answer"]), ("unknown", "relevance", UNKNOWN))
        self.assertIn("Уточните", result["clarify"])
        self.assertIn("день 11", transport.clarify_system)
        self.assertEqual(transport.calls["answer"], 0)
        self.assertIsNone(result["judge"])
        self.assertTrue(result["checks"]["passed"])

    def test_broken_or_unfounded_answers_fail_the_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            index = self._index(directory)
            loose = Settings(threshold=0.0, candidates=4, top_k=2)
            not_json = ask_grounded(
                MEMORY_PROBE.question, index=index, embedder=HashingEmbedder(), transport=GroundedTransport(answer="просто текст"),
                settings=loose, emit=lambda event: None, probe=MEMORY_PROBE,
            )
            invented = ask_grounded(
                MEMORY_PROBE.question, index=index, embedder=HashingEmbedder(),
                transport=GroundedTransport(answer=json.dumps({"status": "answer", "answer": "Семь слоёв [1].", "quotes": [{"n": 1, "text": "семь вечных слоёв"}]})),
                settings=loose, emit=lambda event: None, probe=MEMORY_PROBE,
            )
            partial = ask_grounded(
                MEMORY_PROBE.question, index=index, embedder=HashingEmbedder(), transport=GroundedTransport(verdict="partial"),
                settings=loose, emit=lambda event: None, probe=MEMORY_PROBE,
            )

        self.assertFalse(not_json["parsed"])
        self.assertFalse(not_json["checks"]["has_quotes"])
        self.assertFalse(not_json["checks"]["passed"])
        self.assertEqual(invented["quotes"][0]["status"], "missing")
        self.assertFalse(invented["checks"]["passed"])
        self.assertEqual(partial["checks"]["meaning"], "partial")
        self.assertFalse(partial["checks"]["passed"])

    def test_answering_an_off_topic_question_is_a_miss(self):
        verdict = check_grounded({"status": "answer", "quotes": [], "sources": []}, None, NEGATIVE_PROBES[1])
        self.assertFalse(verdict["passed"])
        self.assertTrue(verdict["negative"])
        self.assertIsNone(check_grounded({"status": "unknown", "quotes": [], "sources": []}, None, None)["passed"])

    def test_compare_sums_up_the_three_checks(self):
        probes = (MEMORY_PROBE, Probe("Что такое Kubernetes?", (), "нет", (REFUSAL,)))
        transport = GroundedTransport()
        with tempfile.TemporaryDirectory() as directory:
            index = self._index(directory)
            # Вопрос про Kubernetes реранкер оценит на 1 — ниже порога 5: «не знаю».
            events = list(iter_grounded_compare(index=index, embedder=HashingEmbedder(), transport=transport, settings=Settings(threshold=0.0, candidates=4, top_k=2), probes=probes))

        rows = {event["index"]: event for event in events if event["type"] == "compare_row"}
        self.assertEqual(rows[1]["checks"]["passed"], True)
        self.assertEqual(rows[2]["status"], "unknown")
        summary = events[-1]
        self.assertEqual(summary["type"], "compare_summary")
        self.assertEqual((summary["answered"], summary["with_sources"], summary["with_quotes"]), (1, 1, 1))
        self.assertEqual((summary["quotes_verified"], summary["quotes_total"]), (1, 1))
        self.assertEqual(summary["meaning"], {"yes": 1, "partial": 0, "no": 0})
        self.assertEqual((summary["passed"], summary["false_unknown"], summary["unknown_off_topic"]), (1, 0, 1))
        json.dumps(events)


class ChatTransport:
    """
    Память: цель из первого сообщения, «ограничение: …» — в constraints; goal_changed — если в сообщении
    «новая задача». Запрос — сообщение плюс «слои памяти». Реранкер: про память — 9. Ответ цитирует
    самую длинную строку первого фрагмента.
    """

    def __init__(self):
        self.answer_users = []

    def send(self, payload):
        system, user = payload["messages"][0]["content"], payload["messages"][1]["content"]
        if system == MEMORY_PROMPT:
            message = user.rsplit("Новое сообщение пользователя:\n", 1)[1]
            current = json.loads(user.split("Текущая память:\n", 1)[1].split("\n\n", 1)[0])
            constraints = current["constraints"] + ([message] if message.lower().startswith("ограничение") else [])
            text = json.dumps({
                "goal": f"цель: {message}" if not current["goal"] or "новая задача" in message.lower() else "переформулировка",
                "goal_changed": "новая задача" in message.lower(),
                "clarified": current["clarified"], "constraints": constraints, "terms": current["terms"],
            }, ensure_ascii=False)
        elif system == CONDENSE_PROMPT:
            text = "«слои памяти агента»"
        elif system == RERANK_PROMPT:
            fragments = user.split("\n\n---\n\n")
            text = json.dumps({"scores": {str(n): (9 if "памят" in f.lower() else 1) for n, f in enumerate(fragments, start=1)}})
        elif system.startswith(ANSWER_PROMPT):
            self.answer_users.append(user)
            lines = user.split("Фрагменты:\n\n", 1)[1].split("\n\n---\n\n")[0].splitlines()[1:]
            text = json.dumps({"status": "answer", "quotes": [{"n": 1, "text": max(lines, key=len)}], "answer": "Три слоя памяти [1]."}, ensure_ascii=False)
        else:
            text = "Уточните, пожалуйста, вопрос."
        return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


class RagChatTestCase(unittest.TestCase):
    def _index(self, directory):
        index = RagIndex(Path(directory) / "index.sqlite3")
        index.build(documents(), HashingEmbedder())
        return index

    def test_goal_stays_word_for_word_unless_the_task_changed(self):
        transport = ChatTransport()
        first = update_memory(TaskMemory(), [], "Хочу понять память агента", 1, transport)["memory"]
        self.assertEqual((first.goal, first.goal_since), ("цель: Хочу понять память агента", 1))

        same = update_memory(first, [], "Ограничение: коротко", 2, transport)
        self.assertFalse(same["changed"])
        self.assertEqual(same["memory"].goal, first.goal)  # «переформулировка» модели не прошла
        self.assertEqual(same["memory"].constraints, ["Ограничение: коротко"])

        moved = update_memory(same["memory"], [], "Новая задача: расписание", 3, transport)
        self.assertTrue(moved["changed"])
        self.assertEqual((moved["memory"].goal_since, moved["memory"].goal_changes[0]["from"]), (3, first.goal))

    def test_broken_memory_reply_keeps_the_old_memory(self):
        class Broken:
            def send(self, payload):
                return {"choices": [{"message": {"content": "не JSON"}}]}

        memory = TaskMemory(goal="цель", goal_since=1)
        result = update_memory(memory, [], "ещё", 2, Broken())
        self.assertIs(result["memory"], memory)
        self.assertIn("не JSON", result["error"])
        self.assertIn("Цель диалога: цель", memory.render())

    def test_turn_uses_memory_history_and_returns_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            index = self._index(directory)
            transport = ChatTransport()
            chat, events = RagChat(), []
            for message in ("Какие слои памяти у агента?", "Ограничение: отвечай коротко", "А сколько живёт каждый?"):
                entry = chat_turn(chat, message, index=index, embedder=HashingEmbedder(), transport=transport,
                                  settings=Settings(threshold=0.0, candidates=4, top_k=2), emit=events.append, goal_keywords=("памят",))

        self.assertEqual([event["type"] for event in events[:5]], ["user", "memory", "query", "retrieved", "answer"])
        self.assertEqual(chat.turns, 3)
        self.assertEqual(entry["turn"], 3)
        self.assertEqual(entry["query"], "слои памяти агента")
        self.assertEqual(entry["quotes"][0]["status"], "exact")
        self.assertTrue(entry["checks"]["has_sources"])
        self.assertTrue(entry["checks"]["goal_kept"])
        self.assertEqual(chat.memory.constraints, ["Ограничение: отвечай коротко"])
        last_prompt = transport.answer_users[-1]
        self.assertIn("Цель диалога: цель: Какие слои памяти у агента?", last_prompt)
        self.assertIn("Ограничения: Ограничение: отвечай коротко", last_prompt)
        self.assertIn("Пользователь: Ограничение: отвечай коротко", last_prompt)
        self.assertEqual(chat.history()[0], {"role": "user", "content": "Какие слои памяти у агента?"})
        json.dumps(events, ensure_ascii=False)

    def test_turn_says_i_dont_know_when_nothing_is_relevant(self):
        with tempfile.TemporaryDirectory() as directory:
            chat = RagChat()
            entry = chat_turn(chat, "Какая погода?", index=self._index(directory), embedder=HashingEmbedder(), transport=ChatTransport(),
                              settings=Settings(threshold=0.99), emit=lambda event: None)
        self.assertEqual((entry["status"], entry["content"]), ("unknown", UNKNOWN))
        self.assertTrue(entry["checks"]["unknown"])
        self.assertEqual(entry["sources"], [])

    def test_store_round_trip_and_unsafe_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ChatStore(directory)
            self.assertEqual(store.load("abc").messages, [])
            chat = RagChat(messages=[{"role": "user", "content": "x", "turn": 1}], memory=TaskMemory(goal="г", goal_since=1), scenario="mcp_tool")
            store.save("abc", chat)
            self.assertTrue((Path(directory) / "abc-d25.json").exists())
            again = store.load("abc")
            self.assertEqual((again.turns, again.memory.goal, again.scenario), (1, "г", "mcp_tool"))
            self.assertTrue(store.delete("abc"))
            self.assertFalse(store.delete("abc"))
            with self.assertRaises(ValueError):
                store.load("../etc")

    def test_scenarios_are_long_and_end_with_a_recap(self):
        self.assertEqual(len(SCENARIOS), 2)
        for scenario in SCENARIOS:
            self.assertGreaterEqual(len(scenario.messages), 10)
            self.assertLessEqual(len(scenario.messages), 15)
            self.assertIn("Напомни", scenario.messages[-1])
            self.assertTrue(scenario.recap)

        scenario = Scenario("s", "S", "цель", ("памят",), ("один", "два"), recap=(("памят",), ("токен",)))
        checks = {"unknown": False, "has_sources": True, "quotes_verified": 2, "quotes_total": 2, "goal_kept": True}
        chat = RagChat(messages=[
            {"role": "user", "content": "один"}, {"role": "assistant", "content": "ответ", "checks": checks, "tokens": 5},
            {"role": "user", "content": "два"}, {"role": "assistant", "content": "Цель — память, ограничение — токены.", "checks": checks, "tokens": 5},
        ])
        summary = scenario_summary(chat, scenario)
        self.assertEqual((summary["turns"], summary["with_sources"], summary["goal_kept"], summary["tokens"]), (2, 2, 2, 10))
        self.assertTrue(summary["recap_ok"])
        chat.messages[-1]["content"] = "Цель — память."
        self.assertFalse(scenario_summary(chat, scenario)["recap_ok"])


if __name__ == "__main__":
    unittest.main()

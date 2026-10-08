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
from advent.rag.probes import Probe, evaluate


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


if __name__ == "__main__":
    unittest.main()

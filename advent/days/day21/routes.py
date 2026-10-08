"""
День 21: индексация документов — чанки, эмбеддинги, индекс.

Корпус — наш собственный проект: README, код advent/ и страницы дней
(advent/rag/corpus.py). Он режется двумя стратегиями — окнами
фиксированного размера и по структуре (advent/rag/chunking.py), каждый
кусок превращается в вектор моделью bge-m3 через локальный Ollama
(advent/rag/embeddings.py), и всё ложится в один SQLite-файл с
метаданными (advent/rag/index.py): data/day21/index.sqlite3.

Страница строит индекс потоком событий (сколько документов, сколько
кусков, сколько посчитано векторов), ищет по нему обеими стратегиями
рядом и сравнивает стратегии на десяти контрольных вопросах.
"""

from __future__ import annotations

import json
import queue
import threading
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, render_template, request, stream_with_context

from ...navigation import get_day
from ...rag import (
    DEFAULT_PATH,
    STRATEGIES,
    STRATEGY_TITLES,
    EmbeddingError,
    IndexNotReady,
    OllamaEmbedder,
    RagIndex,
    collect,
    evaluate,
)
from ...rag.probes import iter_evaluate
from ...rag.corpus import CHARS_PER_PAGE


bp = Blueprint("day21", __name__, url_prefix="/days/21")

MAX_QUERY = 500
MAX_K = 10
# Файлы, на которых разница стратегий видна лучше всего: длинный модуль с
# классами, README с разделами и страница дня.
SHOWCASE = ("advent/mcp/agent.py", "README.md", "advent/templates/days/day18.html")


def _index() -> RagIndex:
    return RagIndex(current_app.config.get("DAY21_INDEX_PATH") or DEFAULT_PATH)


def _embedder():
    # Тесты подставляют эмбеддер без сети.
    return current_app.config.get("RAG_EMBEDDER") or OllamaEmbedder()


def _corpus() -> dict[str, Any]:
    documents = collect()
    kinds: dict[str, dict[str, int]] = {}
    for document in documents:
        group = kinds.setdefault(document.kind, {"files": 0, "chars": 0})
        group["files"] += 1
        group["chars"] += len(document.text)
    chars = sum(len(document.text) for document in documents)
    return {
        "documents": len(documents),
        "chars": chars,
        "pages": round(chars / CHARS_PER_PAGE),
        "kinds": kinds,
        "files": [{"source": d.source, "kind": d.kind, "title": d.title, "chars": len(d.text)} for d in documents],
    }


@bp.get("/")
def index():
    corpus = _corpus()
    return render_template(
        "days/day21.html",
        active_day=get_day(21),
        corpus=corpus,
        info=_index().info(),
        strategies=[{"key": key, "title": STRATEGY_TITLES[key]} for key in STRATEGIES],
        showcase=[source for source in SHOWCASE if any(item["source"] == source for item in corpus["files"])],
        model=_embedder().name,
    )


@bp.get("/api/info")
def info():
    return jsonify(_index().info())


@bp.post("/api/build")
def build():
    """Сборка потоком: корпус → нарезка → векторы → готово → контрольные вопросы по одному."""
    index, embedder = _index(), _embedder()
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def worker() -> None:
        try:
            index.build(collect(), embedder, progress=events.put)
            for event in iter_evaluate(index, embedder):
                events.put(event)
        except EmbeddingError as error:
            events.put({"type": "error", "error": str(error)})
        except Exception as error:  # noqa: BLE001 — до страницы должна дойти любая причина
            events.put({"type": "error", "error": f"{type(error).__name__}: {error}"})
        finally:
            events.put(None)

    threading.Thread(target=worker, name="rag-build", daemon=True).start()

    def generate():
        while (event := events.get()) is not None:
            yield json.dumps(event, ensure_ascii=False) + "\n"

    response = Response(stream_with_context(generate()), mimetype="application/x-ndjson")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@bp.get("/api/search")
def search():
    """Один вопрос — выдача обеих стратегий рядом."""
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"error": "Введите запрос"}), 400
    if len(query) > MAX_QUERY:
        return jsonify({"error": f"Запрос длиннее {MAX_QUERY} символов"}), 400
    try:
        k = min(max(int(request.args.get("k", 5)), 1), MAX_K)
    except ValueError:
        return jsonify({"error": "k — целое число"}), 400

    index, embedder = _index(), _embedder()
    try:
        results = {strategy: [hit.as_dict() for hit in index.search(query, embedder, strategy, k=k)] for strategy in STRATEGIES}
    except IndexNotReady as error:
        return jsonify({"error": str(error)}), 409
    except EmbeddingError as error:
        return jsonify({"error": str(error)}), 502
    return jsonify({"query": query, "k": k, "results": results})


@bp.get("/api/probes")
def probes():
    try:
        return jsonify(evaluate(_index(), _embedder()))
    except IndexNotReady as error:
        return jsonify({"error": str(error)}), 409
    except EmbeddingError as error:
        return jsonify({"error": str(error)}), 502


@bp.get("/api/probes/stream")
def probes_stream():
    """Контрольные вопросы по одному: вопрос → место нужного файла в каждой стратегии → следующий."""
    index, embedder = _index(), _embedder()
    if not index.info()["built"]:
        return jsonify({"error": "Индекс ещё не собран"}), 409

    def generate():
        try:
            for event in iter_evaluate(index, embedder):
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except (IndexNotReady, EmbeddingError) as error:
            yield json.dumps({"type": "error", "error": str(error)}, ensure_ascii=False) + "\n"

    response = Response(stream_with_context(generate()), mimetype="application/x-ndjson")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@bp.get("/api/chunks")
def chunks():
    """Как нарезан один файл — обеими стратегиями, для сравнения границ."""
    source = request.args.get("source") or ""
    index = _index()
    if source not in index.sources():
        return jsonify({"error": "Такого файла в индексе нет"}), 404
    return jsonify({"source": source, "strategies": {strategy: index.sample(strategy, source) for strategy in STRATEGIES}})

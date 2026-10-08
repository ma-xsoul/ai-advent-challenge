"""
День 22: первый RAG-запрос — ответ с базой и без неё.

Вопрос → поиск кусков в индексе дня 21 → куски вместе с вопросом в
промпт → ответ LLM (advent/rag/answer.py). Тот же вопрос уходит модели и
без кусков; оба ответа идут параллельно и стоят рядом.

Десять контрольных вопросов — те же, что в дне 21, но теперь у каждого
есть ожидание: какие факты должны прозвучать и на какие файлы сослаться.
Прогон сверяет оба режима с ожиданием кодом и сводит итог в таблицу.

Индекс общий с днём 21 (DAY21_INDEX_PATH): собирается там, здесь только
читается.
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, render_template, request, stream_with_context

from ...agent.transport import DeepSeekTransport, TransportError
from ...navigation import get_day
from ...rag import DEFAULT_PATH, PROBES, EmbeddingError, IndexNotReady, OllamaEmbedder, RagIndex
from ...rag.answer import MAX_K, MODE_TITLES, MODEL, MODES, STRATEGY, TOP_K, ask, find_probe, iter_compare


bp = Blueprint("day22", __name__, url_prefix="/days/22")

MAX_QUESTION = 500


def _index() -> RagIndex:
    return RagIndex(current_app.config.get("DAY21_INDEX_PATH") or DEFAULT_PATH)


def _embedder():
    return current_app.config.get("RAG_EMBEDDER") or OllamaEmbedder()


def _transport():
    return current_app.config.get("AGENT_TRANSPORT") or DeepSeekTransport()


def _index_state(index: RagIndex, embedder) -> dict[str, Any]:
    """Готов ли индекс для вопросов: собран, той же моделью, со стратегией structure."""
    info = index.info()
    state = {"ready": False, "model": info.get("model"), "built_at": info.get("built_at")}
    if not info["built"]:
        return {**state, "reason": "Индекс ещё не собран — соберите его на странице дня 21."}
    if info.get("model") != embedder.name:
        return {**state, "reason": f"Индекс собран моделью {info['model']}, а вопросы — {embedder.name}. Пересоберите на дне 21."}
    strategy = (info.get("strategies") or {}).get(STRATEGY)
    if not strategy:
        return {**state, "reason": f"В индексе нет стратегии {STRATEGY} — пересоберите на дне 21."}
    return {**state, "ready": True, "chunks": strategy["chunks"], "documents": info["corpus"]["documents"]}


@bp.get("/")
def index():
    return render_template(
        "days/day22.html",
        active_day=get_day(22),
        state=_index_state(_index(), _embedder()),
        probes=[{"index": number, **probe.as_dict()} for number, probe in enumerate(PROBES, start=1)],
        modes=[{"key": mode, "title": MODE_TITLES[mode]} for mode in MODES],
        strategy=STRATEGY,
        top_k=TOP_K,
        model=MODEL,
    )


def _stream(work: Callable[[Callable[[dict[str, Any]], None]], None]) -> Response:
    """Работа идёт в своём потоке, события — через очередь: страница видит их сразу."""
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def worker() -> None:
        try:
            work(events.put)
        except (IndexNotReady, EmbeddingError, TransportError) as error:
            events.put({"type": "error", "error": str(error)})
        except Exception as error:  # noqa: BLE001 — до страницы должна дойти любая причина
            events.put({"type": "error", "error": f"{type(error).__name__}: {error}"})
        finally:
            events.put(None)

    threading.Thread(target=worker, name="rag-answer", daemon=True).start()

    def generate() -> Iterator[str]:
        while (event := events.get()) is not None:
            yield json.dumps(event, ensure_ascii=False) + "\n"

    response = Response(stream_with_context(generate()), mimetype="application/x-ndjson")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@bp.post("/api/ask")
def ask_question():
    """Один вопрос: retrieved → delta… обоих режимов вперемешку → answer × 2 → done."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Ожидался JSON-объект с полем question"}), 400
    question = str(data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "Введите вопрос"}), 400
    if len(question) > MAX_QUESTION:
        return jsonify({"error": f"Вопрос длиннее {MAX_QUESTION} символов"}), 400
    try:
        k = min(max(int(data.get("k", TOP_K)), 1), MAX_K)
    except (TypeError, ValueError):
        return jsonify({"error": "k — целое число"}), 400

    index, embedder, transport = _index(), _embedder(), _transport()
    state = _index_state(index, embedder)
    if not state["ready"]:
        return jsonify({"error": state["reason"]}), 409
    probe = find_probe(question)

    def work(emit):
        if probe is not None:
            emit({"type": "probe", "index": PROBES.index(probe) + 1, **probe.as_dict()})
        ask(question, index=index, embedder=embedder, transport=transport, emit=emit, k=k, probe=probe)
        emit({"type": "done"})

    return _stream(work)


@bp.post("/api/compare")
def compare():
    """Десять контрольных вопросов по очереди, оба режима, сверка с ожиданием, итог."""
    index, embedder, transport = _index(), _embedder(), _transport()
    state = _index_state(index, embedder)
    if not state["ready"]:
        return jsonify({"error": state["reason"]}), 409

    def work(emit):
        for event in iter_compare(index=index, embedder=embedder, transport=transport):
            emit(event)

    return _stream(work)


@bp.get("/api/probes")
def probes():
    return jsonify([{"index": number, **probe.as_dict()} for number, probe in enumerate(PROBES, start=1)])

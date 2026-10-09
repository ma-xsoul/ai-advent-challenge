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

from flask import Blueprint, jsonify, render_template, request

from ...navigation import get_day
from ...rag import PROBES
from ...rag.answer import MAX_K, MODE_TITLES, MODEL, MODES, STRATEGY, TOP_K, ask, find_probe, iter_compare
from ..rag_pages import embedder, index_state, ndjson_stream, rag_index, transport


bp = Blueprint("day22", __name__, url_prefix="/days/22")

MAX_QUESTION = 500


@bp.get("/")
def index():
    return render_template(
        "days/day22.html",
        active_day=get_day(22),
        state=index_state(rag_index(), embedder()),
        probes=[{"index": number, **probe.as_dict()} for number, probe in enumerate(PROBES, start=1)],
        modes=[{"key": mode, "title": MODE_TITLES[mode]} for mode in MODES],
        strategy=STRATEGY,
        top_k=TOP_K,
        model=MODEL,
    )


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

    index, embedder_, transport_ = rag_index(), embedder(), transport()
    state = index_state(index, embedder_)
    if not state["ready"]:
        return jsonify({"error": state["reason"]}), 409
    probe = find_probe(question)

    def work(emit):
        if probe is not None:
            emit({"type": "probe", "index": PROBES.index(probe) + 1, **probe.as_dict()})
        ask(question, index=index, embedder=embedder_, transport=transport_, emit=emit, k=k, probe=probe)
        emit({"type": "done"})

    return ndjson_stream(work)


@bp.post("/api/compare")
def compare():
    """Десять контрольных вопросов по очереди, оба режима, сверка с ожиданием, итог."""
    index, embedder_, transport_ = rag_index(), embedder(), transport()
    state = index_state(index, embedder_)
    if not state["ready"]:
        return jsonify({"error": state["reason"]}), 409

    def work(emit):
        for event in iter_compare(index=index, embedder=embedder_, transport=transport_):
            emit(event)

    return ndjson_stream(work)


@bp.get("/api/probes")
def probes():
    return jsonify([{"index": number, **probe.as_dict()} for number, probe in enumerate(PROBES, start=1)])

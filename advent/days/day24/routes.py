"""
День 24: цитаты, источники и анти-галлюцинации.

Ссылка [n] — ещё не доказательство: модель может сослаться на кусок и
написать то, чего в нём нет. Здесь (advent/rag/grounded.py) ответ обязан
прийти с цитатами из найденных кусков: код ищет каждую цитату в её куске,
источники (source, section, chunk_id) подставляет сам, а отдельный
запрос-судья решает, подтверждают ли цитаты смысл ответа. Если после
порога и реранкера дня 23 не осталось ни одного куска — релевантность
ниже порога: ассистент говорит «не знаю» и просит уточнить вопрос.

Индекс общий с днём 21 и собирается там.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, render_template, request

from ...navigation import get_day
from ...rag.answer import MODEL
from ...rag.grounded import QUOTE_TITLES, ask, iter_compare
from ...rag.rerank import ALL_PROBES, STRATEGY, Settings
from ..day22.routes import MAX_QUESTION
from ..rag_pages import embedder, index_state, ndjson_stream, rag_index, transport


bp = Blueprint("day24", __name__, url_prefix="/days/24")


def _probe_dict(number: int, probe) -> dict[str, Any]:
    return {"index": number, "negative": not probe.sources, **probe.as_dict()}


@bp.get("/")
def index():
    return render_template(
        "days/day24.html",
        active_day=get_day(24),
        state=index_state(rag_index(), embedder()),
        probes=[_probe_dict(number, probe) for number, probe in enumerate(ALL_PROBES, start=1)],
        settings=Settings().as_dict(),
        limits=Settings.LIMITS,
        quote_titles=QUOTE_TITLES,
        strategy=STRATEGY,
        model=MODEL,
    )


def _prepare(data: Any):
    """Настройки и всё, что нужно для запроса; ValueError — 400, LookupError — 409."""
    if not isinstance(data, dict):
        raise ValueError("Ожидался JSON-объект")
    settings = Settings.parse(data.get("settings"))
    index, embedder_ = rag_index(), embedder()
    state = index_state(index, embedder_)
    if not state["ready"]:
        raise LookupError(state["reason"])
    return settings, index, embedder_, transport()


@bp.post("/api/ask")
def ask_question():
    """Один вопрос: probe? → retrieved → answer → judge? → checks → done."""
    data = request.get_json(silent=True)
    question = str(data.get("question") or "").strip() if isinstance(data, dict) else ""
    if not question:
        return jsonify({"error": "Введите вопрос"}), 400
    if len(question) > MAX_QUESTION:
        return jsonify({"error": f"Вопрос длиннее {MAX_QUESTION} символов"}), 400
    try:
        settings, index, embedder_, transport_ = _prepare(data)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except LookupError as error:
        return jsonify({"error": str(error)}), 409

    probe = next((p for p in ALL_PROBES if p.question.casefold() == question.casefold()), None)

    def work(emit):
        if probe is not None:
            emit({"type": "probe", **_probe_dict(ALL_PROBES.index(probe) + 1, probe)})
        ask(question, index=index, embedder=embedder_, transport=transport_, settings=settings, emit=emit, probe=probe)
        emit({"type": "done"})

    return ndjson_stream(work)


@bp.post("/api/compare")
def compare():
    """Тринадцать контрольных вопросов: источники, цитаты, смысл, «не знаю»."""
    try:
        settings, index, embedder_, transport_ = _prepare(request.get_json(silent=True) or {})
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except LookupError as error:
        return jsonify({"error": str(error)}), 409

    def work(emit):
        for event in iter_compare(index=index, embedder=embedder_, transport=transport_, settings=settings):
            emit(event)

    return ndjson_stream(work)

"""
День 23: реранкинг и фильтрация — второй этап после поиска.

Три режима на одном вопросе (advent/rag/rerank.py):

- без фильтра — как в дне 22: top-5 по вектору сразу модели;
- порог + реранк — top-20 кандидатов → порог похожести → оценка
  DeepSeek 0–10 → лучшие 5;
- + query rewrite — то же, но кандидаты ищутся и по вопросу, и по его
  переписанной версии.

Страница показывает воронку каждого кандидата (похожесть, оценка
реранкера, где отсеян), три ответа рядом и прогон тринадцати
контрольных вопросов: десять по базе из дня 22 и три вне её. Ручки —
порог, топ-K до и после, порог реранкера — приходят со страницы.

Индекс общий с днём 21 и собирается там.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, render_template, request

from ...navigation import get_day
from ...rag.answer import MODEL
from ...rag.rerank import ALL_PROBES, MODE_TITLES, MODES, STRATEGY, Settings, iter_compare, run_question
from ..day22.routes import MAX_QUESTION
from ..rag_pages import embedder, index_state, ndjson_stream, rag_index, transport


bp = Blueprint("day23", __name__, url_prefix="/days/23")


def _probe_dict(number: int, probe) -> dict[str, Any]:
    return {"index": number, "negative": not probe.sources, **probe.as_dict()}


@bp.get("/")
def index():
    return render_template(
        "days/day23.html",
        active_day=get_day(23),
        state=index_state(rag_index(), embedder()),
        probes=[_probe_dict(number, probe) for number, probe in enumerate(ALL_PROBES, start=1)],
        modes=[{"key": mode, "title": MODE_TITLES[mode]} for mode in MODES],
        settings=Settings().as_dict(),
        limits=Settings.LIMITS,
        strategy=STRATEGY,
        model=MODEL,
    )


def _prepare(data: Any) -> tuple[Settings, tuple]:
    """Настройки и всё, что нужно для запроса. Ошибки — (ответ, код)."""
    if not isinstance(data, dict):
        raise ValueError("Ожидался JSON-объект")
    settings = Settings.parse(data.get("settings"))
    index, embedder_ = rag_index(), embedder()
    state = index_state(index, embedder_)
    if not state["ready"]:
        raise LookupError(state["reason"])
    return settings, (index, embedder_, transport())


@bp.post("/api/ask")
def ask_question():
    """Один вопрос: probe? → funnel × 3 → delta… → answer × 3 → done."""
    data = request.get_json(silent=True)
    question = str(data.get("question") or "").strip() if isinstance(data, dict) else ""
    if not question:
        return jsonify({"error": "Введите вопрос"}), 400
    if len(question) > MAX_QUESTION:
        return jsonify({"error": f"Вопрос длиннее {MAX_QUESTION} символов"}), 400
    try:
        settings, (index, embedder_, transport_) = _prepare(data)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except LookupError as error:
        return jsonify({"error": str(error)}), 409

    probe = next((p for p in ALL_PROBES if p.question.casefold() == question.casefold()), None)

    def work(emit):
        if probe is not None:
            emit({"type": "probe", **_probe_dict(ALL_PROBES.index(probe) + 1, probe)})
        emit({"type": "settings", **settings.as_dict()})
        run_question(question, index=index, embedder=embedder_, transport=transport_, settings=settings, emit=emit, probe=probe)
        emit({"type": "done"})

    return ndjson_stream(work)


@bp.post("/api/compare")
def compare():
    """Тринадцать контрольных вопросов, три режима, итог."""
    try:
        settings, (index, embedder_, transport_) = _prepare(request.get_json(silent=True) or {})
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except LookupError as error:
        return jsonify({"error": str(error)}), 409

    def work(emit):
        for event in iter_compare(index=index, embedder=embedder_, transport=transport_, settings=settings):
            emit(event)

    return ndjson_stream(work)

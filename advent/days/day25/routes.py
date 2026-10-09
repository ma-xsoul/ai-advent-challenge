"""
День 25: мини-чат с RAG и памятью — всё вместе.

Чат (advent/rag/chat.py) хранит историю диалога, на каждое сообщение
ищет контекст в базе проекта воронкой дня 23 и отвечает с цитатами и
источниками как в дне 24, а «память задачи» — цель, уточнения,
ограничения, термины — обновляется каждый ход и уходит в промпт.

Два длинных сценария по 12 сообщений прогоняются одной кнопкой: по
каждому ходу видно, есть ли источники и держится ли цель, а в конце —
итог: назвал ли ассистент цель и ограничения, когда его попросили.

Диалог — <сессия>-d25.json рядом с диалогами других дней, кнопка
«сбросить всё» сносит и его. Индекс общий с днём 21.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, jsonify, render_template, request

from ...navigation import get_day
from ...rag.answer import MODEL
from ...rag.chat import SCENARIOS, ChatStore, RagChat, get_scenario, scenario_summary, session_lock, turn
from ...rag.rerank import STRATEGY, Settings
from ..agent_chat import remember_session, session_id
from ..day22.routes import MAX_QUESTION
from ..rag_pages import embedder, index_state, ndjson_stream, rag_index, transport


bp = Blueprint("day25", __name__, url_prefix="/days/25")


def _store() -> ChatStore:
    return ChatStore(current_app.config["AGENT_STORE_DIR"])


def _state(chat: RagChat) -> dict[str, Any]:
    scenario = get_scenario(chat.scenario) if chat.scenario else None
    return {
        "messages": chat.messages,
        "memory": chat.memory.as_dict(),
        "scenario": chat.scenario,
        "summary": scenario_summary(chat, scenario) if scenario and chat.turns == len(scenario.messages) else None,
    }


@bp.get("/")
def index():
    sid = session_id()
    response = current_app.make_response(
        render_template(
            "days/day25.html",
            active_day=get_day(25),
            state=index_state(rag_index(), embedder()),
            chat=_state(_store().load(sid)),
            scenarios=[scenario.as_dict() for scenario in SCENARIOS],
            settings=Settings().as_dict(),
            strategy=STRATEGY,
            model=MODEL,
        )
    )
    remember_session(response, sid)
    return response


@bp.get("/api/state")
def state():
    return jsonify(_state(_store().load(session_id())))


def _ready():
    index, embedder_ = rag_index(), embedder()
    ready = index_state(index, embedder_)
    if not ready["ready"]:
        return None, (jsonify({"error": ready["reason"]}), 409)
    return (index, embedder_, transport()), None


@bp.post("/api/chat")
def chat():
    """Один ход: user → memory → query → retrieved → answer → done. settings — как в днях 23–24, необязательно."""
    data = request.get_json(silent=True)
    message = str(data.get("message") or "").strip() if isinstance(data, dict) else ""
    if not message:
        return jsonify({"error": "Введите сообщение"}), 400
    if len(message) > MAX_QUESTION:
        return jsonify({"error": f"Сообщение длиннее {MAX_QUESTION} символов"}), 400
    try:
        settings = Settings.parse(data.get("settings"))
    except ValueError as problem:
        return jsonify({"error": str(problem)}), 400
    tools, error = _ready()
    if error:
        return error
    sid, store = session_id(), _store()
    lock = session_lock(sid)
    if lock.locked():
        return jsonify({"error": "Предыдущий ход ещё идёт"}), 409

    def work(emit):
        with lock:
            dialog = store.load(sid)
            scenario = get_scenario(dialog.scenario) if dialog.scenario else None
            turn(dialog, message, index=tools[0], embedder=tools[1], transport=tools[2], settings=settings, emit=emit,
                 goal_keywords=scenario.goal_keywords if scenario else ())
            store.save(sid, dialog)
        emit({"type": "done"})

    response = ndjson_stream(work)
    remember_session(response, sid)
    return response


@bp.post("/api/scenario")
def scenario():
    """Длинный сценарий с чистого листа: ходы один за другим, потом итог."""
    data = request.get_json(silent=True)
    chosen = get_scenario(str(data.get("key") or "")) if isinstance(data, dict) else None
    if chosen is None:
        return jsonify({"error": "Такого сценария нет"}), 404
    try:
        settings = Settings.parse(data.get("settings"))
    except ValueError as problem:
        return jsonify({"error": str(problem)}), 400
    tools, error = _ready()
    if error:
        return error
    sid, store = session_id(), _store()
    lock = session_lock(sid)
    if lock.locked():
        return jsonify({"error": "Предыдущий ход ещё идёт"}), 409

    def work(emit):
        with lock:
            dialog = RagChat(scenario=chosen.key)
            store.save(sid, dialog)
            emit({"type": "scenario_start", "key": chosen.key, "title": chosen.title, "total": len(chosen.messages)})
            for message in chosen.messages:
                turn(dialog, message, index=tools[0], embedder=tools[1], transport=tools[2], settings=settings, emit=emit,
                     goal_keywords=chosen.goal_keywords)
                # Сохраняем после каждого хода: перезагрузка страницы посреди прогона не теряет сделанное.
                store.save(sid, dialog)
            emit({"type": "scenario_summary", "key": chosen.key, **scenario_summary(dialog, chosen)})
        emit({"type": "done"})

    response = ndjson_stream(work)
    remember_session(response, sid)
    return response


@bp.post("/api/reset")
def reset():
    """Новый диалог: история и память задачи — с чистого листа."""
    sid = session_id()
    if session_lock(sid).locked():
        return jsonify({"error": "Ход ещё идёт — дождитесь ответа"}), 409
    deleted = _store().delete(sid)
    response = jsonify({"deleted": deleted, **_state(RagChat())})
    remember_session(response, sid)
    return response

from dataclasses import asdict

from flask import Blueprint, jsonify, make_response, render_template, request

from ...agent import StoreError, get_scenario
from ...navigation import get_day
from ..agent_chat import (
    message_from_request,
    registry,
    remember_session,
    session_id,
    state as snapshot_state,
    store_error_response,
    stream_chat,
)


bp = Blueprint("day08", __name__, url_prefix="/days/8")

# Лимит контекста в токенах. Верхняя граница — окно deepseek-v4-flash;
# нижняя — чтобы хоть один ход помещался вместе с системным промптом.
LIMIT_MIN = 100
LIMIT_MAX = 128_000

# Сценарий дня: «собираем ТЗ» из примера в задании десятого дня.
SCENARIO_KEY = "spec"


@bp.get("/")
def index():
    sid = session_id()
    try:
        agent = registry().peek(sid)
    except StoreError as error:
        return (
            render_template("days/day08.html", active_day=get_day(8), model="—", snapshot=None, store_error=str(error), scenario=None),
            500,
        )

    response = make_response(
        render_template(
            "days/day08.html",
            active_day=get_day(8),
            model=agent.config.model,
            snapshot=snapshot_state(registry(), sid, agent),
            store_error=None,
            scenario=asdict(get_scenario(SCENARIO_KEY)),
        )
    )
    remember_session(response, sid)
    return response


@bp.get("/api/state")
def state():
    sid = session_id()
    try:
        agent = registry().peek(sid)
    except StoreError as error:
        return store_error_response(error)
    response = jsonify(snapshot_state(registry(), sid, agent))
    remember_session(response, sid)
    return response


@bp.post("/api/reset")
def reset():
    sid = session_id()
    agent = registry().reset(sid)
    response = jsonify(snapshot_state(registry(), sid, agent))
    remember_session(response, sid)
    return response


@bp.post("/api/restart")
def restart():
    sid = session_id()
    reg = registry()
    reg.evict(sid)
    try:
        agent = reg.get(sid)
    except StoreError as error:
        return store_error_response(error)
    response = jsonify(snapshot_state(reg, sid, agent))
    remember_session(response, sid)
    return response


@bp.post("/api/limit")
def set_limit():
    """
    Сменить лимит контекста, не теряя диалог. Пустое значение — снять
    лимит. Куратор: окно можно искусственно уменьшить, чтобы увидеть
    переполнение за несколько ходов.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Ожидался JSON-объект с лимитом"}), 400

    limit = payload.get("limit")
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int):
            return jsonify({"error": "Лимит должен быть целым числом токенов или null"}), 400
        if not LIMIT_MIN <= limit <= LIMIT_MAX:
            return jsonify({"error": f"Лимит должен быть от {LIMIT_MIN} до {LIMIT_MAX} токенов"}), 400

    sid = session_id()
    reg = registry()
    try:
        agent = reg.reconfigure(sid, context_limit=limit)
    except StoreError as error:
        return store_error_response(error)
    reg.save(sid)
    response = jsonify(snapshot_state(reg, sid, agent))
    remember_session(response, sid)
    return response


@bp.get("/api/scenario")
def scenario():
    return jsonify(asdict(get_scenario(SCENARIO_KEY)))


@bp.post("/api/chat/stream")
def chat_stream():
    text, error_response = message_from_request()
    if error_response is not None:
        return error_response

    sid = session_id()
    try:
        agent = registry().get(sid)
    except StoreError as error:
        return store_error_response(error)
    return stream_chat(registry(), sid, agent, text)

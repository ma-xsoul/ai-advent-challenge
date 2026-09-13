from flask import Blueprint, jsonify, make_response, render_template

from ...agent import StoreError
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


bp = Blueprint("day07", __name__, url_prefix="/days/7")


@bp.get("/")
def index():
    sid = session_id()
    try:
        agent = registry().peek(sid)
    except StoreError as error:
        return (
            render_template("days/day07.html", active_day=get_day(7), model="—", snapshot=None, store_error=str(error)),
            500,
        )

    response = make_response(
        render_template(
            "days/day07.html",
            active_day=get_day(7),
            model=agent.config.model,
            snapshot=snapshot_state(registry(), sid, agent),
            store_error=None,
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
    """
    Имитация перезапуска процесса: агент выкидывается из памяти, следующий
    запрос читает его с диска. Честная проверка — убить сервер — тоже
    работает: страница при загрузке отдаёт восстановленный стек.
    """
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

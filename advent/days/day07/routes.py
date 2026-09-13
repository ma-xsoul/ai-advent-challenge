import json
import uuid
from dataclasses import asdict

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    make_response,
    render_template,
    request,
    stream_with_context,
)

from ...agent import Agent, AgentRegistry, StoreError, TransportError
from ...navigation import get_day


bp = Blueprint("day07", __name__, url_prefix="/days/7")

# Та же cookie, что у дня 6: одна сессия браузера, но реестр другой —
# с диском, поэтому агент здесь свой.
SESSION_COOKIE = "agent_session"


@bp.get("/")
def index():
    session_id = _session_id()
    try:
        agent = _registry().peek(session_id)
    except StoreError as error:
        return render_template("days/day07.html", active_day=get_day(7), model="—", snapshot=None, store_error=str(error)), 500

    response = make_response(
        render_template(
            "days/day07.html",
            active_day=get_day(7),
            model=agent.config.model,
            snapshot=_state(session_id, agent),
            store_error=None,
        )
    )
    _remember_session(response, session_id)
    return response


@bp.get("/api/state")
def state():
    session_id = _session_id()
    try:
        agent = _registry().peek(session_id)
    except StoreError as error:
        return jsonify({"error": str(error)}), 500
    response = jsonify(_state(session_id, agent))
    _remember_session(response, session_id)
    return response


@bp.post("/api/reset")
def reset():
    session_id = _session_id()
    agent = _registry().reset(session_id)
    response = jsonify(_state(session_id, agent))
    _remember_session(response, session_id)
    return response


@bp.post("/api/restart")
def restart():
    """
    Имитация перезапуска процесса: агент выкидывается из памяти, следующий
    запрос читает его с диска. Честная проверка — убить сервер — тоже
    работает: страница при загрузке отдаёт восстановленный стек.
    """
    session_id = _session_id()
    registry = _registry()
    registry.evict(session_id)
    try:
        agent = registry.get(session_id)
    except StoreError as error:
        return jsonify({"error": str(error)}), 500
    response = jsonify(_state(session_id, agent))
    _remember_session(response, session_id)
    return response


@bp.post("/api/chat/stream")
def chat_stream():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Ожидался JSON-объект с сообщением"}), 400

    text = payload.get("message", "")
    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "Введите сообщение"}), 400
    if len(text.strip()) > 10_000:
        return jsonify({"error": "Сообщение не должно превышать 10 000 символов"}), 400

    session_id = _session_id()
    registry = _registry()
    try:
        agent = registry.get(session_id)
    except StoreError as error:
        return jsonify({"error": str(error)}), 500

    def generate_events():
        try:
            reply = yield from _stream_reply(agent, text)
        except TransportError as error:
            yield _event({"type": "error", "error": str(error), "snapshot": _state(session_id, agent)})
            return
        # Ответ дошёл целиком — стек на диск. Ошибка записи не должна
        # прятать уже полученный ответ, поэтому она уходит отдельным полем.
        save_error = None
        try:
            registry.save(session_id)
        except OSError as error:
            save_error = f"Ответ получен, но не сохранён на диск: {error}"
        yield _event(
            {
                "type": "done",
                "reply": {
                    "content": reply.content,
                    "finish_reason": reply.finish_reason,
                    "model": reply.model,
                    "input_tokens": reply.input_tokens,
                    "output_tokens": reply.output_tokens,
                },
                "snapshot": _state(session_id, agent),
                "save_error": save_error,
            }
        )

    response = Response(
        stream_with_context(generate_events()),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    _remember_session(response, session_id)
    return response


def _stream_reply(agent: Agent, text: str):
    generator = agent.stream(text)
    while True:
        try:
            chunk = next(generator)
        except StopIteration as stop:
            return stop.value
        yield _event({"type": "delta", "content": chunk})


def _state(session_id: str, agent: Agent) -> dict:
    """Слепок агента плюс то, что знает только приложение: реестр и файл."""
    registry = _registry()
    snapshot = agent.snapshot()
    snapshot["agents_in_registry"] = len(registry)
    stored = registry.stored(session_id)
    snapshot["storage"] = asdict(stored) if stored else None
    return snapshot


def _event(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False) + "\n"


def _registry() -> AgentRegistry:
    return current_app.extensions["persistent_agent_registry"]


def _session_id() -> str:
    return request.cookies.get(SESSION_COOKIE) or uuid.uuid4().hex


def _remember_session(response, session_id: str) -> None:
    if request.cookies.get(SESSION_COOKIE) != session_id:
        response.set_cookie(SESSION_COOKIE, session_id, samesite="Lax", max_age=60 * 60 * 24 * 30)

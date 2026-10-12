"""День 27: локальный чат с историей и потоковыми ответами через Ollama."""

from __future__ import annotations

import re
from threading import Lock

from flask import Blueprint, current_app, jsonify, make_response, render_template

from ...agent import StoreError
from ...local import OllamaClient, OllamaError
from ...navigation import get_day
from ..agent_chat import (
    message_from_request, registry, remember_session, session_id,
    state as snapshot_state, store_error_response, stream_chat,
)


bp = Blueprint("day27", __name__, url_prefix="/days/27")
REGISTRY = "local_agent_registry"
SUFFIX = "-d27"
SAFE_SESSION = re.compile(r"^[A-Za-z0-9_-]{1,124}$")


def _identity():
    sid = session_id()
    if not SAFE_SESSION.fullmatch(sid):
        return None, None, (jsonify({"error": "Недопустимый идентификатор сессии"}), 400)
    return sid, sid + SUFFIX, None


def _lock(store_id):
    with current_app.extensions["local_agent_locks_guard"]:
        return current_app.extensions["local_agent_locks"].setdefault(store_id, Lock())


def _busy():
    return jsonify({"error": "Дождитесь завершения текущего ответа"}), 409


@bp.get("/")
def index():
    sid, store_id, error = _identity()
    if error is not None:
        return error
    reg = registry(REGISTRY)
    snapshot = None
    store_error = None
    model = current_app.config.get("DAY27_MODEL", "gpt-oss:20b")
    try:
        agent = reg.peek(store_id)
        model = agent.config.model
        snapshot = snapshot_state(reg, store_id, agent)
    except StoreError as error:
        store_error = str(error)
    response = make_response(render_template(
        "days/day27.html", active_day=get_day(27), model=model,
        snapshot=snapshot, store_error=store_error,
    ), 500 if store_error else 200)
    remember_session(response, sid)
    return response


@bp.get("/api/state")
def state():
    sid, store_id, error = _identity()
    if error is not None:
        return error
    reg = registry(REGISTRY)
    try:
        agent = reg.peek(store_id)
        response = jsonify(snapshot_state(reg, store_id, agent))
    except StoreError as error:
        return store_error_response(error)
    remember_session(response, sid)
    return response


@bp.get("/api/status")
def status():
    client = current_app.config.get("OLLAMA_CLIENT") or OllamaClient()
    sid, store_id, error = _identity()
    if error is not None:
        return error
    try:
        model = registry(REGISTRY).peek(store_id).config.model
    except StoreError as error:
        return jsonify({"ok": False, "error": str(error)}), 500
    base = {"url": client.url, "model": model}
    try:
        version = client.version()
        installed = any(item["name"] == model and item["chat"] for item in client.models())
        return jsonify({**base, "ok": installed, "installed": installed, "version": version,
                        "error": None if installed else f"В Ollama нет чат-модели {model}. Выберите установленную модель в настройках приложения."})
    except OllamaError as error:
        return jsonify({**base, "ok": False, "error": str(error)})


@bp.post("/api/reset")
def reset():
    sid, store_id, error = _identity()
    if error is not None:
        return error
    lock = _lock(store_id)
    if not lock.acquire(blocking=False):
        return _busy()
    try:
        reg = registry(REGISTRY)
        agent = reg.reset(store_id)
        response = jsonify(snapshot_state(reg, store_id, agent))
        remember_session(response, sid)
        return response
    except StoreError as error:
        return store_error_response(error)
    except OSError as error:
        return jsonify({"error": f"Не удалось очистить диалог: {error}"}), 500
    finally:
        lock.release()


@bp.post("/api/chat/stream")
def chat_stream():
    text, error = message_from_request()
    if error is not None:
        return error
    sid, store_id, error = _identity()
    if error is not None:
        return error
    lock = _lock(store_id)
    if not lock.acquire(blocking=False):
        return _busy()
    try:
        reg = registry(REGISTRY)
        agent = reg.get(store_id)
        response = stream_chat(reg, sid, agent, text, store_id=store_id)
    except StoreError as error:
        lock.release()
        return store_error_response(error)
    except BaseException:
        lock.release()
        raise

    body = response.response
    release_guard = Lock()
    released = False

    def finish():
        nonlocal released
        try:
            close = getattr(body, "close", None)
            if close is not None:
                close()
        finally:
            with release_guard:
                if not released:
                    released = True
                    lock.release()

    def guarded():
        try:
            yield from body
        finally:
            finish()

    response.response = guarded()
    # Даже закрытый до первого чтения Response должен отпустить блокировку.
    response.call_on_close(finish)
    return response

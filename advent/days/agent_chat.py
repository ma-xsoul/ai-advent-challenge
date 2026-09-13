"""
Общая часть маршрутов чата с агентом для дней 7+.

Каждый день добавляет своё — лимит, стратегию, сценарий, — а сессия,
поток ответа, сохранение и слепок состояния у всех одинаковые.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from flask import Response, current_app, jsonify, request, stream_with_context

from ..agent import Agent, AgentRegistry, ContextOverflowError, StoreError, TransportError


SESSION_COOKIE = "agent_session"
MAX_MESSAGE_LENGTH = 10_000


def registry(name: str = "persistent_agent_registry") -> AgentRegistry:
    return current_app.extensions[name]


def session_id() -> str:
    return request.cookies.get(SESSION_COOKIE) or uuid.uuid4().hex


def remember_session(response, sid: str) -> None:
    if request.cookies.get(SESSION_COOKIE) != sid:
        response.set_cookie(SESSION_COOKIE, sid, samesite="Lax", max_age=60 * 60 * 24 * 30)


def state(reg: AgentRegistry, sid: str, agent: Agent) -> dict[str, Any]:
    """Слепок агента плюс то, что знает только приложение: реестр и файл."""
    snapshot = agent.snapshot()
    snapshot["agents_in_registry"] = len(reg)
    stored = reg.stored(sid)
    snapshot["storage"] = asdict(stored) if stored else None
    return snapshot


def message_from_request() -> tuple[str | None, tuple[Any, int] | None]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, (jsonify({"error": "Ожидался JSON-объект с сообщением"}), 400)

    text = payload.get("message", "")
    if not isinstance(text, str) or not text.strip():
        return None, (jsonify({"error": "Введите сообщение"}), 400)
    if len(text.strip()) > MAX_MESSAGE_LENGTH:
        return None, (
            jsonify({"error": f"Сообщение не должно превышать {MAX_MESSAGE_LENGTH:,} символов".replace(",", " ")}),
            400,
        )
    return text.strip(), None


def stream_chat(
    reg: AgentRegistry,
    sid: str,
    agent: Agent,
    text: str,
    *,
    state_fn: Callable[[Agent], dict[str, Any]] | None = None,
) -> Response:
    """
    Потоковый ответ агента в NDJSON. После полного ответа стек уходит на
    диск; ошибка записи не прячет уже полученный ответ и уезжает отдельным
    полем. Переполнение контекста — отдельное событие с числами.
    """
    snapshot_of = state_fn or (lambda a: state(reg, sid, a))

    def generate_events():
        try:
            reply = yield from _stream_reply(agent, text)
        except ContextOverflowError as error:
            yield _event(
                {
                    "type": "error",
                    "error": str(error),
                    "overflow": {"estimated": error.estimated, "limit": error.limit},
                    "snapshot": snapshot_of(agent),
                }
            )
            return
        except TransportError as error:
            yield _event({"type": "error", "error": str(error), "snapshot": snapshot_of(agent)})
            return

        save_error = None
        try:
            reg.save(sid)
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
                "turn": agent.turns[-1].as_dict() if agent.turns else None,
                "snapshot": snapshot_of(agent),
                "save_error": save_error,
            }
        )

    response = Response(
        stream_with_context(generate_events()),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    remember_session(response, sid)
    return response


def _stream_reply(agent: Agent, text: str):
    generator = agent.stream(text)
    while True:
        try:
            chunk = next(generator)
        except StopIteration as stop:
            return stop.value
        yield _event({"type": "delta", "content": chunk})


def _event(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False) + "\n"


def store_error_response(error: StoreError):
    return jsonify({"error": str(error)}), 500

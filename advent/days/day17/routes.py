"""
День 17: первый инструмент MCP — свой сервер и вызов из агента.

Свой MCP-сервер вокруг публичного GitHub API (advent/mcp/servers/github.py)
поднимается процессом на каждый ход. Агент берёт у него tools/list, отдаёт
схемы модели, и дальше модель сама решает: ответить текстом или попросить
вызов. Вызов делает агент через MCP, результат возвращается модели, и она
строит ответ. Страница видит каждый шаг потоком: что попросила модель,
с какими аргументами, что вернул сервер и чем кончилось.

Диалог дня 17 хранится отдельно от остальных: <сессия>-d17.json — в нём
есть сообщения с tool_calls и role=tool, которых нет у обычного агента.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, make_response, render_template, stream_with_context

from ...agent import AgentConfig, DeepSeekTransport, get_scenario
from ...mcp import GITHUB_SERVER, inspect_server
from ...mcp.agent import MAX_ROUNDS, DialogError, ToolDialog, ToolDialogStore, stream_turn
from ...navigation import get_day
from ..agent_chat import message_from_request, remember_session, session_id


bp = Blueprint("day17", __name__, url_prefix="/days/17")

SCENARIO_KEY = "mcp-github"
STORE_NAME = "d17"

CONFIG = AgentConfig(
    name="d17",
    system_prompt=(
        "Ты помощник в учебной лаборатории. У тебя есть инструменты GitHub через MCP. "
        "Пиши только по-русски — и ответ, и короткую фразу перед вызовом инструмента. "
        "Если для ответа нужны данные GitHub — репозитории, звёзды, даты, issues, лицензии, — "
        "вызови инструмент, а не отвечай по памяти. Если вопрос не про GitHub — отвечай сам, "
        "без инструментов. В ответе опирайся на результат инструмента и давай ссылки на репозитории."
    ),
    temperature=0.3,
    max_tokens=900,
)


def _store() -> ToolDialogStore:
    return ToolDialogStore(current_app.config["AGENT_STORE_DIR"], STORE_NAME)


def _transport():
    return current_app.config.get("AGENT_TRANSPORT") or DeepSeekTransport()


def _factory():
    # Тесты подставляют сервер в памяти процесса — без сети и без процесса.
    return current_app.config.get("MCP_TRANSPORT_FACTORY")


def _state(sid: str, dialog: ToolDialog) -> dict[str, Any]:
    """Что нужно странице, чтобы восстановить ленту после перезагрузки."""
    calls = [{key: value for key, value in call.items() if key != "for_model"} for call in dialog.calls]
    return {
        "messages": dialog.messages,
        "calls": calls,
        "turns": dialog.turns,
        "summary": {
            "turns": len(dialog.turns),
            "messages": len(dialog.messages),
            "tool_calls": len(dialog.calls),
            "tool_errors": sum(not call.get("ok") for call in dialog.calls),
            "llm_calls": sum(turn.get("llm_calls", 0) for turn in dialog.turns),
            "prompt_tokens": sum(turn.get("prompt_tokens", 0) for turn in dialog.turns),
            "completion_tokens": sum(turn.get("completion_tokens", 0) for turn in dialog.turns),
            "schema_tokens_sent": sum(turn.get("schema_tokens_sent", 0) for turn in dialog.turns),
        },
        "file": _store().describe(sid),
    }


@bp.get("/")
def index():
    sid = session_id()
    store_error = None
    try:
        dialog = _store().load(sid)
    except DialogError as error:
        dialog, store_error = ToolDialog(), str(error)

    response = make_response(
        render_template(
            "days/day17.html",
            active_day=get_day(17),
            server=GITHUB_SERVER,
            scenario=get_scenario(SCENARIO_KEY),
            max_rounds=MAX_ROUNDS,
            state=_state(sid, dialog) if dialog.messages else None,
            store_error=store_error,
        )
    )
    remember_session(response, sid)
    return response


@bp.get("/api/tools")
def tools():
    """tools/list нашего сервера — то, что увидит модель: имена, описания, схемы."""
    result = inspect_server(GITHUB_SERVER, transport_factory=_factory())
    return jsonify(result.as_dict()), 200 if result.ok else 502


@bp.get("/api/state")
def state():
    sid = session_id()
    try:
        dialog = _store().load(sid)
    except DialogError as error:
        return jsonify({"error": str(error)}), 500
    response = jsonify(_state(sid, dialog))
    remember_session(response, sid)
    return response


@bp.post("/api/chat/stream")
def chat_stream():
    text, error_response = message_from_request()
    if error_response is not None:
        return error_response

    sid = session_id()
    store = _store()
    try:
        dialog = store.load(sid)
    except DialogError as error:
        return jsonify({"error": str(error)}), 500

    def save() -> None:
        store.save(sid, dialog)

    events = stream_turn(
        dialog,
        text,
        config=CONFIG,
        transport=_transport(),
        server=GITHUB_SERVER,
        factory=_factory(),
        on_done=save,
    )

    def generate():
        for event in events:
            if event["type"] == "done":
                event = {**event, "state": _state(sid, dialog)}
            yield json.dumps(event, ensure_ascii=False) + "\n"

    response = Response(stream_with_context(generate()), mimetype="application/x-ndjson")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    remember_session(response, sid)
    return response


@bp.post("/api/reset")
def reset():
    sid = session_id()
    _store().delete(sid)
    response = jsonify(_state(sid, ToolDialog()))
    remember_session(response, sid)
    return response


@bp.get("/api/scenario")
def scenario():
    item = get_scenario(SCENARIO_KEY)
    if item is None:
        return jsonify({"error": "Сценарий не найден"}), 404
    return jsonify(asdict(item))

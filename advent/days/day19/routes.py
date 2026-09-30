"""
День 19: композиция MCP-инструментов — цепочку строит агент.

Тот же GitHub-сервер, что в дне 17, но с двумя новыми инструментами:
compare_repositories (обработать) и save_report (сохранить). Порядок
вызовов нигде не прописан — модель по результату поиска сама формулирует
сравнение, по результату сравнения — сохранение. Кругов «модель →
инструмент» за ход больше, чем в дне 17: цепочке нужно три круга на
вызовы и один на ответ.

После хода код сверяет цепочку по журналу вызовов (advent/mcp/chain.py):
тот ли порядок, те ли имена ушли в сравнение, дословно ли таблица попала
в отчёт и совпадает ли файл на диске. Диалог — <сессия>-d19.json, отчёты —
data/day19/reports.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, make_response, render_template, stream_with_context

from ...agent import AgentConfig, DeepSeekTransport, get_scenario
from ...mcp import GITHUB_SERVER, inspect_server
from ...mcp.agent import DialogError, ToolDialog, ToolDialogStore, stream_turn
from ...mcp.chain import check_chain
from ...mcp.servers.github import DEFAULT_REPORTS
from ...navigation import get_day
from ..agent_chat import message_from_request, remember_session, session_id


bp = Blueprint("day19", __name__, url_prefix="/days/19")

SCENARIO_KEY = "mcp-chain"
STORE_NAME = "d19"
MAX_ROUNDS = 6
REPORTS_SHOWN = 10
REPORT_PREVIEW_CHARS = 8000

CONFIG = AgentConfig(
    name="d19",
    system_prompt=(
        "Ты помощник в учебной лаборатории. У тебя есть инструменты GitHub через MCP. Пиши только "
        "по-русски — и ответ, и короткую фразу перед вызовом инструмента. Если задача требует "
        "нескольких шагов, выполняй их инструментами по очереди, сам решая порядок. Данные между "
        "инструментами передавай дословно: имена — как в full_name из результата, таблицы и тексты — "
        "как их вернул инструмент, без пересказа. Цифры бери только из результатов инструментов."
    ),
    temperature=0.3,
    max_tokens=1500,
)


def _store() -> ToolDialogStore:
    return ToolDialogStore(current_app.config["AGENT_STORE_DIR"], STORE_NAME)


def _reports_dir() -> Path:
    return Path(current_app.config.get("DAY19_REPORTS_DIR") or DEFAULT_REPORTS)


def _transport():
    return current_app.config.get("AGENT_TRANSPORT") or DeepSeekTransport()


def _factory():
    return current_app.config.get("MCP_TRANSPORT_FACTORY")


def _reports() -> list[dict[str, Any]]:
    directory = _reports_dir()
    if not directory.is_dir():
        return []
    files = sorted(directory.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)[:REPORTS_SHOWN]
    reports = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        reports.append(
            {
                "file": path.name,
                "bytes": path.stat().st_size,
                "modified": int(path.stat().st_mtime),
                "content": text[:REPORT_PREVIEW_CHARS],
                "truncated": len(text) > REPORT_PREVIEW_CHARS,
            }
        )
    return reports


def _chain(dialog: ToolDialog) -> dict[str, Any] | None:
    """Сверка цепочки последнего хода, в котором вообще были вызовы."""
    if not dialog.calls:
        return None
    last_turn = dialog.calls[-1]["turn"]
    result = check_chain([call for call in dialog.calls if call["turn"] == last_turn], _reports_dir())
    return {**result, "turn": last_turn}


def _state(sid: str, dialog: ToolDialog) -> dict[str, Any]:
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
        "chain": _chain(dialog),
        "reports": _reports(),
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
            "days/day19.html",
            active_day=get_day(19),
            server=GITHUB_SERVER,
            scenario=get_scenario(SCENARIO_KEY),
            max_rounds=MAX_ROUNDS,
            state=_state(sid, dialog),
            store_error=store_error,
        )
    )
    remember_session(response, sid)
    return response


@bp.get("/api/tools")
def tools():
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
        max_rounds=MAX_ROUNDS,
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

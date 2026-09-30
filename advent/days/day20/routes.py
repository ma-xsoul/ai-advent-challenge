"""
День 20: оркестрация MCP — несколько серверов, один агент.

За агентом три сервера сразу: GitMCP (чужой, HTTP), GitHub (наш, stdio,
отчёты — в data/day20/reports) и курс доллара (наш, stdio). Модель видит
инструменты всех трёх с префиксом сервера — github__search_…,
gitmcp__search_… — и называет только инструмент; на какой сервер отправить
вызов, решает агент по префиксу (advent/mcp/agent.py, таблица маршрутов).

После хода advent/mcp/orchestration.py сверяет журнал: у каждого вызова
нашёлся сервер, выбраны ровно те серверы, что нужны просьбе, в GitMCP
ушло имя репозитория из результата GitHub, отчёт сохранён после ответа
GitMCP. Диалог — <сессия>-d20.json.

Сначала вместо GitMCP стоял DeepWiki, но он отвечает своей нейросетью и
бесплатных пользователей быстро режет лимитом (429) — флоу рвался.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, make_response, render_template, stream_with_context

from ...agent import AgentConfig, DeepSeekTransport, get_scenario
from ...mcp import GITHUB_SERVER, GITMCP_SERVER, RATES_SERVER, McpServer, inspect_server
from ...mcp.agent import DialogError, ToolDialog, ToolDialogStore, stream_turn
from ...mcp.client import PROJECT_ROOT
from ...mcp.orchestration import check_flow
from ...navigation import get_day
from ..agent_chat import message_from_request, remember_session, session_id


bp = Blueprint("day20", __name__, url_prefix="/days/20")

SCENARIO_KEY = "mcp-orchestra"
STORE_NAME = "d20"
MAX_ROUNDS = 12
# Чужой сервер по сети: первый поиск GitMCP по документации бывает и 20 с.
TIMEOUT_SECONDS = 120
REPORTS_SHOWN = 10
REPORT_PREVIEW_CHARS = 8000
DEFAULT_REPORTS = PROJECT_ROOT / "data" / "day20" / "reports"

CONFIG = AgentConfig(
    name="d20",
    system_prompt=(
        "Ты помощник в учебной лаборатории. За тобой несколько MCP-серверов, у каждого свои инструменты. "
        "Пиши только по-русски — и ответ, и короткую фразу перед вызовом инструмента. Вызывай только те "
        "серверы и инструменты, которые нужны для этой просьбы: остальные не трогай. Данные между "
        "инструментами передавай дословно. Если сервер не знает объект или вернул ошибку — не "
        "останавливайся, возьми следующий подходящий вариант из уже полученных данных. Цифры и факты "
        "бери только из результатов инструментов."
    ),
    temperature=0.3,
    max_tokens=2000,
)


def _reports_dir() -> Path:
    return Path(current_app.config.get("DAY20_REPORTS_DIR") or DEFAULT_REPORTS)


def _servers() -> list[McpServer]:
    """Реестр дня 20. GitHub — тот же сервер, но с отдельной папкой отчётов."""
    github = replace(GITHUB_SERVER, target=f"{GITHUB_SERVER.target} --reports-dir {shlex.quote(str(_reports_dir()))}")
    return [GITMCP_SERVER, github, RATES_SERVER]


def _store() -> ToolDialogStore:
    return ToolDialogStore(current_app.config["AGENT_STORE_DIR"], STORE_NAME)


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


def _flow(dialog: ToolDialog) -> dict[str, Any] | None:
    """Сверка последнего хода: его вызовы, подключённые серверы и сама просьба."""
    if not dialog.turns:
        return None
    turn = dialog.turns[-1]
    questions = [message["content"] for message in dialog.messages if message["role"] == "user"]
    question = questions[turn["turn"] - 1] if len(questions) >= turn["turn"] else ""
    calls = [call for call in dialog.calls if call.get("turn") == turn["turn"]]
    return {**check_flow(calls, turn.get("servers") or [], question), "turn": turn["turn"], "question": question}


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
        "flow": _flow(dialog),
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
            "days/day20.html",
            active_day=get_day(20),
            servers=_servers(),
            scenario=get_scenario(SCENARIO_KEY),
            max_rounds=MAX_ROUNDS,
            state=_state(sid, dialog),
            store_error=store_error,
        )
    )
    remember_session(response, sid)
    return response


@bp.get("/api/servers")
def servers():
    """tools/list каждого сервера реестра — что увидит модель и во что это обойдётся."""
    cards = []
    for server in _servers():
        result = inspect_server(server, transport_factory=_factory(), timeout=TIMEOUT_SECONDS)
        data = result.as_dict()
        cards.append(
            {
                "key": server.key,
                "title": server.title,
                "transport": server.transport,
                "ok": data["ok"],
                "error": data["error"],
                "elapsed_ms": data["elapsed_ms"],
                "protocol": data["protocol_version"],
                "schema_tokens": data["schema_tokens"],
                "tools": [
                    {"name": f"{server.key}__{tool['name']}", "title": tool["title"], "description": tool["description"]}
                    for tool in data["tools"]
                ],
            }
        )
    return jsonify({"servers": cards, "schema_tokens": sum(card["schema_tokens"] for card in cards)})


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
        servers=_servers(),
        factory=_factory(),
        on_done=save,
        timeout=TIMEOUT_SECONDS,
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

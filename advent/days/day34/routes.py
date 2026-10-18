"""
День 34: ассистент для работы с файлами проекта.

Цель — словами; ассистент (advent/mcp/files_agent.py) сам ищет и читает
файлы через MCP-сервер files и готовит правки в черновике. Страница
показывает след работы, отчёт и diff; применить или выбросить правки
решает человек. Прогоны сохраняются в data/day34/runs.json — ту же цель
можно повторить и сравнить.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, jsonify, render_template, request

from ...local.rag import RunStore
from ...mcp.client import FILES_SERVER, PROJECT_ROOT
from ...mcp.files_agent import MODEL, SCENARIOS, run_goal
from ...mcp.servers.files import DEFAULT_DRAFT, DEFAULT_ROOT, Workspace, WorkspaceError
from ...navigation import get_day
from ..rag_pages import ndjson_stream, transport


bp = Blueprint("day34", __name__, url_prefix="/days/34")

DEFAULT_RUNS = PROJECT_ROOT / "data" / "day34" / "runs.json"
MAX_GOAL = 1500


def _workspace() -> Workspace:
    return Workspace(current_app.config.get("DAY34_ROOT") or DEFAULT_ROOT, current_app.config.get("DAY34_DRAFT") or DEFAULT_DRAFT)


def _runs() -> RunStore:
    return RunStore(current_app.config.get("DAY34_RUNS_PATH") or DEFAULT_RUNS)


def _brief(run: dict[str, Any]) -> dict[str, Any]:
    return {key: run.get(key) for key in ("id", "started_at", "goal", "changes", "stats", "files_read")}


@bp.get("/")
def index():
    return render_template(
        "days/day34.html",
        active_day=get_day(34),
        scenarios=SCENARIOS,
        runs=[_brief(run) for run in _runs().load()],
        changes=_workspace().changes(),
        server=FILES_SERVER.as_dict(),
        model=MODEL,
    )


@bp.post("/api/run")
def run():
    data = request.get_json(silent=True)
    goal = str(data.get("goal") or "").strip() if isinstance(data, dict) else ""
    if not goal:
        return jsonify({"error": "Напишите цель"}), 400
    if len(goal) > MAX_GOAL:
        return jsonify({"error": f"Цель длиннее {MAX_GOAL} символов"}), 400
    workspace, transport_, factory, store = _workspace(), transport(), current_app.config.get("DAY34_MCP_FACTORY"), _runs()

    def work(emit):
        for event in run_goal(goal, workspace=workspace, transport=transport_, factory=factory):
            if event["type"] == "run":
                store.add(event["run"])
                event = {"type": "run", "run": _brief(event["run"])}
            emit(event)

    return ndjson_stream(work)


@bp.get("/api/changes")
def changes():
    return jsonify({"files": _workspace().changes()})


@bp.post("/api/changes/apply")
def apply():
    data = request.get_json(silent=True)
    paths = data.get("paths") if isinstance(data, dict) else None
    if paths is not None and (not isinstance(paths, list) or not all(isinstance(path, str) for path in paths)):
        return jsonify({"error": "paths — список путей"}), 400
    try:
        applied = _workspace().apply(paths)
    except WorkspaceError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify({"applied": applied, "files": _workspace().changes()})


@bp.post("/api/changes/discard")
def discard():
    return jsonify({"discarded": _workspace().discard(), "files": []})


@bp.get("/api/runs")
def runs():
    return jsonify([_brief(item) for item in _runs().load()])


@bp.get("/api/runs/<run_id>")
def run_detail(run_id: str):
    found = next((item for item in _runs().load() if item.get("id") == run_id), None)
    if found is None:
        return jsonify({"error": "Такого прогона нет"}), 404
    return jsonify(found)

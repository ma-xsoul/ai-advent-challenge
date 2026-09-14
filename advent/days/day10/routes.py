"""
День 10: три стратегии контекста без summary и переключатель между ними.

Один диалог, одна активная ветка. Стратегия — часть конфига агента
активной ветки, переключается на живом диалоге. Ветки — отдельные агенты
в том же реестре под id <сессия>-<ветка>; дерево веток лежит рядом с
файлами агентов. Таблица прогонов копится в дереве: по строке на каждый
прогон сценария — так стратегии сравниваются на одной линейке.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, current_app, jsonify, make_response, render_template, request

from ...agent import (
    Agent,
    AgentRegistry,
    DialogTree,
    FactsPolicy,
    StoreError,
    TreeStore,
    get_branch_scenario,
    get_scenario,
)
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


bp = Blueprint("day10", __name__, url_prefix="/days/10")

STRATEGIES = ("full", "window", "facts")
STRATEGY_TITLES = {"full": "Полная история", "window": "Sliding Window", "facts": "Sticky Facts"}
N_MIN, N_MAX = 1, 50
DEFAULT_N = 4

SCENARIO_KEY = "spec"
BRANCH_SCENARIO_KEY = "spec-branches"


def tree_store() -> TreeStore:
    return TreeStore(current_app.config["AGENT_STORE_DIR"])


def agent_id(sid: str, branch: str) -> str:
    return f"{sid}-{branch}"


def active_agent(reg: AgentRegistry, sid: str, tree: DialogTree, *, create: bool) -> Agent:
    """Агент активной ветки: create=False — только посмотреть, не регистрируя."""
    aid = agent_id(sid, tree.active)
    return reg.get(aid) if create else reg.peek(aid)


def full_state(reg: AgentRegistry, sid: str, tree: DialogTree, agent: Agent) -> dict[str, Any]:
    """Всё, что нужно странице: дерево с размерами веток, слепок активной ветки, стратегия."""
    branches = []
    for branch in tree.branches:
        item = reg.peek(agent_id(sid, branch.id)) if branch.id != tree.active else agent
        branches.append({**asdict(branch), "messages": len(item.history), "strategy": item.config.strategy})
    return {
        "tree": {**tree.as_dict(), "branches": branches},
        "branch": snapshot_state(reg, agent_id(sid, tree.active), agent),
        "strategy": strategy_view(agent),
    }


def strategy_view(agent: Agent) -> dict[str, Any]:
    config = agent.config
    if config.window is not None:
        n = config.window
    elif config.facts is not None:
        n = config.facts.keep_last
    else:
        n = DEFAULT_N
    kind = config.strategy if config.strategy in STRATEGIES else "full"
    return {"kind": kind, "title": STRATEGY_TITLES[kind], "n": n}


def strategy_changes(kind: str, n: int) -> dict[str, Any]:
    """Что поменять в конфиге, чтобы включить стратегию. Конспект дня 9 здесь всегда выключен."""
    return {
        "compression": None,
        "window": n if kind == "window" else None,
        "facts": FactsPolicy(keep_last=n) if kind == "facts" else None,
    }


def run_row(tree: DialogTree, agent: Agent, scenario_key: str) -> dict[str, Any]:
    """Строка таблицы прогонов: качество (факты), стабильность (что потеряно), расход."""
    scenario = get_scenario(scenario_key) or get_scenario(SCENARIO_KEY)
    answer = next((m.content for m in reversed(agent.history) if m.role == "assistant"), "")
    checks = scenario.check(answer) if answer else []
    side_requests = len(agent.compressions) + len(agent.fact_updates)
    side_tokens = sum((r.input_tokens or 0) + (r.output_tokens or 0) for r in (*agent.compressions, *agent.fact_updates))
    view = strategy_view(agent)
    branch = tree.branch(tree.active)
    return {
        "id": len(tree.runs) + 1,
        "strategy": view["kind"],
        "title": view["title"],
        "n": view["n"] if view["kind"] != "full" else None,
        "scenario": scenario.key,
        "branch": branch.title if branch else tree.active,
        "turns": len(agent.turns),
        "requests": agent.usage.requests,
        "side_requests": side_requests,
        "input_tokens": agent.usage.input_tokens,
        "output_tokens": agent.usage.output_tokens,
        "total_tokens": agent.usage.total_tokens,
        "side_tokens": side_tokens,
        "facts_found": sum(check.found for check in checks),
        "facts_total": len(scenario.facts),
        "missing": [check.label for check in checks if not check.found],
        "answer": answer,
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _respond(sid: str, payload: Any, status: int = 200):
    response = make_response(jsonify(payload), status)
    remember_session(response, sid)
    return response


@bp.get("/")
def index():
    sid = session_id()
    reg = registry()
    tree = tree_store().load(sid)
    store_error = None
    state = None
    try:
        agent = active_agent(reg, sid, tree, create=False)
        state = full_state(reg, sid, tree, agent)
    except StoreError as error:
        store_error = str(error)
    response = make_response(
        render_template(
            "days/day10.html",
            active_day=get_day(10),
            model=current_app.config.get("DEEPSEEK_MODEL") or "deepseek-v4-flash",
            state=state,
            store_error=store_error,
            scenario=asdict(get_scenario(SCENARIO_KEY)),
            branch_scenario=asdict(get_branch_scenario(BRANCH_SCENARIO_KEY)),
            strategies=[{"kind": kind, "title": STRATEGY_TITLES[kind]} for kind in STRATEGIES],
            n_bounds=(N_MIN, N_MAX),
        ),
        500 if store_error else 200,
    )
    remember_session(response, sid)
    return response


@bp.get("/api/state")
def state():
    sid = session_id()
    reg = registry()
    tree = tree_store().load(sid)
    try:
        agent = active_agent(reg, sid, tree, create=False)
    except StoreError as error:
        return store_error_response(error)
    return _respond(sid, full_state(reg, sid, tree, agent))


@bp.get("/api/scenario")
def scenario():
    key = request.args.get("key", SCENARIO_KEY)
    item = get_scenario(key)
    if item is not None:
        return jsonify(asdict(item))
    branch_item = get_branch_scenario(key)
    if branch_item is not None:
        return jsonify(asdict(branch_item))
    return jsonify({"error": "Неизвестный сценарий"}), 404


@bp.post("/api/chat/stream")
def chat_stream():
    text, error_response = message_from_request()
    if error_response is not None:
        return error_response
    sid = session_id()
    reg = registry()
    tree = tree_store().load(sid)
    try:
        agent = active_agent(reg, sid, tree, create=True)
    except StoreError as error:
        return store_error_response(error)
    return stream_chat(reg, sid, agent, text, store_id=agent_id(sid, tree.active))


@bp.post("/api/strategy")
def set_strategy():
    """Переключатель стратегий: новый конфиг у агента активной ветки, стек прежний."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Ожидался JSON-объект со стратегией"}), 400
    kind = payload.get("kind")
    n = payload.get("n", DEFAULT_N)
    if kind not in STRATEGIES:
        return jsonify({"error": "Стратегия должна быть full, window или facts"}), 400
    if not _is_int(n) or not N_MIN <= n <= N_MAX:
        return jsonify({"error": f"N должно быть целым от {N_MIN} до {N_MAX}"}), 400

    sid = session_id()
    reg = registry()
    tree = tree_store().load(sid)
    try:
        active_agent(reg, sid, tree, create=True)
        agent = reg.reconfigure(agent_id(sid, tree.active), **strategy_changes(kind, n))
    except StoreError as error:
        return store_error_response(error)
    reg.save(agent_id(sid, tree.active))
    return _respond(sid, full_state(reg, sid, tree, agent))


@bp.post("/api/checkpoint")
def add_checkpoint():
    """Checkpoint — отметка «здесь можно ветвиться» на текущей длине стека активной ветки."""
    payload = request.get_json(silent=True) or {}
    sid = session_id()
    reg = registry()
    store = tree_store()
    tree = store.load(sid)
    try:
        agent = active_agent(reg, sid, tree, create=True)
        point = tree.add_checkpoint(tree.active, len(agent.history), _title(payload))
    except StoreError as error:
        return store_error_response(error)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    store.save(sid, tree)
    return _respond(sid, {"checkpoint": asdict(point), "state": full_state(reg, sid, tree, agent)})


@bp.post("/api/fork")
def fork():
    """
    Ветка от checkpoint: слепок родителя, обрезанный до этой точки,
    становится новым агентом. Дальше каждая ветка живёт сама, а страница
    переключается на новую.
    """
    payload = request.get_json(silent=True) or {}
    sid = session_id()
    reg = registry()
    store = tree_store()
    tree = store.load(sid)
    point = tree.checkpoint(str(payload.get("checkpoint") or ""))
    if point is None:
        return jsonify({"error": "Укажите существующий checkpoint"}), 400
    try:
        parent = reg.get(agent_id(sid, point.branch))
        saved = parent.fork(min(point.message, len(parent.history)))
        branch = tree.add_branch(point.id, _title(payload))
        agent = reg.spawn(agent_id(sid, branch.id), saved)
    except StoreError as error:
        return store_error_response(error)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    tree.switch(branch.id)
    store.save(sid, tree)
    return _respond(sid, {"branch": asdict(branch), "state": full_state(reg, sid, tree, agent)})


@bp.post("/api/switch")
def switch():
    payload = request.get_json(silent=True) or {}
    sid = session_id()
    reg = registry()
    store = tree_store()
    tree = store.load(sid)
    try:
        tree.switch(str(payload.get("branch") or ""))
        agent = active_agent(reg, sid, tree, create=True)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except StoreError as error:
        return store_error_response(error)
    store.save(sid, tree)
    return _respond(sid, full_state(reg, sid, tree, agent))


@bp.post("/api/runs")
def add_run():
    """Записать прогон: активная ветка прошла сценарий — фиксируем факты и расход."""
    payload = request.get_json(silent=True) or {}
    sid = session_id()
    reg = registry()
    store = tree_store()
    tree = store.load(sid)
    try:
        agent = active_agent(reg, sid, tree, create=True)
    except StoreError as error:
        return store_error_response(error)
    if not agent.turns:
        return jsonify({"error": "Нечего записывать: ходов ещё не было"}), 400
    row = run_row(tree, agent, str(payload.get("scenario") or SCENARIO_KEY))
    tree.runs.append(row)
    store.save(sid, tree)
    return _respond(sid, {"run": row, "state": full_state(reg, sid, tree, agent)})


@bp.delete("/api/runs")
def clear_runs():
    sid = session_id()
    reg = registry()
    store = tree_store()
    tree = store.load(sid)
    tree.runs.clear()
    store.save(sid, tree)
    try:
        agent = active_agent(reg, sid, tree, create=False)
    except StoreError as error:
        return store_error_response(error)
    return _respond(sid, full_state(reg, sid, tree, agent))


@bp.post("/api/reset")
def reset():
    """Новый диалог: ветки и checkpoint'ы стираются, стратегия и таблица прогонов остаются."""
    sid = session_id()
    reg = registry()
    store = tree_store()
    tree = store.load(sid)
    previous = None
    try:
        previous = active_agent(reg, sid, tree, create=False).config
    except StoreError:
        pass
    for branch in tree.branches:
        reg.forget(agent_id(sid, branch.id))
    fresh = DialogTree.fresh(runs=tree.runs)
    agent = reg.reconfigure(
        agent_id(sid, fresh.active),
        compression=None,
        window=previous.window if previous else None,
        facts=previous.facts if previous else None,
    )
    reg.save(agent_id(sid, fresh.active))
    store.save(sid, fresh)
    return _respond(sid, full_state(reg, sid, fresh, agent))


def _title(payload: dict[str, Any]) -> str | None:
    title = payload.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()[:80]
    return None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)

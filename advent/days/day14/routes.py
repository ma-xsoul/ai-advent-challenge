"""
День 14: инварианты — правила, которые держит код, а не модель.

Инварианты живут в <сессия>-invariants.json — отдельно от диалога,
памяти, профиля и задачи. Перед каждым ходом уходят в системный промпт
явной процедурой «сверь и откажи». После ответа два рубежа: регулярка по
уликам и ревизор — второй запрос к модели. Всё, что нашли, ложится в
журнал проверок, и по нему видно, где агент удержал правило сам, где
уступил и что из этого поймал код.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from flask import Blueprint, current_app, jsonify, make_response, render_template, request

from ...agent import (
    Agent,
    AgentRegistry,
    CheckRecord,
    DeepSeekTransport,
    Invariant,
    InvariantError,
    InvariantStore,
    StoreError,
    TransportError,
    all_invariants,
    get_scenario,
    inspect,
    estimate_tokens,
    render_invariants,
    review,
)
from ...agent.invariants import KINDS, MAX_CUSTOM, PRESET_IDS, verdict_of
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


bp = Blueprint("day14", __name__, url_prefix="/days/14")

SCENARIO_KEY = "invariants"


def invariant_store() -> InvariantStore:
    return InvariantStore(current_app.config["AGENT_STORE_DIR"])


def transport():
    return current_app.config.get("AGENT_TRANSPORT") or DeepSeekTransport()


def attach(agent: Agent, invariants: list[Invariant]) -> None:
    agent.invariants_text = render_invariants(invariants)


def full_state(reg: AgentRegistry, sid: str, agent: Agent, data: dict[str, Any]) -> dict[str, Any]:
    invariants = all_invariants(data)
    attach(agent, invariants)
    snapshot = snapshot_state(reg, sid, agent)
    checks = [{**c, "verdict": c.get("verdict") or verdict_of(c)} for c in data.get("checks", [])]
    snapshot["invariants"] = [item.as_dict() for item in invariants]
    snapshot["invariants_tokens"] = estimate_tokens(render_invariants(invariants))
    snapshot["checks"] = checks
    snapshot["system_text"] = agent.system_text
    snapshot["side"] = {
        "requests": sum(1 for c in checks if c.get("reviewer") is not None),
        "input_tokens": sum(c.get("input_tokens") or 0 for c in checks),
        "output_tokens": sum(c.get("output_tokens") or 0 for c in checks),
    }
    # Сводка по журналу: сколько раз модель удержал сам, сколько раз
    # уступил и что из этого поймал код.
    snapshot["summary"] = {
        "turns": len(checks),
        "refused": sum(1 for c in checks if c.get("refused")),
        "named": sum(1 for c in checks if c.get("named")),
        "alarms": sum(1 for c in checks if c.get("regex")),
        "false_alarms": sum(1 for c in checks if c.get("verdict") == "false_alarm"),
        "caught": sum(1 for c in checks if c.get("verdict") == "caught"),
    }
    return snapshot


def _page(sid: str, reg: AgentRegistry, agent: Agent | None, store_error: str | None):
    scenario = get_scenario(SCENARIO_KEY)
    return render_template(
        "days/day14.html",
        active_day=get_day(14),
        model=agent.config.model if agent else "—",
        state=full_state(reg, sid, agent, invariant_store().load(sid)) if agent else None,
        store_error=store_error,
        scenario=asdict(scenario) if scenario else None,
        kinds=KINDS,
    )


@bp.get("/")
def index():
    sid = session_id()
    reg = registry()
    try:
        agent = reg.peek(sid)
    except StoreError as error:
        return _page(sid, reg, None, str(error)), 500
    response = make_response(_page(sid, reg, agent, None))
    remember_session(response, sid)
    return response


@bp.get("/api/state")
def state():
    return _state_response(session_id())


@bp.get("/api/scenario")
def scenario():
    item = get_scenario(SCENARIO_KEY)
    if item is None:
        return jsonify({"error": "Сценарий не найден"}), 404
    return jsonify(asdict(item))


@bp.post("/api/invariant")
def add_invariant():
    """Свой инвариант: правило, вид и — если проверяемо — регулярка."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Ожидался JSON-объект"}), 400
    sid = session_id()
    store = invariant_store()
    data = store.load(sid)
    try:
        item = Invariant.from_dict(payload)
    except InvariantError as error:
        return jsonify({"error": str(error)}), 400
    if item.id in PRESET_IDS:
        return jsonify({"error": "Встроенный инвариант нельзя переписать — задайте другой id"}), 400
    custom = [c for c in data["custom"] if c.get("id") != item.id]
    if len(custom) >= MAX_CUSTOM:
        return jsonify({"error": f"Не больше {MAX_CUSTOM} своих инвариантов"}), 400
    custom.append(item.as_dict())
    data["custom"] = custom
    try:
        store.save(sid, data)
    except InvariantError as error:
        return jsonify({"error": str(error)}), 500
    return _state_response(sid)


@bp.delete("/api/invariant")
def delete_invariant():
    payload = request.get_json(silent=True)
    ident = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(ident, str) or not ident:
        return jsonify({"error": "Укажите id инварианта"}), 400
    if ident in PRESET_IDS:
        return jsonify({"error": "Встроенный инвариант удалить нельзя — его можно выключить"}), 400
    sid = session_id()
    store = invariant_store()
    data = store.load(sid)
    before = len(data["custom"])
    data["custom"] = [c for c in data["custom"] if c.get("id") != ident]
    if len(data["custom"]) == before:
        return jsonify({"error": "Такого инварианта нет"}), 404
    store.save(sid, data)
    return _state_response(sid)


@bp.post("/api/invariant/toggle")
def toggle_invariant():
    """Выключить правило — оно остаётся в списке, но не уходит в промпт и не проверяется."""
    payload = request.get_json(silent=True)
    ident = payload.get("id") if isinstance(payload, dict) else None
    enabled = payload.get("enabled") if isinstance(payload, dict) else None
    if not isinstance(ident, str) or not isinstance(enabled, bool):
        return jsonify({"error": "Ожидались id и enabled: true/false"}), 400
    sid = session_id()
    store = invariant_store()
    data = store.load(sid)
    if ident in PRESET_IDS:
        disabled = set(data["disabled"])
        (disabled.discard if enabled else disabled.add)(ident)
        data["disabled"] = sorted(disabled)
    else:
        found = False
        for item in data["custom"]:
            if item.get("id") == ident:
                item["enabled"] = enabled
                found = True
        if not found:
            return jsonify({"error": "Такого инварианта нет"}), 404
    store.save(sid, data)
    return _state_response(sid)


@bp.delete("/api/checks")
def clear_checks():
    sid = session_id()
    store = invariant_store()
    data = store.load(sid)
    data["checks"] = []
    store.save(sid, data)
    return _state_response(sid)


@bp.post("/api/reset")
def reset():
    """Новый диалог: стек пуст, инварианты и журнал остаются."""
    sid = session_id()
    reg = registry()
    agent = reg.reset(sid)
    response = jsonify(full_state(reg, sid, agent, invariant_store().load(sid)))
    remember_session(response, sid)
    return response


@bp.post("/api/chat/stream")
def chat_stream():
    text, error_response = message_from_request()
    if error_response is not None:
        return error_response

    sid = session_id()
    reg = registry()
    store = invariant_store()
    data = store.load(sid)
    invariants = all_invariants(data)
    try:
        agent = reg.get(sid)
    except StoreError as error:
        return store_error_response(error)
    attach(agent, invariants)

    def after_turn(current: Agent, question: str):
        yield from _check(sid, current, store, data, invariants, question)

    return stream_chat(
        reg,
        sid,
        agent,
        text,
        state_fn=lambda a: full_state(reg, sid, a, data),
        after_turn=after_turn,
    )


def _check(sid, agent, store, data, invariants, question):
    """Два рубежа после ответа: регулярка сразу, ревизор — отдельным запросом."""
    answer = agent.history[-1].content if agent.history else ""
    turn = len(agent.turns)
    seen = inspect(answer, invariants)
    yield {"type": "invariants_inspected", "inspection": seen}

    reviewer = None
    reviewer_refused = None
    reply = None
    error = None
    if any(item.enabled for item in invariants):
        yield {"type": "invariants_reviewing"}
        try:
            verdict, reply = review(transport(), agent.config.model, invariants, question, answer)
            reviewer = verdict["violations"]
            reviewer_refused = verdict["refused"]
        except TransportError as exc:
            # Ревизор не ответил — регулярка уже отработала, ход состоялся.
            error = str(exc)

    record = CheckRecord(
        after_turn=turn,
        question=question[:200],
        refused=seen["refused"],
        named=seen["named"],
        regex=seen["violations"],
        mentions=seen["mentions"],
        reviewer=reviewer,
        reviewer_refused=reviewer_refused,
        input_tokens=reply.input_tokens if reply else None,
        output_tokens=reply.output_tokens if reply else None,
        reviewer_error=error,
    )
    data["checks"] = (data.get("checks", []) + [record.as_dict()])[-40:]
    try:
        store.save(sid, data)
    except InvariantError as exc:
        yield {"type": "invariants_failed", "error": str(exc)}
        return
    yield {"type": "invariants_checked", "record": record.as_dict()}


def _state_response(sid: str):
    reg = registry()
    try:
        agent = reg.peek(sid)
    except StoreError as error:
        return store_error_response(error)
    response = jsonify(full_state(reg, sid, agent, invariant_store().load(sid)))
    remember_session(response, sid)
    return response

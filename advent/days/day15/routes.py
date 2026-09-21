"""
День 15: контролируемые переходы — автомат, который нельзя перепрыгнуть.

Тот же автомат, что в дне 13, но в строгом режиме: у переходов есть
таблица и guard'ы, и через одни ворота — TaskState.request() — проходят
все: кнопка пользователя, наблюдатель после хода и сама модель строкой
«[переход: …]». Всё, чего в таблице нет, получает отказ с причиной и
запись в журнал. Задача и диалог дня 15 живут отдельно от дня 13:
<сессия>-task15.json и агент <сессия>-d15.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict
from typing import Any

from flask import Blueprint, current_app, jsonify, make_response, render_template, request

from ...agent import (
    STAGES,
    TRANSITIONS,
    Agent,
    AgentRegistry,
    DeepSeekTransport,
    StoreError,
    TaskError,
    TaskState,
    TaskStore,
    TransportError,
    get_task_scenario,
    new_task,
    observe,
    requested_transition,
    retelling,
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


bp = Blueprint("day15", __name__, url_prefix="/days/15")

SCENARIO_KEY = "task-guarded"
STORE_NAME = "task15"

# Отказал ли агент в прыжке — по словам в начале ответа.
_JUMP_REFUSAL = re.compile(
    r"не могу|нельзя|не буду|не стану|перепрыг|сначала|пока не|без утвержд|без провер|этап", re.IGNORECASE
)


def task_store() -> TaskStore:
    return TaskStore(current_app.config["AGENT_STORE_DIR"], STORE_NAME)


def transport():
    return current_app.config.get("AGENT_TRANSPORT") or DeepSeekTransport()


def agent_id(sid: str) -> str:
    return f"{sid}-d15"


def attach(agent: Agent, task: TaskState | None) -> None:
    agent.task_text = task.render() if task else None


def full_state(reg: AgentRegistry, sid: str, agent: Agent, task: TaskState | None) -> dict[str, Any]:
    attach(agent, task)
    snapshot = snapshot_state(reg, agent_id(sid), agent)
    last = next((m.content for m in reversed(agent.history) if m.role == "assistant"), "")
    snapshot["task"] = task.as_dict() if task else None
    snapshot["task_file"] = task_store().describe(sid)
    snapshot["system_text"] = agent.system_text
    snapshot["retelling"] = retelling(last, task) if task and last else None
    records = task.observer if task else []
    snapshot["side"] = {
        "requests": len(records),
        "input_tokens": sum(r.input_tokens or 0 for r in records),
        "output_tokens": sum(r.output_tokens or 0 for r in records),
    }
    snapshot["stages"] = list(STAGES)
    snapshot["edges"] = [edge.as_dict() for edge in TRANSITIONS]
    log = task.log if task else []
    snapshot["summary"] = {
        "transitions": sum(1 for t in log if not t.refused and t.from_stage != t.to_stage),
        "refused": sum(1 for t in log if t.refused),
        "refused_user": sum(1 for t in log if t.refused and t.actor == "user"),
        "refused_model": sum(1 for t in log if t.refused and t.actor == "model"),
        "refused_observer": sum(1 for t in log if t.refused and t.actor == "observer"),
    }
    return snapshot


def _page(sid: str, reg: AgentRegistry, agent: Agent | None, store_error: str | None):
    scenario = get_task_scenario(SCENARIO_KEY)
    return render_template(
        "days/day15.html",
        active_day=get_day(15),
        model=agent.config.model if agent else "—",
        state=full_state(reg, sid, agent, task_store().load(sid)) if agent else None,
        store_error=store_error,
        scenario=asdict(scenario) if scenario else None,
        stages=STAGES,
        edges=[edge.as_dict() for edge in TRANSITIONS],
    )


@bp.get("/")
def index():
    sid = session_id()
    reg = registry()
    try:
        agent = reg.peek(agent_id(sid))
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
    item = get_task_scenario(SCENARIO_KEY)
    if item is None:
        return jsonify({"error": "Сценарий не найден"}), 404
    return jsonify(asdict(item))


# ── Одни ворота для всех переходов ──────────────────────────────────────


def _with_task(sid: str, action):
    store = task_store()
    task = store.load(sid)
    if task is None:
        return jsonify({"error": "Задачи ещё нет — напишите агенту, и она появится"}), 404
    reg = registry()
    try:
        agent = reg.peek(agent_id(sid))
    except StoreError as error:
        return store_error_response(error)
    try:
        result = action(task, len(agent.turns))
    except TaskError as error:
        return jsonify({"error": str(error), "task": task.as_dict()}), 409
    store.save(sid, task)
    payload = full_state(reg, sid, agent, task)
    if result is not None:
        payload["transition"] = result.as_dict()
    response = jsonify(payload)
    remember_session(response, sid)
    # Отказ — это не ошибка сервера, а результат: 409 с полным состоянием,
    # чтобы страница показала, почему нельзя.
    return (response, 409) if result is not None and result.refused else response


@bp.post("/api/task/transition")
def transition():
    """
    Запросить любой переход. Легальный — сделается, нелегальный — отказ
    с причиной и запись в журнале. Это и есть «попытка перейти в
    недопустимое состояние» из задания.
    """
    payload = request.get_json(silent=True)
    target = payload.get("to") if isinstance(payload, dict) else None
    if target not in STAGES:
        return jsonify({"error": f"Укажите этап: {', '.join(STAGES)}"}), 400
    return _with_task(session_id(), lambda task, turn: task.request(target, actor="user", turn=turn))


@bp.post("/api/task/pause")
def pause():
    return _with_task(session_id(), lambda task, turn: task.pause(turn=turn))


@bp.post("/api/task/resume")
def resume():
    return _with_task(session_id(), lambda task, turn: task.resume(turn=turn))


@bp.post("/api/task/new")
def drop_task():
    sid = session_id()
    task_store().delete(sid)
    return _state_response(sid)


@bp.post("/api/reset")
def reset():
    """Новый диалог: стек пуст, задача остаётся."""
    sid = session_id()
    reg = registry()
    agent = reg.reset(agent_id(sid))
    response = jsonify(full_state(reg, sid, agent, task_store().load(sid)))
    remember_session(response, sid)
    return response


@bp.post("/api/chat/stream")
def chat_stream():
    text, error_response = message_from_request()
    if error_response is not None:
        return error_response

    sid = session_id()
    reg = registry()
    store = task_store()
    try:
        agent = reg.get(agent_id(sid))
    except StoreError as error:
        return store_error_response(error)

    task = store.load(sid)
    born = task is None
    if born:
        task = new_task(text, uuid.uuid4().hex[:8], guarded=True)
        store.save(sid, task)
    attach(agent, task)

    def after_turn(current: Agent, question: str):
        yield from _after(sid, current, store, task, question, born)

    return stream_chat(
        reg,
        sid,
        agent,
        text,
        store_id=agent_id(sid),
        state_fn=lambda a: full_state(reg, sid, a, task),
        after_turn=after_turn,
    )


def _after(sid, agent, store, task, question, born):
    """
    После ответа: (1) не попросила ли модель переход строкой [переход: …] —
    прогоняем через ворота; (2) наблюдатель докладывает; его выводы тоже
    идут через ворота, где guard может отказать (финал — только пользователь).
    """
    if born:
        yield {"type": "task_created", "task": task.as_dict()}
    answer = agent.history[-1].content if agent.history else ""
    turn = len(agent.turns)

    wanted = requested_transition(answer)
    refused_jump = bool(_JUMP_REFUSAL.search(answer[:300]))
    if wanted and not task.paused:
        record = task.request(wanted, actor="model", turn=turn)
        store.save(sid, task)
        yield {"type": "task_transition", "record": record.as_dict(), "task": task.as_dict()}
    yield {"type": "task_jump_check", "refused": refused_jump, "wanted": wanted}

    if task.stage == "done" or task.paused:
        return

    yield {"type": "task_observing", "stage": task.stage}
    try:
        report, reply = observe(transport(), agent.config.model, task, question, answer)
        record = _apply_guarded(task, report, turn, reply)
        store.save(sid, task)
    except (TransportError, TaskError) as error:
        yield {"type": "task_failed", "error": str(error)}
        return
    yield {"type": "task_updated", "record": record.as_dict(), "task": task.as_dict()}
    for item in task.log[-3:]:
        if item.refused and item.actor == "observer" and item.turn == turn:
            yield {"type": "task_transition", "record": item.as_dict(), "task": task.as_dict()}


def _apply_guarded(task: TaskState, report: dict[str, Any], turn: int, reply):
    """
    Доклад наблюдателя в строгом режиме: на validation его «ok» — только
    просьба закрыть, и ворота её отклонят (финал подтверждает пользователь).
    Остальное — как в дне 13.
    """
    if task.stage == "validation" and report.get("verdict") == "ok":
        task.request("done", actor="observer", turn=turn)
        from ...agent.task import ObserverRecord

        record = ObserverRecord(turn, task.stage, report, "рекомендует принять — ждёт пользователя", reply.input_tokens, reply.output_tokens)
        task.observer = (task.observer + [record])[-60:]
        return record
    return task.apply_report(report, turn=turn, reply=reply)


def _state_response(sid: str):
    reg = registry()
    try:
        agent = reg.peek(agent_id(sid))
    except StoreError as error:
        return store_error_response(error)
    response = jsonify(full_state(reg, sid, agent, task_store().load(sid)))
    remember_session(response, sid)
    return response

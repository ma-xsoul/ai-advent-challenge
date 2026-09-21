"""
День 13: состояние задачи как конечный автомат.

Задача рождается с первой репликой и живёт в <сессия>-task.json — это
task scope из дня 11, только теперь там не факты, а объект с этапом,
шагом и ожиданием. Перед каждым ходом она уходит агенту блоком в
системный промпт; после ответа наблюдатель докладывает, что произошло, и
код двигает автомат. «Новый диалог» стирает стек, но не задачу — в этом
и проверка: продолжит агент с шага или объяснит всё заново.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any

from flask import Blueprint, current_app, jsonify, make_response, render_template, request

from ...agent import (
    STAGES,
    Agent,
    AgentRegistry,
    DeepSeekTransport,
    StoreError,
    TaskError,
    TaskState,
    TaskStore,
    TransportError,
    observe,
    get_task_scenario,
    new_task,
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


bp = Blueprint("day13", __name__, url_prefix="/days/13")

SCENARIO_KEY = "task-tictactoe"

# Реплика, которую отправляет кнопка «Продолжить»: агент должен ответить
# следующим шагом, а не вопросом «о чём была задача».
RESUME_MESSAGE = "Продолжаем."


def task_store() -> TaskStore:
    return TaskStore(current_app.config["AGENT_STORE_DIR"])


def transport():
    return current_app.config.get("AGENT_TRANSPORT") or DeepSeekTransport()


def attach(agent: Agent, task: TaskState | None) -> None:
    agent.task_text = task.render() if task else None


def full_state(reg: AgentRegistry, sid: str, agent: Agent, task: TaskState | None) -> dict[str, Any]:
    attach(agent, task)
    snapshot = snapshot_state(reg, sid, agent)
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
    return snapshot


def _page(sid: str, reg: AgentRegistry, agent: Agent | None, store_error: str | None):
    scenario = get_task_scenario(SCENARIO_KEY)
    return render_template(
        "days/day13.html",
        active_day=get_day(13),
        model=agent.config.model if agent else "—",
        state=full_state(reg, sid, agent, task_store().load(sid)) if agent else None,
        store_error=store_error,
        scenario=asdict(scenario) if scenario else None,
        stages=STAGES,
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
    item = get_task_scenario(SCENARIO_KEY)
    if item is None:
        return jsonify({"error": "Сценарий не найден"}), 404
    return jsonify(asdict(item))


# ── Переходы по кнопкам: код двигает автомат ────────────────────────────


def _transition(sid: str, action):
    """Общая обвязка: загрузить задачу, применить переход, сохранить, отдать состояние."""
    store = task_store()
    task = store.load(sid)
    if task is None:
        return jsonify({"error": "Задачи ещё нет — напишите агенту, и она появится"}), 404
    reg = registry()
    try:
        agent = reg.peek(sid)
    except StoreError as error:
        return store_error_response(error)
    try:
        action(task, len(agent.turns))
    except TaskError as error:
        return jsonify({"error": str(error), "task": task.as_dict()}), 409
    store.save(sid, task)
    response = jsonify(full_state(reg, sid, agent, task))
    remember_session(response, sid)
    return response


@bp.post("/api/task/approve")
def approve():
    return _transition(session_id(), lambda task, turn: task.approve_plan(turn=turn))


@bp.post("/api/task/step")
def step_done():
    """Ручное «шаг сделан» — когда наблюдатель недосмотрел."""
    return _transition(session_id(), lambda task, turn: task.complete_step(turn=turn, reason="шаг закрыт вручную"))


@bp.post("/api/task/verdict")
def verdict():
    payload = request.get_json(silent=True)
    ok = payload.get("ok") if isinstance(payload, dict) else None
    if not isinstance(ok, bool):
        return jsonify({"error": "Ожидался ok: true или false"}), 400
    step = payload.get("step") if isinstance(payload, dict) else None
    return _transition(
        session_id(),
        lambda task, turn: task.validate(ok, turn=turn, step=step if isinstance(step, int) else None, issues=["вернул пользователь"] if not ok else None),
    )


@bp.post("/api/task/pause")
def pause():
    return _transition(session_id(), lambda task, turn: task.pause(turn=turn))


@bp.post("/api/task/resume")
def resume():
    return _transition(session_id(), lambda task, turn: task.resume(turn=turn))


@bp.post("/api/task/new")
def drop_task():
    """Снять задачу: следующая реплика родит новую. Диалог не трогаем."""
    sid = session_id()
    task_store().delete(sid)
    return _state_response(sid)


@bp.post("/api/reset")
def reset():
    """Новый диалог: стек пуст, задача остаётся. Это и есть проверка продолжения."""
    sid = session_id()
    reg = registry()
    agent = reg.reset(sid)
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
        agent = reg.get(sid)
    except StoreError as error:
        return store_error_response(error)

    # Задача рождается с первой репликой — куратор: «считаю задачей с самого
    # старта, когда юзер отправил промпт».
    task = store.load(sid)
    born = task is None
    if born:
        task = new_task(text, uuid.uuid4().hex[:8])
        store.save(sid, task)
    attach(agent, task)

    def after_turn(current: Agent, question: str):
        yield from _observe(sid, current, store, task, question, born)

    return stream_chat(
        reg,
        sid,
        agent,
        text,
        state_fn=lambda a: full_state(reg, sid, a, task),
        after_turn=after_turn,
    )


def _observe(sid, agent, store, task, question, born):
    """Наблюдатель после ответа: доклад → код двигает автомат → событие в ленту."""
    if born:
        yield {"type": "task_created", "task": task.as_dict()}
    if task.stage == "done" or task.paused:
        return

    answer = agent.history[-1].content if agent.history else ""
    turn = len(agent.turns)
    yield {"type": "task_observing", "stage": task.stage}
    try:
        report, reply = observe(transport(), agent.config.model, task, question, answer)
        record = task.apply_report(report, turn=turn, reply=reply)
        store.save(sid, task)
    except (TransportError, TaskError) as error:
        # Ход состоялся, автомат остался где был — следующая реплика попробует снова.
        yield {"type": "task_failed", "error": str(error)}
        return
    yield {"type": "task_updated", "record": record.as_dict(), "task": task.as_dict()}


def _state_response(sid: str):
    reg = registry()
    try:
        agent = reg.peek(sid)
    except StoreError as error:
        return store_error_response(error)
    response = jsonify(full_state(reg, sid, agent, task_store().load(sid)))
    remember_session(response, sid)
    return response

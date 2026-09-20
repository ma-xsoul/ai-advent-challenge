"""
День 11: модель памяти агента — три слоя вместо одного стека.

Слои живут снаружи агента, в своих файлах, как дерево веток в дне 10.
Перед каждым ходом включённые слои подмешиваются в системный промпт, а
после ответа нарезатель (или эвристика, или руки) раскладывает свежую
реплику по слоям. Разные сроки жизни видны прямо в маршрутах: «Новый
диалог» сносит краткосрочный слой, «Новая задача» — рабочий,
долговременный не трогает ни то, ни другое.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from flask import Blueprint, current_app, jsonify, make_response, render_template, request

from ...agent import (
    SCOPES,
    Agent,
    DeepSeekTransport,
    LayeredMemory,
    MemoryStore,
    StoreError,
    TransportError,
    get_memory_scenario,
    route_by_agent,
    route_by_heuristic,
)
from ...agent.memory import MemoryError_, ROUTER_MODES
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


bp = Blueprint("day11", __name__, url_prefix="/days/11")

SCENARIO_KEY = "layers"

# Сколько последних раскладок держим в истории: страница показывает их
# лентой, а бесконечный список в файле никому не нужен.
MAX_UPDATES = 40


def memory_store() -> MemoryStore:
    return MemoryStore(current_app.config["AGENT_STORE_DIR"])


def transport():
    """Тот же транспорт, что у агента: в тестах его подменяет конфиг."""
    return current_app.config.get("AGENT_TRANSPORT") or DeepSeekTransport()


def settings_of(store: MemoryStore, sid: str) -> dict[str, Any]:
    data = store.load_settings(sid)
    mode = data.get("mode")
    enabled = data.get("enabled")
    return {
        "mode": mode if mode in ROUTER_MODES else "agent",
        "enabled": [scope for scope in SCOPES if not isinstance(enabled, list) or scope in enabled],
        "updates": data.get("updates") if isinstance(data.get("updates"), list) else [],
    }


def full_state(reg, sid: str, agent: Agent, store: MemoryStore, memory: LayeredMemory) -> dict[str, Any]:
    settings = settings_of(store, sid)
    snapshot = snapshot_state(reg, sid, agent)
    updates = settings["updates"]
    snapshot["memory"] = memory.state(settings["enabled"])
    snapshot["mode"] = settings["mode"]
    snapshot["enabled"] = settings["enabled"]
    snapshot["updates"] = updates
    snapshot["files"] = store.files(sid)
    # Побочные запросы памяти — отдельной строкой, как в дне 10: помнить
    # стоит токенов, и эта цена должна быть на виду.
    snapshot["side"] = {
        "requests": sum(1 for item in updates if item.get("mode") == "agent"),
        "input_tokens": sum(item.get("input_tokens") or 0 for item in updates),
        "output_tokens": sum(item.get("output_tokens") or 0 for item in updates),
    }
    return snapshot


def load(sid: str):
    store = memory_store()
    memory = store.load(sid)
    return store, memory


def _remember(response, sid: str):
    remember_session(response, sid)
    return response


@bp.get("/")
def index():
    sid = session_id()
    store, memory = load(sid)
    try:
        agent = registry().peek(sid)
    except StoreError as error:
        return (
            render_template(
                "days/day11.html",
                active_day=get_day(11),
                model="—",
                state=None,
                store_error=str(error),
                scenario=None,
                scopes=SCOPES,
                modes=ROUTER_MODES,
            ),
            500,
        )

    scenario = get_memory_scenario(SCENARIO_KEY)
    response = make_response(
        render_template(
            "days/day11.html",
            active_day=get_day(11),
            model=agent.config.model,
            state=full_state(registry(), sid, agent, store, memory),
            store_error=None,
            scenario=asdict(scenario),
            scopes=SCOPES,
            modes=ROUTER_MODES,
        )
    )
    return _remember(response, sid)


@bp.get("/api/state")
def state():
    sid = session_id()
    store, memory = load(sid)
    try:
        agent = registry().peek(sid)
    except StoreError as error:
        return store_error_response(error)
    return _remember(jsonify(full_state(registry(), sid, agent, store, memory)), sid)


@bp.get("/api/scenario")
def scenario():
    item = get_memory_scenario(SCENARIO_KEY)
    if item is None:
        return jsonify({"error": "Сценарий не найден"}), 404
    return jsonify(asdict(item))


@bp.post("/api/mode")
def set_mode():
    """Кто раскладывает: нарезатель, эвристика или руки."""
    payload = request.get_json(silent=True)
    mode = payload.get("mode") if isinstance(payload, dict) else None
    if mode not in ROUTER_MODES:
        return jsonify({"error": f"Режим раскладки: {', '.join(ROUTER_MODES)}"}), 400

    sid = session_id()
    store, memory = load(sid)
    settings = settings_of(store, sid)
    settings["mode"] = mode
    store.save_settings(sid, settings)
    try:
        agent = registry().peek(sid)
    except StoreError as error:
        return store_error_response(error)
    return _remember(jsonify(full_state(registry(), sid, agent, store, memory)), sid)


@bp.post("/api/layers")
def set_layers():
    """
    Какие слои подмешивать в промпт. Выключение слоя его не стирает —
    это ручка «а что агент ответит без рабочей памяти».
    """
    payload = request.get_json(silent=True)
    enabled = payload.get("enabled") if isinstance(payload, dict) else None
    if not isinstance(enabled, list) or any(scope not in SCOPES for scope in enabled):
        return jsonify({"error": "Ожидался список слоёв из chat, task, user"}), 400

    sid = session_id()
    store, memory = load(sid)
    settings = settings_of(store, sid)
    settings["enabled"] = [scope for scope in SCOPES if scope in enabled]
    store.save_settings(sid, settings)
    try:
        agent = registry().peek(sid)
    except StoreError as error:
        return store_error_response(error)
    return _remember(jsonify(full_state(registry(), sid, agent, store, memory)), sid)


@bp.post("/api/memory")
def put_memory():
    """Положить запись руками — третий режим раскладки из задания."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Ожидался JSON-объект"}), 400
    scope = payload.get("scope")
    key = payload.get("key")
    value = payload.get("value")
    if scope not in SCOPES:
        return jsonify({"error": "Укажите слой: chat, task или user"}), 400
    if not isinstance(key, str) or not key.strip():
        return jsonify({"error": "Укажите ключ"}), 400
    if not isinstance(value, str) or not value.strip():
        return jsonify({"error": "Укажите значение"}), 400

    sid = session_id()
    store, memory = load(sid)
    memory[scope].put(key, value, source="manual")
    store.save_scope(sid, memory, scope)
    try:
        agent = registry().peek(sid)
    except StoreError as error:
        return store_error_response(error)
    return _remember(jsonify(full_state(registry(), sid, agent, store, memory)), sid)


@bp.delete("/api/memory")
def forget_memory():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Ожидался JSON-объект"}), 400
    scope = payload.get("scope")
    key = payload.get("key")
    if scope not in SCOPES or not isinstance(key, str) or not key.strip():
        return jsonify({"error": "Укажите слой и ключ"}), 400

    sid = session_id()
    store, memory = load(sid)
    memory[scope].forget(key)
    store.save_scope(sid, memory, scope)
    try:
        agent = registry().peek(sid)
    except StoreError as error:
        return store_error_response(error)
    return _remember(jsonify(full_state(registry(), sid, agent, store, memory)), sid)


@bp.post("/api/memory/move")
def move_memory():
    """Переложить запись в другой слой: нарезатель ошибся — поправили руками."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Ожидался JSON-объект"}), 400
    source = payload.get("from")
    target = payload.get("to")
    key = payload.get("key")
    if source not in SCOPES or target not in SCOPES or source == target:
        return jsonify({"error": "Укажите разные слои from и to"}), 400
    if not isinstance(key, str) or not key.strip():
        return jsonify({"error": "Укажите ключ"}), 400

    sid = session_id()
    store, memory = load(sid)
    item = memory[source].items.get(key.strip())
    if item is None:
        return jsonify({"error": "Такой записи нет"}), 404
    memory[source].forget(key)
    memory[target].put(item.key, item.value, source="manual", turn=item.turn)
    store.save_scope(sid, memory, source)
    store.save_scope(sid, memory, target)
    try:
        agent = registry().peek(sid)
    except StoreError as error:
        return store_error_response(error)
    return _remember(jsonify(full_state(registry(), sid, agent, store, memory)), sid)


@bp.post("/api/memory/clear")
def clear_memory():
    payload = request.get_json(silent=True)
    scope = payload.get("scope") if isinstance(payload, dict) else None
    if scope not in SCOPES:
        return jsonify({"error": "Укажите слой: chat, task или user"}), 400

    sid = session_id()
    store = memory_store()
    try:
        store.clear(sid, scope)
    except MemoryError_ as error:
        return jsonify({"error": str(error)}), 500
    memory = store.load(sid)
    try:
        agent = registry().peek(sid)
    except StoreError as error:
        return store_error_response(error)
    return _remember(jsonify(full_state(registry(), sid, agent, store, memory)), sid)


@bp.post("/api/task/new")
def new_task():
    """Новая задача: рабочий слой умирает, диалог и пользователь остаются."""
    sid = session_id()
    store = memory_store()
    try:
        store.clear(sid, "task")
    except MemoryError_ as error:
        return jsonify({"error": str(error)}), 500
    memory = store.load(sid)
    try:
        agent = registry().peek(sid)
    except StoreError as error:
        return store_error_response(error)
    return _remember(jsonify(full_state(registry(), sid, agent, store, memory)), sid)


@bp.post("/api/reset")
def reset():
    """
    Новый диалог: стек и краткосрочный слой умирают, рабочий и
    долговременный — нет. В этом и вся разница между слоями.
    """
    sid = session_id()
    store = memory_store()
    try:
        store.clear(sid, "chat")
    except MemoryError_ as error:
        return jsonify({"error": str(error)}), 500
    agent = registry().reset(sid)
    memory = store.load(sid)
    return _remember(jsonify(full_state(registry(), sid, agent, store, memory)), sid)


@bp.post("/api/chat/stream")
def chat_stream():
    text, error_response = message_from_request()
    if error_response is not None:
        return error_response

    sid = session_id()
    reg = registry()
    store, memory = load(sid)
    settings = settings_of(store, sid)
    try:
        agent = reg.get(sid)
    except StoreError as error:
        return store_error_response(error)

    # Слои уходят в системный промпт перед ходом — агент должен знать,
    # что памяти у него три и какая из них сколько живёт.
    agent.memory_text = memory.render(settings["enabled"])

    def after_turn(current: Agent, question: str):
        yield from _route(reg, sid, current, store, memory, settings, question)

    return stream_chat(
        reg,
        sid,
        agent,
        text,
        state_fn=lambda a: full_state(reg, sid, a, store, memory),
        after_turn=after_turn,
    )


def _route(reg, sid, agent, store, memory, settings, question):
    """Раскладка свежей реплики по слоям — после ответа, отдельным шагом."""
    mode = settings["mode"]
    if mode == "manual":
        return

    answer = agent.history[-1].content if agent.history else ""
    turn = len(agent.turns)
    yield {"type": "memory_routing", "mode": mode}

    try:
        if mode == "agent":
            routed, reply = route_by_agent(transport(), agent.config.model, memory, question, answer)
            update = memory.apply(routed, mode=mode, turn=turn, reply=reply)
        else:
            update = memory.apply(route_by_heuristic(question, turn), mode=mode, turn=turn)
    except TransportError as error:
        # Ход состоялся, память осталась прежней — следующая реплика
        # попробует снова. Терять ответ из-за нарезателя нельзя.
        yield {"type": "memory_failed", "error": str(error)}
        return

    try:
        store.save(sid, memory)
        settings["updates"] = (settings["updates"] + [update.as_dict()])[-MAX_UPDATES:]
        store.save_settings(sid, settings)
    except MemoryError_ as error:
        yield {"type": "memory_failed", "error": str(error)}
        return

    yield {
        "type": "memory_routed",
        "update": update.as_dict(),
        "memory": memory.state(settings["enabled"]),
    }

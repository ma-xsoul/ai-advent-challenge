"""
День 12: персонализация — профиль пользователя поверх модели памяти.

Две дорожки, как в дне 9, только различаются не стратегией, а профилем:
<сессия>-a и <сессия>-b — два агента с одним стеком вопросов и разными
профилями. Профиль — отдельный файл, отдельно от памяти и от диалога:
смена профиля не трогает ни стек, ни слои. Слои памяти дня 11 той же
сессии подмешиваются обеим дорожкам — профиль лежит поверх них.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from flask import Blueprint, current_app, jsonify, make_response, render_template, request

from ...agent import (
    Agent,
    AgentRegistry,
    MemoryStore,
    Profile,
    ProfileError,
    ProfileStore,
    StoreError,
    all_profiles,
    check_answer,
    find_profile,
    get_scenario,
)
from ...agent.profile import MAX_CUSTOM, PRESET_IDS
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


bp = Blueprint("day12", __name__, url_prefix="/days/12")

LANES = ("a", "b")
# Что стоит на дорожках, пока пользователь не выбрал: два самых разных
# встроенных профиля — чтобы разница была видна с первого ответа.
DEFAULT_LANES = {"a": "maksim", "b": "customer"}

SCENARIO_KEY = "profiles"


def profile_store() -> ProfileStore:
    return ProfileStore(current_app.config["AGENT_STORE_DIR"])


def memory_store() -> MemoryStore:
    return MemoryStore(current_app.config["AGENT_STORE_DIR"])


def lane_id(sid: str, lane: str) -> str:
    return f"{sid}-{lane}"


def lane_profiles(data: dict[str, Any]) -> dict[str, Profile]:
    """Профиль каждой дорожки; если привязка битая — встроенный по умолчанию."""
    out = {}
    for lane in LANES:
        wanted = data.get("lanes", {}).get(lane) or DEFAULT_LANES[lane]
        out[lane] = find_profile(data, wanted) or find_profile(data, DEFAULT_LANES[lane])
    return out


def lane_agents(reg: AgentRegistry, sid: str, *, create: bool) -> dict[str, Agent]:
    return {lane: (reg.get if create else reg.peek)(lane_id(sid, lane)) for lane in LANES}


def attach(agents: dict[str, Agent], profiles: dict[str, Profile], memory_text: str | None) -> None:
    """
    Подложить каждой дорожке её профиль и общую память. Профиль — сразу за
    базовым промптом, память — после: сначала «как работать», потом
    «что знаем».
    """
    for lane, agent in agents.items():
        agent.profile_text = profiles[lane].render()
        agent.memory_text = memory_text


def full_state(reg: AgentRegistry, sid: str, agents: dict[str, Agent]) -> dict[str, Any]:
    pstore = profile_store()
    data = pstore.load(sid)
    profiles = lane_profiles(data)
    memory = memory_store().load(sid)
    memory_text = memory.render()
    attach(agents, profiles, memory_text)

    lanes = {}
    for lane in LANES:
        snapshot = snapshot_state(reg, lane_id(sid, lane), agents[lane])
        last = next((m.content for m in reversed(agents[lane].history) if m.role == "assistant"), "")
        snapshot["profile"] = profiles[lane].id
        snapshot["check"] = check_answer(last, profiles[lane]) if last else None
        snapshot["system_text"] = agents[lane].system_text
        lanes[lane] = snapshot

    layers = memory.state()["layers"]
    return {
        "lanes": lanes,
        "profiles": [profile.as_dict() for profile in all_profiles(data)],
        "memory": {
            "tokens": memory.tokens(),
            "counts": {layer["scope"]: len(layer["items"]) for layer in layers},
        },
    }


def _page(sid: str, reg: AgentRegistry, agents: dict[str, Agent] | None, store_error: str | None):
    scenario = get_scenario(SCENARIO_KEY)
    return render_template(
        "days/day12.html",
        active_day=get_day(12),
        model=agents["a"].config.model if agents else "—",
        state=full_state(reg, sid, agents) if agents else None,
        store_error=store_error,
        scenario=asdict(scenario) if scenario else None,
        lanes=LANES,
    )


@bp.get("/")
def index():
    sid = session_id()
    reg = registry()
    try:
        agents = lane_agents(reg, sid, create=False)
    except StoreError as error:
        return _page(sid, reg, None, str(error)), 500
    response = make_response(_page(sid, reg, agents, None))
    remember_session(response, sid)
    return response


@bp.get("/api/state")
def state():
    sid = session_id()
    reg = registry()
    try:
        agents = lane_agents(reg, sid, create=False)
    except StoreError as error:
        return store_error_response(error)
    response = jsonify(full_state(reg, sid, agents))
    remember_session(response, sid)
    return response


@bp.get("/api/scenario")
def scenario():
    item = get_scenario(SCENARIO_KEY)
    if item is None:
        return jsonify({"error": "Сценарий не найден"}), 404
    return jsonify(asdict(item))


@bp.post("/api/lane")
def set_lane():
    """Привязать профиль к дорожке. Диалог дорожки остаётся — меняется только конфиг."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Ожидался JSON-объект"}), 400
    lane = payload.get("lane")
    profile_id = payload.get("profile")
    if lane not in LANES:
        return jsonify({"error": "Укажите дорожку: a или b"}), 400

    sid = session_id()
    pstore = profile_store()
    data = pstore.load(sid)
    if not isinstance(profile_id, str) or find_profile(data, profile_id) is None:
        return jsonify({"error": "Такого профиля нет"}), 404
    data.setdefault("lanes", {})[lane] = profile_id
    try:
        pstore.save(sid, data)
    except ProfileError as error:
        return jsonify({"error": str(error)}), 500
    return _state_response(sid)


@bp.post("/api/profile")
def save_profile():
    """
    Свой профиль: создать или поправить. Так «условный пользователь
    сервиса настраивает агента под себя» — руками, без правки кода.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Ожидался JSON-объект"}), 400

    sid = session_id()
    pstore = profile_store()
    data = pstore.load(sid)
    try:
        profile = Profile.from_dict(payload)
    except ProfileError as error:
        return jsonify({"error": str(error)}), 400
    if profile.id in PRESET_IDS:
        return jsonify({"error": "Встроенный профиль нельзя переписать — задайте другой id"}), 400

    custom = [item for item in data.get("custom", []) if item.get("id") != profile.id]
    if len(custom) >= MAX_CUSTOM:
        return jsonify({"error": f"Не больше {MAX_CUSTOM} своих профилей"}), 400
    custom.append(profile.as_dict())
    data["custom"] = custom
    try:
        pstore.save(sid, data)
    except ProfileError as error:
        return jsonify({"error": str(error)}), 500
    return _state_response(sid)


@bp.delete("/api/profile")
def delete_profile():
    payload = request.get_json(silent=True)
    profile_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(profile_id, str) or not profile_id:
        return jsonify({"error": "Укажите id профиля"}), 400
    if profile_id in PRESET_IDS:
        return jsonify({"error": "Встроенный профиль удалить нельзя"}), 400

    sid = session_id()
    pstore = profile_store()
    data = pstore.load(sid)
    before = len(data.get("custom", []))
    data["custom"] = [item for item in data.get("custom", []) if item.get("id") != profile_id]
    if len(data["custom"]) == before:
        return jsonify({"error": "Такого профиля нет"}), 404
    # Дорожка, стоявшая на удалённом профиле, откатывается к встроенному.
    for lane, bound in list(data.get("lanes", {}).items()):
        if bound == profile_id:
            data["lanes"][lane] = DEFAULT_LANES.get(lane, DEFAULT_LANES["a"])
    try:
        pstore.save(sid, data)
    except ProfileError as error:
        return jsonify({"error": str(error)}), 500
    return _state_response(sid)


@bp.post("/api/reset")
def reset():
    """Новый диалог на обеих дорожках. Профили и память остаются."""
    sid = session_id()
    reg = registry()
    for lane in LANES:
        reg.reset(lane_id(sid, lane))
    return _state_response(sid)


@bp.post("/api/chat/stream")
def chat_stream():
    text, error_response = message_from_request()
    if error_response is not None:
        return error_response
    lane = (request.get_json(silent=True) or {}).get("lane")
    if lane not in LANES:
        return jsonify({"error": "Укажите дорожку: a или b"}), 400

    sid = session_id()
    reg = registry()
    try:
        agents = lane_agents(reg, sid, create=True)
    except StoreError as error:
        return store_error_response(error)

    # Профиль и память подкладываются перед каждым ходом — «подключите
    # профиль к каждому запросу» буквально.
    data = profile_store().load(sid)
    attach(agents, lane_profiles(data), memory_store().load(sid).render())
    return stream_chat(
        reg,
        sid,
        agents[lane],
        text,
        store_id=lane_id(sid, lane),
        state_fn=lambda a: full_state(reg, sid, agents)["lanes"][lane],
    )


def _state_response(sid: str):
    reg = registry()
    try:
        agents = lane_agents(reg, sid, create=False)
    except StoreError as error:
        return store_error_response(error)
    response = jsonify(full_state(reg, sid, agents))
    remember_session(response, sid)
    return response

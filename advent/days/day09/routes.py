"""
День 9: два агента на одном сценарии — без сжатия и со сжатием.

Обе «дорожки» живут в том же реестре, что и дни 7–8, под своими
идентификаторами: <сессия>-plain и <сессия>-summary. Так на одной странице
сравниваются два агента с разными конфигами — то самое «заспавнить с
разными конфигами» из дня 6, только теперь ради сравнения.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from flask import Blueprint, jsonify, make_response, render_template, request

from ...agent import Agent, AgentRegistry, CompressionPolicy, StoreError, TransportError, get_scenario
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


bp = Blueprint("day09", __name__, url_prefix="/days/9")

# Дорожки сравнения: левая шлёт модели весь стек, правая — конспект плюс хвост.
LANES = ("plain", "summary")
LANE_TITLES = {"plain": "Без сжатия", "summary": "Со сжатием"}

# Политика по умолчанию — из задания: последние N «как есть», остальное
# в конспект каждые 10 сообщений. N = 4 — две последние пары реплик.
DEFAULT_POLICY = CompressionPolicy(keep_last=4, every=10)

KEEP_MIN, KEEP_MAX = 0, 50
EVERY_MIN, EVERY_MAX = 2, 100
# Лимит контекста — как в дне 8, но теперь на обе дорожки сразу.
LIMIT_MIN, LIMIT_MAX = 100, 128_000

# Два прогона одного диалога: с короткими ответами и с развёрнутыми.
# Факты в обоих одинаковые, а вот экономика сжатия — разная.
SCENARIO_KEY = "spec"
SCENARIO_KEYS = ("spec", "spec-verbose")


def lane_id(sid: str, lane: str) -> str:
    return f"{sid}-{lane}"


def lane_agents(reg: AgentRegistry, sid: str, *, create: bool) -> dict[str, Agent]:
    """
    Оба агента сессии. create=False — только посмотреть (GET), не плодя
    записей в реестре. Дорожка со сжатием получает политику при первом
    обращении; дальше политика живёт в её конфиге и в файле на диске.
    """
    agents: dict[str, Agent] = {}
    for lane in LANES:
        lid = lane_id(sid, lane)
        agent = reg.get(lid) if create else reg.peek(lid)
        if lane == "summary" and agent.config.compression is None:
            if create:
                agent = reg.reconfigure(lid, compression=DEFAULT_POLICY)
            else:
                agent = agent.with_config(compression=DEFAULT_POLICY)
        agents[lane] = agent
    return agents


def lanes_state(reg: AgentRegistry, sid: str, agents: dict[str, Agent]) -> dict[str, Any]:
    """Всё, что нужно странице: слепки обеих дорожек, политика и сравнение."""
    policy = agents["summary"].config.compression or DEFAULT_POLICY
    return {
        "lanes": {lane: snapshot_state(reg, lane_id(sid, lane), agent) for lane, agent in agents.items()},
        "policy": {
            "keep_last": policy.keep_last,
            "every": policy.every,
            "summary_max_tokens": policy.summary_max_tokens,
            "limit": agents["summary"].config.context_limit,
        },
        "compare": compare(agents),
    }


def compare(agents: dict[str, Agent]) -> dict[str, Any]:
    """
    Сравнение из задания: качество ответов и расход токенов до/после.

    Качество — сколько фактов сценария уцелело в последнем ответе каждой
    дорожки. Расход — итог за диалог, включая запросы на конспект.
    """
    scenario = get_scenario(SCENARIO_KEY)
    lanes: dict[str, dict[str, Any]] = {}
    checks: dict[str, dict[str, bool]] = {}
    for lane, agent in agents.items():
        answer = next((m.content for m in reversed(agent.history) if m.role == "assistant"), "")
        found = scenario.check(answer) if answer else []
        checks[lane] = {check.label: check.found for check in found}
        summary_in = sum(r.input_tokens or 0 for r in agent.compressions)
        summary_out = sum(r.output_tokens or 0 for r in agent.compressions)
        lanes[lane] = {
            "title": LANE_TITLES[lane],
            "turns": len(agent.turns),
            "requests": agent.usage.requests,
            "summary_requests": len(agent.compressions),
            "input_tokens": agent.usage.input_tokens,
            "output_tokens": agent.usage.output_tokens,
            "total_tokens": agent.usage.total_tokens,
            "summary_tokens": summary_in + summary_out,
            "checked": bool(answer),
            "facts_found": sum(check.found for check in found),
            "facts_total": len(scenario.facts),
            "missing": [check.label for check in found if not check.found],
        }

    plain_total = lanes["plain"]["total_tokens"]
    saved = plain_total - lanes["summary"]["total_tokens"]
    return {
        "facts": [
            {"label": fact.label, **{lane: checks[lane].get(fact.label, False) for lane in LANES}}
            for fact in scenario.facts
        ],
        "lanes": lanes,
        # Положительное — сжатие сэкономило, отрицательное — обошлось дороже.
        "saved_tokens": saved,
        "saved_percent": round(100 * saved / plain_total) if plain_total else None,
        "same_turns": lanes["plain"]["turns"] == lanes["summary"]["turns"],
    }


def _page(sid: str, reg: AgentRegistry, agents: dict[str, Agent] | None, store_error: str | None):
    scenario = get_scenario(SCENARIO_KEY)
    state = lanes_state(reg, sid, agents) if agents else None
    response = make_response(
        render_template(
            "days/day09.html",
            active_day=get_day(9),
            model=agents["plain"].config.model if agents else "—",
            state=state,
            store_error=store_error,
            scenario=asdict(scenario),
            scenarios=[asdict(get_scenario(key)) for key in SCENARIO_KEYS],
            lane_titles=LANE_TITLES,
            bounds={
                "keep": (KEEP_MIN, KEEP_MAX),
                "every": (EVERY_MIN, EVERY_MAX),
                "limit": (LIMIT_MIN, LIMIT_MAX),
            },
        ),
        500 if store_error else 200,
    )
    remember_session(response, sid)
    return response


@bp.get("/")
def index():
    sid = session_id()
    reg = registry()
    try:
        agents = lane_agents(reg, sid, create=False)
    except StoreError as error:
        return _page(sid, reg, None, str(error))
    return _page(sid, reg, agents, None)


@bp.get("/api/state")
def state():
    sid = session_id()
    reg = registry()
    try:
        agents = lane_agents(reg, sid, create=False)
    except StoreError as error:
        return store_error_response(error)
    response = jsonify(lanes_state(reg, sid, agents))
    remember_session(response, sid)
    return response


@bp.get("/api/scenario")
def scenario():
    key = request.args.get("key", SCENARIO_KEY)
    if key not in SCENARIO_KEYS:
        return jsonify({"error": "Неизвестный сценарий"}), 404
    return jsonify(asdict(get_scenario(key)))


@bp.post("/api/reset")
def reset():
    """Новый диалог на обеих дорожках. Политика и лимит остаются — сбрасывается история."""
    sid = session_id()
    reg = registry()
    try:
        old = lane_agents(reg, sid, create=True)
    except StoreError:
        old = {}
    agents: dict[str, Agent] = {}
    for lane in LANES:
        lid = lane_id(sid, lane)
        previous = old.get(lane)
        reg.reset(lid)
        agents[lane] = reg.reconfigure(
            lid,
            compression=previous.config.compression if previous else (DEFAULT_POLICY if lane == "summary" else None),
            context_limit=previous.config.context_limit if previous else None,
        )
        reg.save(lid)
    response = jsonify(lanes_state(reg, sid, agents))
    remember_session(response, sid)
    return response


@bp.post("/api/policy")
def set_policy():
    """
    Сменить N, порог и лимит, не теряя диалог. Политика — только у дорожки
    со сжатием, лимит — у обеих: так на одном сценарии видно, что левая
    упирается в стену, а правая сжимается и идёт дальше.
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Ожидался JSON-объект с политикой"}), 400

    keep_last = payload.get("keep_last")
    every = payload.get("every")
    limit = payload.get("limit")
    if not _is_int(keep_last) or not KEEP_MIN <= keep_last <= KEEP_MAX:
        return jsonify({"error": f"N должно быть целым от {KEEP_MIN} до {KEEP_MAX}"}), 400
    if not _is_int(every) or not EVERY_MIN <= every <= EVERY_MAX:
        return jsonify({"error": f"Порог должен быть целым от {EVERY_MIN} до {EVERY_MAX}"}), 400
    if keep_last >= every:
        return jsonify({"error": "N должно быть меньше порога — иначе сжимать нечего"}), 400
    if limit is not None and (not _is_int(limit) or not LIMIT_MIN <= limit <= LIMIT_MAX):
        return jsonify({"error": f"Лимит должен быть от {LIMIT_MIN} до {LIMIT_MAX} токенов или пустым"}), 400

    sid = session_id()
    reg = registry()
    try:
        lane_agents(reg, sid, create=True)
        # Потолок конспекта на странице не настраивается — берётся из кода.
        policy = CompressionPolicy(keep_last=keep_last, every=every)
        agents = {
            "plain": reg.reconfigure(lane_id(sid, "plain"), context_limit=limit),
            "summary": reg.reconfigure(lane_id(sid, "summary"), context_limit=limit, compression=policy),
        }
    except StoreError as error:
        return store_error_response(error)
    for lane in LANES:
        reg.save(lane_id(sid, lane))
    response = jsonify(lanes_state(reg, sid, agents))
    remember_session(response, sid)
    return response


@bp.post("/api/compress")
def compress_now():
    """Кнопка «сжать сейчас» — второй триггер из подсказки куратора, рядом со счётчиком."""
    sid = session_id()
    reg = registry()
    try:
        agents = lane_agents(reg, sid, create=True)
    except StoreError as error:
        return store_error_response(error)
    agent = agents["summary"]
    try:
        record = agent.compress("manual")
    except TransportError as error:
        return jsonify({"error": f"Конспект не получился: {error}"}), 502
    reg.save(lane_id(sid, "summary"))
    response = jsonify(
        {
            "compression": asdict(record) if record else None,
            "state": lanes_state(reg, sid, agents),
        }
    )
    remember_session(response, sid)
    return response


@bp.post("/api/chat/stream")
def chat_stream():
    text, error_response = message_from_request()
    if error_response is not None:
        return error_response
    lane = (request.get_json(silent=True) or {}).get("lane")
    if lane not in LANES:
        return jsonify({"error": "Укажите дорожку: plain или summary"}), 400

    sid = session_id()
    reg = registry()
    try:
        agents = lane_agents(reg, sid, create=True)
    except StoreError as error:
        return store_error_response(error)
    return stream_chat(reg, sid, agents[lane], text, store_id=lane_id(sid, lane))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)

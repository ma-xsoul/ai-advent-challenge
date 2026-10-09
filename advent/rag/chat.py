"""
Мини-чат с RAG и памятью задачи — день 25.

Один ход — четыре шага:

1. параллельно:
   - запрос — сообщение вслед («а сколько он живёт?») переписывается в
     самостоятельный поисковый запрос: по голому «он» поиск ничего не найдёт;
   - память задачи — JSON: цель диалога, что пользователь уже уточнил,
     ограничения, термины. Цель меняется, только если модель явно сказала,
     что пользователь сменил задачу; иначе код оставляет прежнюю дословно;
2. поиск — воронка дня 23 по запросу: порог похожести и реранкер;
3. ответ — как в дне 24: JSON, сначала цитаты, потом ответ по ним; в
   промпте — память задачи и последние реплики. Пусто после воронки —
   «не знаю» и просьба уточнить, модель не отвечает;
4. проверка хода: есть ли источники, нашлись ли цитаты, держится ли цель.

Диалог — <сессия>-d25.json рядом с диалогами других дней: переживает
перезагрузку, а «сбросить всё» сносит его вместе с остальными.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..agent.transport import Transport, TransportError
from .embeddings import Embedder
from .grounded import ANSWER_PROMPT, QUOTE_OK, UNKNOWN, _ask_json, _json, _public, clarify, sources_of, verify_quote
from .index import Hit, RagIndex
from .rerank import Settings, funnel


HISTORY_MESSAGES = 6
MAX_LIST = 6

CONDENSE_PROMPT = (
    "Ты готовишь поисковый запрос к базе кода и документации проекта ai-advent-challenge (Flask, LLM-агент на "
    "DeepSeek: память, задачи, инварианты, MCP-серверы, расписание, RAG). Перепиши последнее сообщение "
    "пользователя в самостоятельный запрос: подставь из истории и памяти задачи, о чём речь вместо «он», «это», "
    "«там», добавь термины и вероятные имена из кода. Если сообщение — не вопрос (уточнение, ограничение, "
    "просьба напомнить), сделай запрос о текущей цели диалога. На вопрос не отвечай. Верни одну строку до 25 "
    "слов без пояснений и кавычек."
)

MEMORY_PROMPT = (
    "Ты ведёшь память задачи диалога о проекте ai-advent-challenge. Тебе дают текущую память, недавние реплики "
    "и новое сообщение пользователя. Верни обновлённую память — только JSON:\n"
    '{"goal": "...", "goal_changed": false, "clarified": ["..."], "constraints": ["..."], "terms": ["..."]}\n'
    "Правила:\n"
    "- goal — цель всего диалога одной фразой: зачем пользователь пришёл. Если цели ещё нет — сформулируй по "
    "первому сообщению. goal_changed=true только если пользователь явно сменил задачу; уточнение, новый вопрос "
    "по той же теме или просьба ответить иначе цель не меняют;\n"
    f"- clarified — факты о задаче, которые сообщил сам пользователь: детали, выбор, контекст — до {MAX_LIST} "
    "коротких пунктов. Хорошо: «чат на 100+ сообщений», «берёт сервер курса доллара за образец». Плохо: "
    "«спрашивает, где описаны серверы», «интересуется стратегиями» — это его вопросы, их не записывай, а такие "
    "пункты из текущей памяти убери;\n"
    "- constraints — ограничения и требования пользователя к ответам и решению;\n"
    "- terms — договорённости о терминах в виде «X — это Y»;\n"
    "- ничего не выдумывай: только то, что сказал пользователь. Старые пункты сохраняй, если они не устарели."
)

CHAT_RULES = (
    "Это диалог: учитывай память задачи и предыдущие реплики, держи цель диалога. Соблюдай ограничения "
    "пользователя и его термины. Отвечай на последнее сообщение пользователя. Если оно не вопрос, а уточнение, "
    "ограничение или договорённость о терминах — коротко подтверди его одной фразой и продолжи по цели диалога: "
    "следующий полезный шаг по фрагментам, с цитатами. Цель, уточнения, ограничения и термины из памяти задачи "
    "называй без цитат — это слова пользователя, а не факты о проекте. status \"unknown\" — только если на вопрос "
    "во фрагментах ответа нет."
)


# ── память задачи ───────────────────────────────────────────────────────


@dataclass
class TaskMemory:
    goal: str = ""
    goal_since: int = 0
    clarified: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)
    goal_changes: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskMemory":
        return cls(
            goal=str(data.get("goal") or ""),
            goal_since=int(data.get("goal_since") or 0),
            clarified=[str(item) for item in data.get("clarified") or []],
            constraints=[str(item) for item in data.get("constraints") or []],
            terms=[str(item) for item in data.get("terms") or []],
            goal_changes=list(data.get("goal_changes") or []),
        )

    def render(self) -> str:
        if not self.goal:
            return "Память задачи пуста — это начало диалога."
        lines = [f"Цель диалога: {self.goal}"]
        for title, items in (("Уже уточнено", self.clarified), ("Ограничения", self.constraints), ("Термины", self.terms)):
            if items:
                lines.append(f"{title}: " + "; ".join(items))
        return "\n".join(lines)


def update_memory(memory: TaskMemory, history: list[dict[str, str]], message: str, turn: int, transport: Transport) -> dict[str, Any]:
    """Новая память задачи. Модель ответила криво — память остаётся прежней, ход не ломается."""
    recent = "\n".join(f"{item['role']}: {item['content'][:400]}" for item in history[-HISTORY_MESSAGES:]) or "—"
    user = f"Текущая память:\n{json.dumps(_memory_view(memory), ensure_ascii=False)}\n\nНедавние реплики:\n{recent}\n\nНовое сообщение пользователя:\n{message}"
    try:
        text, cost = _ask_json(transport, MEMORY_PROMPT, user, max_tokens=600)
    except TransportError as error:
        return {"memory": memory, "changed": False, "error": str(error), "tokens": 0}
    data = _json(text)
    if data is None:
        return {"memory": memory, "changed": False, "error": "память: модель ответила не JSON", "tokens": cost["tokens"]}

    proposed = " ".join(str(data.get("goal") or "").split())
    changed = bool(data.get("goal_changed")) and bool(memory.goal) and bool(proposed) and proposed != memory.goal
    fresh = TaskMemory(
        goal=memory.goal or proposed,
        goal_since=memory.goal_since or (turn if proposed else 0),
        clarified=_items(data.get("clarified"), memory.clarified),
        constraints=_items(data.get("constraints"), memory.constraints),
        terms=_items(data.get("terms"), memory.terms),
        goal_changes=list(memory.goal_changes),
    )
    if changed:
        # Цель сменилась — это видно в памяти: с какого хода и какой была.
        fresh.goal_changes.append({"turn": turn, "from": memory.goal, "to": proposed})
        fresh.goal, fresh.goal_since = proposed, turn
    return {"memory": fresh, "changed": changed, "error": None, "tokens": cost["tokens"]}


def _items(raw: Any, previous: list[str]) -> list[str]:
    if not isinstance(raw, list):
        return previous
    items = [" ".join(str(item).split()) for item in raw if str(item).strip()]
    return list(dict.fromkeys(items))[:MAX_LIST]


def _memory_view(memory: TaskMemory) -> dict[str, Any]:
    return {"goal": memory.goal, "clarified": memory.clarified, "constraints": memory.constraints, "terms": memory.terms}


def condense(message: str, history: list[dict[str, str]], memory: TaskMemory, transport: Transport) -> dict[str, Any]:
    recent = "\n".join(f"{item['role']}: {item['content'][:300]}" for item in history[-HISTORY_MESSAGES:]) or "—"
    user = f"{memory.render()}\n\nНедавние реплики:\n{recent}\n\nПоследнее сообщение пользователя:\n{message}"
    try:
        text, cost = _ask_json(transport, CONDENSE_PROMPT, user, max_tokens=120, json_mode=False)
    except TransportError:
        return {"query": message, "tokens": 0}
    query = " ".join(text.strip().strip('"«»').split())
    return {"query": query or message, "tokens": cost["tokens"]}


# ── диалог ───────────────────────────────────────────────────────────────


@dataclass
class RagChat:
    messages: list[dict[str, Any]] = field(default_factory=list)
    memory: TaskMemory = field(default_factory=TaskMemory)
    scenario: str | None = None

    @property
    def turns(self) -> int:
        return sum(message["role"] == "user" for message in self.messages)

    def history(self) -> list[dict[str, str]]:
        """Только текст реплик — без цитат и источников: модели нужен разговор, а не служебное."""
        return [{"role": message["role"], "content": message["content"]} for message in self.messages]

    def as_dict(self) -> dict[str, Any]:
        return {"messages": self.messages, "memory": self.memory.as_dict(), "scenario": self.scenario}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RagChat":
        return cls(
            messages=list(data.get("messages") or []),
            memory=TaskMemory.from_dict(data.get("memory") or {}),
            scenario=data.get("scenario"),
        )


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ChatStore:
    """<сессия>-d25.json в общей папке диалогов."""

    def __init__(self, directory: str | Path, name: str = "d25") -> None:
        self._directory = Path(directory)
        self._name = name

    def load(self, session_id: str) -> RagChat:
        path = self._path(session_id)
        if not path.exists():
            return RagChat()
        try:
            return RagChat.from_dict(json.loads(path.read_text(encoding="utf-8"))["chat"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return RagChat()

    def save(self, session_id: str, chat: RagChat) -> None:
        path = self._path(session_id)
        self._directory.mkdir(parents=True, exist_ok=True)
        payload = {"session_id": session_id, "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "chat": chat.as_dict()}
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)

    def delete(self, session_id: str) -> bool:
        try:
            self._path(session_id).unlink()
            return True
        except FileNotFoundError:
            return False

    def _path(self, session_id: str) -> Path:
        if not _SAFE_ID.match(session_id):
            raise ValueError("Недопустимый идентификатор сессии")
        return self._directory / f"{session_id}-{self._name}.json"


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def session_lock(session_id: str) -> threading.Lock:
    """Один ход за раз на сессию: два параллельных хода затёрли бы друг другу диалог."""
    with _locks_guard:
        return _locks.setdefault(session_id, threading.Lock())


# ── один ход ─────────────────────────────────────────────────────────────


def turn(
    chat: RagChat,
    message: str,
    *,
    index: RagIndex,
    embedder: Embedder,
    transport: Transport,
    settings: Settings,
    emit,
    goal_keywords: tuple[str, ...] = (),
) -> dict[str, Any]:
    """
    События: memory → query → retrieved → answer → checks. Диалог и память
    меняются на месте; сохраняет вызывающий.
    """
    started = time.perf_counter()
    number = chat.turns + 1
    history = chat.history()
    emit({"type": "user", "turn": number, "content": message})

    with ThreadPoolExecutor(max_workers=2) as pool:
        remembered = pool.submit(update_memory, chat.memory, history, message, number, transport)
        condensed = pool.submit(condense, message, history, chat.memory, transport)
        memory_step, query_step = remembered.result(), condensed.result()
    chat.memory = memory_step["memory"]
    emit({"type": "memory", "turn": number, "changed": memory_step["changed"], "error": memory_step["error"], **chat.memory.as_dict()})
    emit({"type": "query", "turn": number, "query": query_step["query"]})

    stage = funnel(query_step["query"], "filter", index=index, embedder=embedder, transport=transport, settings=settings)
    context: list[Hit] = stage["context"]
    scores = [row["rerank"] for row in stage["candidates"] if row["rerank"] is not None]
    relevance = max(scores) if scores else None
    emit({"type": "retrieved", "turn": number, "relevance": relevance, "context": len(context), "passed": sum(row["stage"] != "threshold" for row in stage["candidates"])})

    tokens = memory_step["tokens"] + query_step["tokens"] + ((stage["cost"]["rerank"] or {}).get("tokens", 0))
    if not context:
        asked = clarify(message, transport)
        tokens += asked["tokens"]
        reply = {"status": "unknown", "answer": UNKNOWN, "clarify": asked["text"], "quotes": [], "sources": [], "parsed": True}
    else:
        reply, cost = _answer(message, chat, history, context, transport)
        tokens += cost

    reply = _public(reply)
    checks = _checks(reply, chat.memory, goal_keywords)
    entry = {
        "role": "assistant",
        "content": reply["answer"],
        "turn": number,
        "status": reply["status"],
        "clarify": reply["clarify"],
        "query": query_step["query"],
        "relevance": relevance,
        "sources": reply["sources"],
        "quotes": [{key: quote[key] for key in ("n", "text", "status", "source", "section", "chunk_id")} for quote in reply["quotes"]],
        "checks": checks,
        "tokens": tokens,
        "ms": round((time.perf_counter() - started) * 1000),
    }
    chat.messages.append({"role": "user", "content": message, "turn": number})
    chat.messages.append(entry)
    emit({"type": "answer", **entry})
    return entry


def _answer(message: str, chat: RagChat, history: list[dict[str, str]], context: list[Hit], transport: Transport) -> tuple[dict[str, Any], int]:
    blocks = [f"[{hit.rank}] {hit.source} · {hit.section} · строки {hit.start_line}–{hit.end_line}\n{hit.text}" for hit in context]
    recent = "\n".join(f"{'Пользователь' if item['role'] == 'user' else 'Ассистент'}: {item['content'][:500]}" for item in history[-HISTORY_MESSAGES:])
    user = (
        f"{chat.memory.render()}\n\n"
        + (f"Недавние реплики:\n{recent}\n\n" if recent else "")
        + "Фрагменты:\n\n" + "\n\n---\n\n".join(blocks)
        + f"\n\nПоследнее сообщение пользователя: {message}"
    )
    text, cost = _ask_json(transport, f"{ANSWER_PROMPT}\n{CHAT_RULES}", user, max_tokens=2000)
    data = _json(text)
    if data is None:
        reply = {"status": "answer", "answer": text.strip(), "clarify": "", "quotes": [], "sources": [], "parsed": False}
        return reply, cost["tokens"]
    status = "unknown" if str(data.get("status", "")).strip().lower() == "unknown" else "answer"
    raw = [
        {"n": int(item["n"]), "text": str(item.get("text") or "").strip()}
        for item in data.get("quotes") or []
        if isinstance(item, dict) and str(item.get("n", "")).strip().isdigit() and str(item.get("text") or "").strip()
    ]
    quotes = [verify_quote(quote, context) for quote in raw]
    answer = str(data.get("answer") or "").strip() or ("Не знаю" if status == "unknown" else "")
    reply = {
        "status": status,
        "answer": answer,
        "clarify": str(data.get("clarify") or "").strip(),
        "quotes": quotes,
        "sources": sources_of(answer, quotes, context),
        "parsed": True,
    }
    return reply, cost["tokens"]


def _checks(reply: dict[str, Any], memory: TaskMemory, goal_keywords: tuple[str, ...]) -> dict[str, Any]:
    """Источники, цитаты и цель — по каждому ходу. Цель с ключевыми словами сценария сверяется ещё и по ним."""
    quotes = reply["quotes"]
    verified = sum(quote["status"] in QUOTE_OK for quote in quotes)
    goal = memory.goal.casefold()
    goal_kept = not memory.goal_changes and bool(goal) and (not goal_keywords or any(word in goal for word in goal_keywords))
    return {
        "unknown": reply["status"] == "unknown",
        "has_sources": bool(reply["sources"]),
        "quotes_verified": verified,
        "quotes_total": len(quotes),
        "goal_kept": goal_kept,
    }


# ── длинные сценарии ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    goal: str
    goal_keywords: tuple[str, ...]
    messages: tuple[str, ...]
    # Последнее сообщение просит напомнить цель: в ответе должна быть хотя бы
    # одна формулировка из каждой группы — и цель, и ограничение.
    recap: tuple[tuple[str, ...], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "title": self.title, "goal": self.goal, "messages": list(self.messages)}


SCENARIOS = (
    Scenario(
        key="mcp_tool",
        title="Свой MCP-инструмент",
        goal="добавить в проект свой MCP-инструмент",
        goal_keywords=("mcp", "инструмент", "тул"),
        messages=(
            "Хочу добавить в проект свой MCP-инструмент. С чего начать — как агент вообще вызывает инструменты?",
            "А где описаны мои серверы и как агент их запускает?",
            "Ограничение: мой сервер должен работать по stdio, без HTTP.",
            "Как устроен сервер курса доллара? Хочу взять его за образец.",
            "Как там сделаны ошибки, чтобы модель видела их текст?",
            "Договоримся: «тул» — это инструмент MCP.",
            "Сколько раз за один ход агент может вызывать тулы?",
            "А если серверов несколько — как агент поймёт, куда слать вызов моего тула?",
            "Что будет, если мой сервер не запустится — ход упадёт?",
            "Отвечай короче, в 2–3 предложения.",
            "Как сохранить результат тула в файл — как это сделано в save_report?",
            "Напомни: какая у нас цель и что мы уже выяснили?",
        ),
        recap=(("mcp", "инструмент", "тул"), ("stdio",)),
    ),
    Scenario(
        key="long_memory",
        title="Память в длинном разговоре",
        goal="выбрать стратегию памяти для чата на 100+ сообщений",
        goal_keywords=("памят", "контекст", "истори", "разговор", "диалог", "сообщени"),
        messages=(
            "Мне нужно выбрать, как агент будет помнить длинный разговор — чат на 100+ сообщений. Что происходит, когда история становится слишком длинной?",
            "А сколько последних сообщений остаётся как есть?",
            "Какие ещё стратегии контекста есть, кроме сжатия?",
            "Чем Sticky Facts отличается от конспекта?",
            "Ограничение: токены дорогие, нужно минимум лишних запросов к модели.",
            "Как заранее оценить, сколько токенов займёт история?",
            "Какие слои памяти есть и что из них живёт дольше одного разговора?",
            "Под «долгой памятью» я имею в виду слой user.",
            "Что станет с памятью задачи, если начать новый диалог?",
            "А где хранятся эти слои на диске?",
            "С учётом бюджета — какую стратегию ты бы выбрал для 100+ сообщений?",
            "Напомни цель и ограничения, которые мы зафиксировали.",
        ),
        recap=(("100",), ("токен", "запрос")),
    ),
)


def get_scenario(key: str) -> Scenario | None:
    return next((scenario for scenario in SCENARIOS if scenario.key == key), None)


def scenario_summary(chat: RagChat, scenario: Scenario) -> dict[str, Any]:
    """Итог длинного сценария: держались ли источники и цель до последнего хода."""
    answers = [message for message in chat.messages if message["role"] == "assistant"]
    checks = [answer["checks"] for answer in answers]
    final = answers[-1]["content"].casefold() if answers else ""
    return {
        "turns": len(answers),
        "with_sources": sum(item["has_sources"] for item in checks),
        "unknown": sum(item["unknown"] for item in checks),
        "quotes_verified": sum(item["quotes_verified"] for item in checks),
        "quotes_total": sum(item["quotes_total"] for item in checks),
        "goal_kept": sum(item["goal_kept"] for item in checks),
        "goal": chat.memory.goal,
        "goal_changes": len(chat.memory.goal_changes),
        "recap_ok": bool(scenario.recap) and all(any(word in final for word in group) for group in scenario.recap),
        "tokens": sum(answer["tokens"] for answer in answers),
    }

"""
AI-конспект и тренажёр курса — день 35.

Задача: вернуться к любому дню курса — вспомнить, что было, спросить и
потренироваться, — не перечитывая сотни сообщений чата, 35 веток кода и
наши разговоры. AI здесь делает работу, а не только отвечает:

1. конспект — по источникам каждого дня (advent/course/sources.py) модель
   пишет главу: суть в 3–5 предложениях, главные идеи, что построено, что
   уточнял куратор, и три вопроса для самопроверки с эталонным ответом.
   Глава пересчитывается, только если её источники изменились;
2. вопрос — RAG по всем источникам (bge-m3, свой индекс data/day35):
   ответ со ссылками [n] на страницу дня, README, чат или наш разговор;
   можно спросить по всему курсу или по одному дню;
3. тренажёр — вопрос главы, твой ответ своими словами, модель сверяет с
   эталоном и источниками: верно / частично / неверно, чего не хватило.
   Счёт по дням копится в data/day35/progress.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from ..agent.transport import Transport, TransportError
from ..mcp.client import PROJECT_ROOT
from ..rag.corpus import Document
from ..rag.embeddings import Embedder
from ..rag.index import RagIndex
from .sources import DAYS, KINDS, Source


MODEL = "deepseek-v4-flash"
STRATEGY = "structure"
DATA = PROJECT_ROOT / "data" / "day35"
DAY_CHARS = 14_000
TOP_K = 6
CONCURRENCY = 6
CITATION = re.compile(r"\[(\d{1,2})\]")

Emit = Callable[[dict[str, Any]], None]

CHAPTER_PROMPT = """Ты составляешь конспект одного дня курса AI Advent Challenge для его участника. Участник — разработчик; весь курс он делал домашние задания в одном Flask-проекте.
Тебе дают источники дня: страницу дня из проекта (задание, цитаты куратора, объяснение практики), абзац README, пост задания и реплики куратора в чате, вопросы участника и ответы на них.

Ответ — только JSON:
{"title": "короткое название дня",
 "summary": "3–5 предложений: что было в задании, что сделали и чему это учит",
 "ideas": ["3–5 главных идей дня — простыми словами, по одной фразе"],
 "built": ["что построено в проекте: модули, страницы, результат проверки — 2–4 пункта"],
 "curator": ["что уточнял куратор — 0–3 пункта, только если это есть в источниках"],
 "questions": [{"q": "вопрос на понимание", "answer": "эталонный ответ в 1–3 предложениях"}]}

Правила: только факты из источников; ровно 3 вопроса — на понимание, а не на память (не «какой порт»), по-русски."""

ASK_PROMPT = """Ты — помощник по курсу AI Advent Challenge. Отвечай на вопрос участника по фрагментам источников [n]: страницы дней проекта, README, чат курса, прошлые разговоры участника с ассистентом.
Ставь ссылки [n] на фрагменты, откуда факт. Если в фрагментах ответа нет — так и скажи. Пиши по-русски, коротко и понятно, как объяснил бы опытный коллега."""

GRADE_PROMPT = """Ты проверяешь ответ участника курса на вопрос для самопроверки. Сравни ответ по смыслу с эталоном и источниками дня — формулировка может быть своя.
Ответ — только JSON: {"verdict": "correct | partial | wrong", "feedback": "1–3 предложения: что верно и чего не хватило", "hint": "одна фраза — на что обратить внимание, если ответ неполный; иначе пусто"}.
correct — главная мысль эталона есть; partial — есть часть или есть ошибка в деталях; wrong — мимо или пусто."""


class CourseError(RuntimeError):
    """Курс не собран или модель ответила не так."""


# ── хранилище ────────────────────────────────────────────────────────────


class CourseStore:
    """data/day35: course.json — главы, progress.json — счёт тренажёра, index.sqlite3 — RAG."""

    def __init__(self, folder: str | Path = DATA) -> None:
        self.folder = Path(folder)

    def _read(self, name: str) -> dict[str, Any]:
        try:
            data = json.loads((self.folder / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, name: str, data: dict[str, Any]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        temp = self.folder / f"{name}.tmp"
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self.folder / name)

    def course(self) -> dict[str, Any]:
        return self._read("course.json")

    def save_course(self, data: dict[str, Any]) -> None:
        self._write("course.json", data)

    def progress(self) -> dict[str, Any]:
        return self._read("progress.json")

    def record(self, day: int, index: int, verdict: str) -> dict[str, Any]:
        data = self.progress()
        item = data.setdefault(str(day), {"answers": 0, "correct": 0, "partial": 0, "last": {}})
        item["answers"] += 1
        if verdict in ("correct", "partial"):
            item[verdict] += 1
        item["last"][str(index)] = verdict
        self._write("progress.json", data)
        return data

    def index(self) -> RagIndex:
        return RagIndex(self.folder / "index.sqlite3")


# ── сборка: глава на день и индекс ───────────────────────────────────────


def _hash(sources: list[Source]) -> str:
    return hashlib.sha256(json.dumps([source.as_dict() for source in sources], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def day_material(day: int, sources: list[Source]) -> str:
    """Источники дня в одном тексте — по важности, в пределах бюджета."""
    order = {"page": 0, "readme": 1, "task": 2, "chat": 3, "talk": 4}
    parts, size = [], 0
    for source in sorted(sources, key=lambda item: order.get(item.kind, 9)):
        block = f"### {KINDS.get(source.kind, source.kind)} · {source.ref}\n{source.text.strip()}"
        if size + len(block) > DAY_CHARS:
            block = block[: max(0, DAY_CHARS - size)]
        if block:
            parts.append(block)
            size += len(block)
        if size >= DAY_CHARS:
            break
    return f"День {day}.\n\n" + "\n\n".join(parts)


def _ask_json(transport: Transport, system: str, user: str, *, model: str, max_tokens: int) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    response = transport.send(payload)
    text = (((response.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    try:
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except ValueError as error:
        raise CourseError(f"Модель вернула не JSON: {text[:160]}") from error
    if not isinstance(data, dict):
        raise CourseError("Модель вернула не объект")
    return data


def _strings(value: Any, limit: int) -> list[str]:
    return [str(item).strip() for item in value if str(item).strip()][:limit] if isinstance(value, list) else []


def chapter(day: int, sources: list[Source], transport: Transport, *, model: str = MODEL) -> dict[str, Any]:
    data = _ask_json(transport, CHAPTER_PROMPT, day_material(day, sources), model=model, max_tokens=1800)
    questions = [
        {"q": str(item.get("q") or "").strip(), "answer": str(item.get("answer") or "").strip()}
        for item in data.get("questions") or [] if isinstance(item, dict) and str(item.get("q") or "").strip()
    ][:3]
    return {
        "day": day,
        "title": str(data.get("title") or f"День {day}").strip(),
        "summary": str(data.get("summary") or "").strip(),
        "ideas": _strings(data.get("ideas"), 5),
        "built": _strings(data.get("built"), 4),
        "curator": _strings(data.get("curator"), 3),
        "questions": questions,
        "sources": {kind: sum(source.kind == kind for source in sources) for kind in KINDS},
        "hash": _hash(sources),
    }


def documents(sources: list[Source]) -> list[Document]:
    """Источник → markdown-документ: заголовок с днём и видом источника, чтобы куски знали, откуда они."""
    result = []
    for number, source in enumerate(sources):
        title = f"День {source.day} · {KINDS.get(source.kind, source.kind)}"
        text = f"# {title}\n\n## {source.ref}\n\n{source.text.strip()}\n"
        result.append(Document(f"day{source.day:02d}/{source.kind}/{number}", "doc", title, text))
    return result


def build(
    sources: list[Source],
    *,
    store: CourseStore,
    embedder: Embedder,
    transport: Transport,
    emit: Emit | None = None,
    model: str = MODEL,
) -> dict[str, Any]:
    """События: sources → index → chapter × N (кэш или новая) → built."""
    emit = emit or (lambda event: None)
    started = time.perf_counter()
    by_day: dict[int, list[Source]] = {}
    for source in sources:
        by_day.setdefault(source.day, []).append(source)
    emit({"type": "sources", "total": len(sources), "days": len(by_day), "kinds": {kind: sum(s.kind == kind for s in sources) for kind in KINDS}})

    info = store.index().build(documents(sources), embedder, strategies=(STRATEGY,))
    emit({"type": "index", "chunks": info["strategies"][STRATEGY]["chunks"], "embedded": info["strategies"][STRATEGY]["embedded"]})

    old = store.course().get("days") or {}
    chapters: dict[str, Any] = {}
    todo = []
    for day in sorted(by_day):
        cached = old.get(str(day))
        if cached and cached.get("hash") == _hash(by_day[day]):
            chapters[str(day)] = cached
            emit({"type": "chapter", "day": day, "title": cached["title"], "cached": True})
        else:
            todo.append(day)

    def write(day: int) -> None:
        try:
            chapters[str(day)] = chapter(day, by_day[day], transport, model=model)
            emit({"type": "chapter", "day": day, "title": chapters[str(day)]["title"], "cached": False})
        except (CourseError, TransportError) as error:
            emit({"type": "chapter", "day": day, "error": str(error)})

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        list(pool.map(write, todo))
    course = {
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": model,
        "sources": len(sources),
        "days": {key: chapters[key] for key in sorted(chapters, key=int)},
    }
    store.save_course(course)
    emit({"type": "built", "days": len(chapters), "fresh": len(todo), "ms": round((time.perf_counter() - started) * 1000)})
    return course


# ── вопрос по курсу ──────────────────────────────────────────────────────


def ask(question: str, *, store: CourseStore, embedder: Embedder, transport: Transport, day: int | None = None, model: str = MODEL) -> dict[str, Any]:
    index = store.index()
    hits = index.search(question, embedder, STRATEGY, k=TOP_K * 8 if day else TOP_K)
    if day:
        hits = [hit for hit in hits if hit.source.startswith(f"day{day:02d}/")][:TOP_K]
    if not hits:
        return {"answer": "В источниках этого дня ничего не нашлось.", "sources": [], "cited": []}
    blocks = [f"[{number}] {hit.title} · {hit.section}\n{hit.text}" for number, hit in enumerate(hits, start=1)]
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": ASK_PROMPT},
                     {"role": "user", "content": "Фрагменты:\n\n" + "\n\n---\n\n".join(blocks) + f"\n\nВопрос: {question}"}],
        "temperature": 0.3,
        "max_tokens": 1200,
        "thinking": {"type": "disabled"},
        "stream": False,
    }
    started = time.perf_counter()
    response = transport.send(payload)
    text = (((response.get("choices") or [{}])[0].get("message") or {}).get("content")) or ""
    cited = sorted({int(n) for n in CITATION.findall(text) if 1 <= int(n) <= len(hits)})
    return {
        "answer": text.strip(),
        "cited": cited,
        "ms": round((time.perf_counter() - started) * 1000),
        "sources": [
            {"n": number, "day": int(hit.source[3:5]), "kind": hit.source.split("/")[1], "title": hit.title,
             "ref": hit.section.split(" › ")[-1], "score": round(hit.score, 3)}
            for number, hit in enumerate(hits, start=1)
        ],
    }


# ── тренажёр ─────────────────────────────────────────────────────────────


def pick(course: dict[str, Any], day: int | None = None, *, rng: random.Random | None = None) -> dict[str, Any]:
    days = course.get("days") or {}
    candidates = [(int(key), number, item) for key, chapter_ in days.items() if day is None or int(key) == day
                  for number, item in enumerate(chapter_.get("questions") or [])]
    if not candidates:
        raise CourseError("Вопросов нет — сначала соберите курс")
    chosen_day, number, item = (rng or random).choice(candidates)
    return {"day": chosen_day, "index": number, "q": item["q"], "title": days[str(chosen_day)]["title"]}


def grade(course: dict[str, Any], day: int, index: int, answer: str, *, transport: Transport, model: str = MODEL) -> dict[str, Any]:
    chapter_ = (course.get("days") or {}).get(str(day))
    if not chapter_ or not 0 <= index < len(chapter_.get("questions") or []):
        raise CourseError("Нет такого вопроса")
    item = chapter_["questions"][index]
    context = f"Глава: {chapter_['title']}\n{chapter_['summary']}\nИдеи: " + "; ".join(chapter_["ideas"])
    data = _ask_json(
        transport, GRADE_PROMPT,
        f"{context}\n\nВопрос: {item['q']}\nЭталон: {item['answer']}\nОтвет участника: {answer}",
        model=model, max_tokens=500,
    )
    verdict = data.get("verdict") if data.get("verdict") in ("correct", "partial", "wrong") else "wrong"
    return {"verdict": verdict, "feedback": str(data.get("feedback") or "").strip(), "hint": str(data.get("hint") or "").strip(),
            "reference": item["answer"]}

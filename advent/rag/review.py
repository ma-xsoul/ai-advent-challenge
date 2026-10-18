"""
AI-ревью кода — день 32.

Один и тот же ревьюер работает в двух местах: в GitHub Actions на каждый
PR (scripts/ai_review.py) и на странице дня 32 — на любой паре веток.

1. diff — `git diff base...head`: только то, что принесла ветка. Плюс
   изменённые файлы целиком, с номерами строк — чтобы модель видела, что
   вокруг правки, и ссылалась на настоящие строки. Бюджет символов
   общий; файлы идут по важности (код, workflow, шаблоны, скрипты,
   тесты, стили, тексты), что не влезло — перечисляется явно;
2. RAG — из diff собираются запросы: путь файла и имена функций и
   классов из добавленных строк. По ним ищутся куски документации и
   кода проекта, кроме самих изменённых файлов — нужны правила и соседи,
   а не та же правка ещё раз (документацию берём всегда: правила проекта
   нужны, даже если PR их дописал). В GitHub Ollama нет, там поиск по словам
   (BM25 по корпусу дня 21); локально — векторный по индексу дня 31;
3. ревью — DeepSeek отвечает JSON: потенциальные баги, архитектурные
   проблемы, рекомендации; у каждого пункта файл, строка, что не так,
   почему и как исправить. Из JSON собирается markdown-комментарий.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..agent.transport import Transport, TransportError
from ..mcp.client import PROJECT_ROOT
from .chunking import Chunk, chunk
from .corpus import collect
from .embeddings import Embedder
from .index import RagIndex


MODEL = "deepseek-v4-flash"
STRATEGY = "structure"
DIFF_CHARS = 60_000
FILES_CHARS = 50_000
FILE_CHARS = 12_000
CHUNK_CHARS = 1_400
QUERIES = 6
PER_QUERY = 3
SOURCES = 8
DOC_SOURCES = 2
MARKER = "<!-- ai-review -->"

# Чем ниже — тем раньше файл попадает в бюджет.
PRIORITY = (
    (lambda p: p.endswith(".py") and not p.startswith("tests/"), 0),
    (lambda p: p.startswith(".github/"), 1),
    (lambda p: p.endswith(".html"), 2),
    (lambda p: p.endswith(".js"), 3),
    (lambda p: p.startswith("tests/"), 4),
    (lambda p: p.endswith(".css"), 5),
)
SKIP = re.compile(r"(\.lock|\.min\.js|\.png|\.jpe?g|\.svg|\.ico|\.sqlite3)$")

SYSTEM_PROMPT = """Ты — строгий ревьюер кода проекта AI Advent: Flask-лаборатория домашних заданий, где каждый день — отдельная страница со своим API.
Тебе дают diff pull request'а, изменённые файлы целиком с номерами строк и фрагменты документации и кода проекта [n], найденные по изменённым местам.

Найди:
1. bugs — потенциальные баги: конкретный сценарий, при котором код поведёт себя неправильно (необработанная ошибка, гонка, неверное условие, утечка, дыра в безопасности).
2. architecture — архитектурные проблемы: расхождения с устройством проекта из фрагментов [n] (где что лежит, зависимости через app.config ради тестов, данные в data/, тесты без сети и во временных папках), дублирование уже существующего кода, смешение слоёв.
3. recommendations — что стоит улучшить: тесты, читаемость, мелкие упрощения.

Правила:
- Каждый пункт привязан к месту: file — путь, line — номер строки из пронумерованного файла (или 0, если про файл целиком).
- Пиши по-русски, конкретно: что не так, почему это плохо, как исправить. Без общих советов и без похвалы.
- Не выдумывай проблем: если в разделе нечего сказать — пустой список. Лучше три настоящих пункта, чем десять натянутых.
- Если пункт опирается на документацию или соседний код — укажи номера фрагментов в sources.
- severity: high — сломает работу или безопасность, medium — заметный риск, low — мелочь.
- Файлы из списка «не вошли в ревью» существуют, ты их просто не видишь: не делай выводов об их содержимом или отсутствии (например, «нет тестов»).

Ответ — только JSON:
{"summary": "2–3 предложения: что делает PR и общий вывод",
 "bugs": [{"file": "...", "line": 0, "title": "...", "why": "...", "fix": "...", "severity": "high|medium|low", "sources": [1]}],
 "architecture": [...те же поля...],
 "recommendations": [...те же поля...]}"""

SECTIONS = (("bugs", "Потенциальные баги"), ("architecture", "Архитектурные проблемы"), ("recommendations", "Рекомендации"))
SEVERITY = {"high": "🔴", "medium": "🟠", "low": "⚪"}

Emit = Callable[[dict[str, Any]], None]


class ReviewError(RuntimeError):
    """Diff не собран или ревью не получено."""


# ── diff ─────────────────────────────────────────────────────────────────


@dataclass
class ChangedFile:
    path: str
    status: str
    added: int | None
    removed: int | None
    diff: str = ""
    content: str = ""

    def card(self) -> dict[str, Any]:
        return {"path": self.path, "status": self.status, "added": self.added, "removed": self.removed}


@dataclass
class ReviewInput:
    base: str
    head: str
    files: list[ChangedFile]
    included: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)

    @property
    def diff_chars(self) -> int:
        return sum(len(item.diff) for item in self.files if item.path in self.included)


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReviewError(f"git не запустился: {error}") from error
    if result.returncode != 0:
        raise ReviewError(f"git {args[0]}: {result.stderr.strip() or result.returncode}")
    return result.stdout


def _priority(path: str) -> int:
    return next((rank for test, rank in PRIORITY if test(path)), 6)


def collect_diff(base: str, head: str = "HEAD", *, root: Path = PROJECT_ROOT) -> ReviewInput:
    """Изменения ветки head относительно base: diff по файлам и сами файлы — в пределах бюджета."""
    for ref in (base, head):
        if ref.startswith("-"):
            raise ReviewError(f"Недопустимое имя ветки: {ref}")
    span = f"{base}...{head}"
    statuses = {}
    for line in _git(root, "diff", "--name-status", "--no-renames", span).splitlines():
        parts = line.split("\t")
        if len(parts) == 2:
            statuses[parts[1]] = parts[0][:1]
    files = []
    for line in _git(root, "diff", "--numstat", "--no-renames", span).splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            added, removed, path = parts
            files.append(ChangedFile(path, statuses.get(path, "M"), int(added) if added.isdigit() else None, int(removed) if removed.isdigit() else None))
    if not files:
        raise ReviewError(f"Между {base} и {head} нет изменений")
    commits = [line for line in _git(root, "log", "--pretty=format:%h %s", f"{base}..{head}").splitlines() if line]

    review = ReviewInput(base=base, head=head, files=sorted(files, key=lambda item: (_priority(item.path), item.path)), commits=commits[:20])
    diff_budget, files_budget = DIFF_CHARS, FILES_CHARS
    for item in review.files:
        if item.added is None or SKIP.search(item.path):
            review.skipped.append(item.path)
            continue
        item.diff = _git(root, "diff", "--no-renames", "-U3", span, "--", item.path)
        if len(item.diff) > diff_budget:
            review.skipped.append(item.path)
            continue
        diff_budget -= len(item.diff)
        review.included.append(item.path)
        if item.status != "D" and files_budget > 0:
            text = _git(root, "show", f"{head}:{item.path}")
            numbered = "\n".join(f"{number:>4} {line}" for number, line in enumerate(text.splitlines(), start=1))
            limit = min(FILE_CHARS, files_budget)
            item.content = numbered if len(numbered) <= limit else numbered[:limit] + "\n… (файл обрезан)"
            files_budget -= len(item.content)
    return review


# ── RAG: запросы из diff и два поисковика ────────────────────────────────


IDENTIFIER = re.compile(r"^\+\s*(?:async\s+)?(?:def|class|function|const|let)\s+([A-Za-z_][\w]*)", re.M)
HUNK = re.compile(r"^@@[^@]*@@\s*(.+)$", re.M)


def queries(review: ReviewInput) -> list[str]:
    """Запрос на файл: путь, имена новых функций и классов, контекст ханков."""
    result = []
    for item in review.files:
        if item.path not in review.included:
            continue
        names = list(dict.fromkeys(IDENTIFIER.findall(item.diff) + [hunk.strip() for hunk in HUNK.findall(item.diff)]))[:8]
        stem = Path(item.path).stem.replace("_", " ")
        result.append(" ".join([item.path, stem, *names]))
        if len(result) >= QUERIES:
            break
    return result


class Retriever(Protocol):
    name: str

    def search(self, query: str, k: int, *, docs_only: bool = False) -> list[dict[str, Any]]: ...


TOKEN = re.compile(r"[a-zа-яё][a-zа-яё0-9]{2,}")


def _tokens(text: str) -> list[str]:
    words = []
    for word in re.findall(r"[A-Za-zА-Яа-яЁё_][\wЁё]*", text):
        words.extend(part for part in re.split(r"_|(?<=[a-zа-я])(?=[A-ZА-Я])", word) if part)
    return [word for word in (w.lower() for w in words) if TOKEN.fullmatch(word)]


def _card(piece: Chunk | Any, score: float) -> dict[str, Any]:
    return {"source": piece.source, "section": piece.section, "kind": piece.kind,
            "lines": f"{piece.start_line}–{piece.end_line}", "score": round(score, 3), "text": piece.text}


class LexicalRetriever:
    """BM25 по кускам корпуса — без векторов и без Ollama: так ищет ревьюер в GitHub."""

    name = "по словам · BM25"

    def __init__(self, pieces: list[Chunk], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.pieces = pieces
        self.k1, self.b = k1, b
        self.docs = [Counter(_tokens(f"{piece.source} {piece.section} {piece.text}")) for piece in pieces]
        self.lengths = [sum(doc.values()) for doc in self.docs]
        self.avg = sum(self.lengths) / len(self.lengths) if self.lengths else 1.0
        frequency: Counter[str] = Counter()
        for doc in self.docs:
            frequency.update(doc.keys())
        total = len(self.docs)
        self.idf = {term: math.log(1 + (total - count + 0.5) / (count + 0.5)) for term, count in frequency.items()}

    @classmethod
    def from_project(cls, root: Path = PROJECT_ROOT) -> LexicalRetriever:
        return cls(chunk(collect(root), STRATEGY))

    def search(self, query: str, k: int, *, docs_only: bool = False) -> list[dict[str, Any]]:
        terms = set(_tokens(query))
        scored = []
        for piece, doc, length in zip(self.pieces, self.docs, self.lengths):
            if docs_only and piece.kind != "doc":
                continue
            score = sum(
                self.idf.get(term, 0) * doc[term] * (self.k1 + 1) / (doc[term] + self.k1 * (1 - self.b + self.b * length / self.avg))
                for term in terms if term in doc
            )
            if score > 0:
                scored.append((score, piece))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [_card(piece, score) for score, piece in scored[:k]]


class VectorRetriever:
    """Векторный поиск по индексу дня 31 — локально, где есть Ollama и bge-m3."""

    name = "векторный · bge-m3"

    def __init__(self, index: RagIndex, embedder: Embedder) -> None:
        self.index, self.embedder = index, embedder

    def search(self, query: str, k: int, *, docs_only: bool = False) -> list[dict[str, Any]]:
        hits = self.index.search(query, self.embedder, STRATEGY, k=k * 4 if docs_only else k)
        if docs_only:
            hits = [hit for hit in hits if hit.kind == "doc"]
        return [_card(hit, hit.score) for hit in hits[:k]]


def retrieve(review: ReviewInput, retriever: Retriever) -> tuple[list[str], list[dict[str, Any]]]:
    """Куски по запросам из diff, без изменённого кода; документации — не меньше двух."""
    changed = {item.path for item in review.files}
    asked = queries(review)
    found: dict[tuple[str, str], dict[str, Any]] = {}

    def take(cards: list[dict[str, Any]], limit: int) -> None:
        added = 0
        for card in cards:
            key = (card["source"], card["lines"])
            if (card["source"] in changed and card["kind"] != "doc") or key in found or added >= limit:
                continue
            found[key] = card
            added += 1

    for query in asked:
        take(retriever.search(query, PER_QUERY * 3), PER_QUERY)
    docs = [card for card in found.values() if card["kind"] == "doc"]
    if len(docs) < DOC_SOURCES:
        take(retriever.search(" ".join(asked) + " архитектура слои конфиг тесты данные", DOC_SOURCES * 3, docs_only=True), DOC_SOURCES - len(docs))
    cards = sorted(found.values(), key=lambda card: (card["kind"] != "doc", -card["score"]))[:SOURCES]
    return asked, [{"n": number, **card} for number, card in enumerate(cards, start=1)]


# ── ревью ────────────────────────────────────────────────────────────────


def prompt(review: ReviewInput, sources: list[dict[str, Any]]) -> str:
    parts = [f"PR: {review.head} → {review.base}"]
    if review.commits:
        parts.append("Коммиты:\n" + "\n".join(f"- {commit}" for commit in review.commits))
    if review.skipped:
        parts.append("Не вошли в ревью (бюджет или бинарные): " + ", ".join(review.skipped))
    parts.append("Diff:\n" + "\n".join(item.diff for item in review.files if item.path in review.included))
    files = [f"=== {item.path} ===\n{item.content}" for item in review.files if item.content]
    if files:
        parts.append("Изменённые файлы целиком (номер строки, затем строка):\n\n" + "\n\n".join(files))
    blocks = []
    for card in sources:
        text = card["text"] if len(card["text"]) <= CHUNK_CHARS else card["text"][:CHUNK_CHARS] + "\n…"
        blocks.append(f"[{card['n']}] {card['source']} · {card['section']} · строки {card['lines']}\n{text}")
    parts.append("Фрагменты документации и кода проекта:\n\n" + "\n\n---\n\n".join(blocks))
    return "\n\n".join(parts)


def _items(raw: Any, total: int) -> list[dict[str, Any]]:
    items = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            continue
        line = item.get("line")
        items.append({
            "file": str(item.get("file") or "").strip(),
            "line": int(line) if isinstance(line, (int, float)) or str(line).isdigit() else 0,
            "title": str(item.get("title")).strip(),
            "why": str(item.get("why") or "").strip(),
            "fix": str(item.get("fix") or "").strip(),
            "severity": item.get("severity") if item.get("severity") in SEVERITY else "low",
            "sources": [int(n) for n in item.get("sources") or [] if str(n).isdigit() and 1 <= int(n) <= total],
        })
    return items


def generate(review: ReviewInput, sources: list[dict[str, Any]], transport: Transport, *, model: str = MODEL) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt(review, sources)}],
        "temperature": 0.2,
        "max_tokens": 4000,
        "thinking": {"type": "disabled"},
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    started = time.perf_counter()
    response = transport.send(payload)
    ms = round((time.perf_counter() - started) * 1000)
    choices = response.get("choices") or [{}]
    text = ((choices[0].get("message") or {}).get("content")) or ""
    try:
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except ValueError as error:
        raise ReviewError(f"Модель вернула не JSON: {text[:200]}") from error
    usage = response.get("usage") or {}
    return {
        "summary": str(data.get("summary") or "").strip(),
        **{key: _items(data.get(key), len(sources)) for key, _ in SECTIONS},
        "stats": {"ms": ms, "prompt_tokens": usage.get("prompt_tokens") or 0, "completion_tokens": usage.get("completion_tokens") or 0,
                  "finish_reason": choices[0].get("finish_reason"), "model": model},
    }


def run(
    base: str,
    head: str,
    *,
    transport: Transport,
    retriever: Retriever,
    root: Path = PROJECT_ROOT,
    emit: Emit | None = None,
    model: str = MODEL,
) -> dict[str, Any]:
    """События: diff → retrieved → review. Возвращает всё вместе — для markdown и страницы."""
    emit = emit or (lambda event: None)
    review = collect_diff(base, head, root=root)
    emit({"type": "diff", "base": base, "head": head, "files": [item.card() for item in review.files],
          "included": review.included, "skipped": review.skipped, "commits": review.commits, "diff_chars": review.diff_chars})
    asked, sources = retrieve(review, retriever)
    emit({"type": "retrieved", "retriever": retriever.name, "queries": asked,
          "sources": [{key: value for key, value in card.items() if key != "text"} for card in sources]})
    result = generate(review, sources, transport, model=model)
    emit({"type": "review", **result})
    return {"base": base, "head": head, "retriever": retriever.name, "files": [item.card() for item in review.files],
            "included": review.included, "skipped": review.skipped, "queries": asked, "sources": sources, **result}


def to_markdown(result: dict[str, Any], *, link: Callable[[str, int], str] | None = None) -> str:
    """Комментарий в PR: вывод, три раздела, источники RAG под катом. MARKER — чтобы обновлять один и тот же."""
    def where(item: dict[str, Any]) -> str:
        label = f"{item['file']}:{item['line']}" if item["line"] else item["file"]
        return f"[`{label}`]({link(item['file'], item['line'])})" if link and item["file"] else f"`{label}`"

    lines = [MARKER, "## 🤖 AI-ревью", "", result["summary"] or "—", ""]
    for key, title in SECTIONS:
        items = result[key]
        lines.append(f"### {title} · {len(items)}")
        if not items:
            lines += ["Не нашёл.", ""]
            continue
        for item in items:
            refs = " ".join(f"[{n}]" for n in item["sources"])
            lines.append(f"- {SEVERITY[item['severity']]} **{item['title']}** — {where(item)}{f' · по {refs}' if refs else ''}")
            if item["why"]:
                lines.append(f"  - почему: {item['why']}")
            if item["fix"]:
                lines.append(f"  - как исправить: {item['fix']}")
        lines.append("")
    sources = "\n".join(f"- [{card['n']}] `{card['source']}` · {card['section']} · строки {card['lines']}" for card in result["sources"])
    stats = result["stats"]
    lines += [
        "<details><summary>RAG: что нашлось по изменённым местам</summary>",
        "",
        f"Поиск: {result['retriever']}. Запросы из diff: " + "; ".join(f"`{query}`" for query in result["queries"]),
        "",
        sources or "—",
        "",
        "</details>",
        "",
        f"<sub>{stats['model']} · файлов в ревью {len(result['included'])} из {len(result['files'])}"
        + (f" (не вошли: {', '.join(result['skipped'])})" if result["skipped"] else "")
        + f" · токенов {stats['prompt_tokens'] + stats['completion_tokens']} · {stats['ms'] / 1000:.1f} с</sub>",
    ]
    return "\n".join(lines)


__all__ = [
    "LexicalRetriever", "MARKER", "ReviewError", "ReviewInput", "TransportError", "VectorRetriever",
    "collect_diff", "generate", "queries", "retrieve", "run", "to_markdown",
]

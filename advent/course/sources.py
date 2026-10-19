"""
Источники курса — день 35.

Из чего собирается конспект каждого дня:

- page   — страница дня в проекте: текст задания, цитаты куратора,
           объяснение практики и карточки (templates/days/dayNN.html);
- readme — абзац «Day N» в README: что построено и чем закончилась
           живая проверка;
- task   — пост задания в Telegram-чате курса;
- chat   — сообщения куратора (Алексей Гладков) и сводки бота чата в дни
           этого задания: между постом дня N и постом дня N+1;
- talk   — наши разговоры в Claude Code: вопросы «зачем», «объясни» и
           ответы на них. День берётся из git-ветки, на которой шёл
           разговор (day-N), — она записана в каждой строке истории.

Сообщения других участников чата не берутся. Всё читается с диска этого
мака и остаётся в data/day35.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from ..mcp.client import PROJECT_ROOT
from ..rag.corpus import page_text


DAYS = 35
CURATOR = "Алексей Гладков"
BOT = "AI Advent Alumni Bot"
TASK_POST = re.compile(r"🔥\s*День\s*(\d{1,2})\b")
DAY_BRANCH = re.compile(r"^day-(\d{1,2})$")
QUESTION = re.compile(r"\?|объясн|зачем|для чего|почему|научит|простыми словами|простым языком|что такое|как работает", re.I)
ANSWER_CHARS = 2500
KINDS = {"page": "страница дня", "readme": "README", "task": "задание в чате", "chat": "чат курса", "talk": "наш разговор"}


@dataclass(frozen=True)
class Source:
    day: int
    kind: str
    ref: str
    text: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── проект: страницы дней и README ───────────────────────────────────────


def page_sources(root: Path = PROJECT_ROOT) -> list[Source]:
    result = []
    for day in range(1, DAYS + 1):
        path = root / "advent" / "templates" / "days" / f"day{day:02d}.html"
        if path.is_file():
            result.append(Source(day, "page", path.relative_to(root).as_posix(), page_text(path.read_text(encoding="utf-8"))))
    return result


def readme_sources(root: Path = PROJECT_ROOT) -> list[Source]:
    """Абзацы «- Day N — …» из README: строка и её продолжение с отступом."""
    path = root / "README.md"
    if not path.is_file():
        return []
    result, current, lines = [], None, []
    for line in path.read_text(encoding="utf-8").splitlines():
        found = re.match(r"^- Day (\d{1,2}) — (.*)$", line)
        if found:
            if current:
                result.append(Source(current, "readme", f"README.md · Day {current}", " ".join(lines)))
            current, lines = int(found.group(1)), [found.group(2)]
        elif current and line.startswith("  ") and line.strip():
            lines.append(line.strip())
        elif current:
            result.append(Source(current, "readme", f"README.md · Day {current}", " ".join(lines)))
            current, lines = None, []
    if current:
        result.append(Source(current, "readme", f"README.md · Day {current}", " ".join(lines)))
    return result


# ── чат курса: выгрузка Telegram ─────────────────────────────────────────


class _Export(HTMLParser):
    """messages.html выгрузки Telegram → сообщения: id, автор, время, текст."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._field: str | None = None
        self._depth = 0
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        classes = (data.get("class") or "").split()
        if tag == "div" and "message" in classes and "default" in classes:
            self._current = {"id": data.get("id") or "", "from": "", "text": "", "date": ""}
            self.messages.append(self._current)
        if self._current is None:
            return
        if tag == "div" and not self._field and ("text" in classes or "from_name" in classes):
            self._field, self._depth, self._buffer = ("text" if "text" in classes else "from"), 1, []
            return
        if tag == "div" and "date" in classes and "details" in classes:
            self._current["date"] = data.get("title") or ""
        if self._field:
            if tag == "div":
                self._depth += 1
            if tag == "br":
                self._buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._field and tag == "div":
            self._depth -= 1
            if self._depth == 0 and self._current is not None:
                self._current[self._field] = html.unescape("".join(self._buffer)).strip()
                self._field = None

    def handle_data(self, data: str) -> None:
        if self._field:
            self._buffer.append(data)


def chat_messages(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Сообщения всех выгрузок без повторов: автор продолжает подряд идущие сообщения."""
    seen: dict[str, dict[str, Any]] = {}
    for path in paths:
        parser = _Export()
        parser.feed(path.read_text(encoding="utf-8"))
        author = ""
        for message in parser.messages:
            author = message["from"] or author
            when = _when(message["date"])
            if message["id"] and when and message["text"]:
                seen[message["id"]] = {**message, "from": author, "when": when}
    return sorted(seen.values(), key=lambda message: message["when"])


def _when(value: str) -> datetime | None:
    try:
        return datetime.strptime(value[:19], "%d.%m.%Y %H:%M:%S")
    except ValueError:
        return None


def chat_sources(paths: Iterable[Path]) -> list[Source]:
    """Пост задания — сам по себе; куратор и сводки бота — в окне от поста дня N до поста дня N+1."""
    messages = chat_messages(paths)
    posts: dict[int, dict[str, Any]] = {}
    for message in messages:
        found = TASK_POST.search(message["text"])
        if found and 1 <= int(found.group(1)) <= DAYS and int(found.group(1)) not in posts:
            posts[int(found.group(1))] = message
    result = [Source(day, "task", f"чат · пост задания {post['when']:%d.%m}", post["text"]) for day, post in sorted(posts.items())]
    order = sorted(posts.items(), key=lambda item: item[1]["when"])
    for number, (day, post) in enumerate(order):
        end = order[number + 1][1]["when"] if number + 1 < len(order) else None
        window = [m for m in messages if m["when"] > post["when"] and (end is None or m["when"] < end)]
        curator = [m for m in window if m["from"] == CURATOR]
        # Подряд идущие реплики куратора — одна мысль: склеиваем, если между ними меньше 10 минут.
        groups: list[list[dict[str, Any]]] = []
        for message in curator:
            if groups and (message["when"] - groups[-1][-1]["when"]).total_seconds() < 600:
                groups[-1].append(message)
            else:
                groups.append([message])
        for group in groups:
            text = " ".join(item["text"] for item in group)
            if len(text) >= 40:
                result.append(Source(day, "chat", f"чат · {CURATOR} {group[0]['when']:%d.%m %H:%M}", text))
        for message in window:
            if message["from"].startswith(BOT) and "Что обсуждали" in message["text"]:
                result.append(Source(day, "chat", f"чат · сводка {message['when']:%d.%m}", message["text"]))
    return result


# ── наши разговоры в Claude Code ─────────────────────────────────────────


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text")
    return ""


def talk_sources(paths: Iterable[Path]) -> list[Source]:
    """Вопрос пользователя «зачем / объясни / почему» на ветке day-N и мой ответ на него."""
    result, seen = [], set()
    for path in paths:
        question: dict[str, Any] | None = None
        answer: list[str] = []

        def flush() -> None:
            if question and answer and question["text"] not in seen:
                seen.add(question["text"])
                text = "\n".join(answer).strip()
                text = text if len(text) <= ANSWER_CHARS else text[:ANSWER_CHARS] + "…"
                result.append(Source(question["day"], "talk", f"наш разговор · {question['when']}", f"Вопрос: {question['text']}\n\nОтвет: {text}"))

        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                kind = item.get("type")
                message = item.get("message") or {}
                if kind == "user" and not item.get("isMeta") and not item.get("toolUseResult"):
                    text = _text(message.get("content")).strip()
                    if not text or text.startswith("<") or any(isinstance(block, dict) and block.get("type") == "tool_result" for block in message.get("content") or [] if isinstance(message.get("content"), list)):
                        continue
                    flush()
                    branch = DAY_BRANCH.match(item.get("gitBranch") or "")
                    good = branch and len(text) >= 20 and QUESTION.search(text) and 1 <= int(branch.group(1)) <= DAYS
                    question = {"day": int(branch.group(1)), "text": text[:600], "when": str(item.get("timestamp") or "")[:10]} if good else None
                    answer = []
                elif kind == "assistant" and question is not None:
                    text = _text(message.get("content")).strip()
                    if text:
                        answer.append(text)
        flush()
    return result


def default_chat_paths(folder: Path) -> list[Path]:
    return sorted(folder.glob("*.html")) if folder.is_dir() else []


def default_talk_paths() -> list[Path]:
    folder = Path.home() / ".claude" / "projects" / ("-" + str(PROJECT_ROOT).strip("/").replace("/", "-"))
    return sorted(folder.glob("*.jsonl")) if folder.is_dir() else []


def collect(*, root: Path = PROJECT_ROOT, chat: Iterable[Path] = (), talks: Iterable[Path] = ()) -> list[Source]:
    return [*page_sources(root), *readme_sources(root), *chat_sources(chat), *talk_sources(talks)]

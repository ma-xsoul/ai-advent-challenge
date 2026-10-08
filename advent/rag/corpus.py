"""
Корпус дня 21: что индексируем.

Наш собственный проект — три вида документов:

- doc  — README и docs/*.md: описание проекта человеческим языком;
- code — advent/**/*.py: код с докстрингами и комментариями по-русски;
- page — страницы дней (templates/days/*.html), очищенные от разметки:
  тексты заданий, объяснения и карточки практики.

Каждый документ — путь, вид, заголовок и текст. Страницы приводятся к
markdown: заголовки h1–h3 становятся «#», пункты списков — «-», чтобы
структурная нарезка резала их по разделам так же, как README.
"""

from __future__ import annotations

import ast
import html
import re
from dataclasses import dataclass
from pathlib import Path

from ..mcp.client import PROJECT_ROOT


# Страница ≈ 1800 символов — так и считаем «20–30 страниц» из задания.
CHARS_PER_PAGE = 1800

# Заглушки вроде «"""Day 3 page package."""» — в индексе им делать нечего.
MIN_CODE_CHARS = 120

# Контрольные вопросы с ответами — не часть базы: иначе поиск подглядывал
# бы в ответы, как студент в шпаргалку.
EXCLUDED = {"advent/rag/probes.py"}


@dataclass(frozen=True)
class Document:
    source: str  # путь от корня проекта — он же source в метаданных чанка
    kind: str  # doc / code / page
    title: str
    text: str

    @property
    def pages(self) -> float:
        return len(self.text) / CHARS_PER_PAGE


def collect(root: Path = PROJECT_ROOT) -> list[Document]:
    """Все документы корпуса в стабильном порядке."""
    documents: list[Document] = []
    for path in [root / "README.md", *sorted((root / "docs").glob("**/*.md"))]:
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            documents.append(Document(_relative(path, root), "doc", _markdown_title(text, path), text))
    for path in sorted((root / "advent").glob("**/*.py")):
        if _relative(path, root) in EXCLUDED:
            continue
        text = path.read_text(encoding="utf-8")
        if len(text.strip()) >= MIN_CODE_CHARS:
            documents.append(Document(_relative(path, root), "code", _module_title(text, path, root), text))
    for path in sorted((root / "advent" / "templates" / "days").glob("*.html")):
        text = page_text(path.read_text(encoding="utf-8"))
        if text.strip():
            documents.append(Document(_relative(path, root), "page", _markdown_title(text, path), text))
    return documents


def page_text(source: str) -> str:
    """HTML-шаблон дня → markdown-текст: без разметки Jinja и тегов, с заголовками и пунктами."""
    text = re.sub(r"{#.*?#}", "", source, flags=re.S)
    text = re.sub(r"{%.*?%}", "", text, flags=re.S)
    text = re.sub(r"{{.*?}}", "", text, flags=re.S)
    text = re.sub(r"<(script|style|template)\b.*?</\1>", "", text, flags=re.S | re.I)
    for level in (1, 2, 3):
        text = re.sub(
            rf"<h{level}\b[^>]*>(.*?)</h{level}>",
            lambda match, level=level: "\n" + "#" * level + " " + _inline(match.group(1)) + "\n",
            text,
            flags=re.S | re.I,
        )
    text = re.sub(r"<li\b[^>]*>", "\n- ", text, flags=re.I)
    text = re.sub(r"<(br|/p|/div|/li|/dt|/dd|/blockquote|/summary)\b[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def _inline(fragment: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _markdown_title(text: str, path: Path) -> str:
    match = re.search(r"^#\s+(.+)$", text, flags=re.M)
    return match.group(1).strip() if match else path.stem


def _module_title(text: str, path: Path, root: Path) -> str:
    """Первая строка докстринга модуля, иначе — имя модуля."""
    try:
        doc = ast.get_docstring(ast.parse(text))
    except SyntaxError:
        doc = None
    first = next((line.strip() for line in (doc or "").splitlines() if line.strip()), "")
    return first or _relative(path, root)

"""
Две стратегии нарезки — день 21.

fixed — окна фиксированного размера с перекрытием. Не знает ничего о
структуре: режет посреди функции или раздела, зато все куски одного
размера и ничего не теряется на стыках — соседние окна перекрываются.

structure — по смыслу документа:
- markdown и страницы дней — по заголовкам (#, ##, ###), раздел помнит
  всю цепочку заголовков над ним;
- код — по функциям и классам (ast): докстринг модуля, каждая функция,
  класс целиком или, если он большой, «шапка» класса и каждый метод
  отдельно; код верхнего уровня между ними — отдельными кусками.

Слишком длинный раздел дорезается по абзацам, слишком короткие соседние —
склеиваются, чтобы в индексе не было кусков из одной строки.

У каждого чанка одинаковые метаданные: source, title, section, chunk_id,
строки начала и конца. Для fixed раздел берётся из структурной нарезки
того же файла — тот, в котором окно начинается.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from .corpus import Document


FIXED_SIZE = 1200
FIXED_OVERLAP = 200
STRUCTURE_MAX = 2400
STRUCTURE_MIN = 300

STRATEGIES = ("fixed", "structure")
STRATEGY_TITLES = {
    "fixed": f"фиксированный размер · {FIXED_SIZE} симв., перекрытие {FIXED_OVERLAP}",
    "structure": "по структуре · заголовки, функции, классы",
}


@dataclass(frozen=True)
class Chunk:
    strategy: str
    chunk_id: str
    source: str
    title: str
    section: str
    kind: str
    start_line: int
    end_line: int
    text: str

    @property
    def chars(self) -> int:
        return len(self.text)


def chunk(documents: list[Document], strategy: str) -> list[Chunk]:
    if strategy not in STRATEGIES:
        raise ValueError(f"Неизвестная стратегия: {strategy}")
    chunks: list[Chunk] = []
    for document in documents:
        sections = _sections(document)
        pieces = _fixed(document, sections) if strategy == "fixed" else sections
        for number, (section, start, end, text) in enumerate(pieces, start=1):
            chunks.append(
                Chunk(
                    strategy=strategy,
                    chunk_id=f"{strategy}:{document.source}#{number}",
                    source=document.source,
                    title=document.title,
                    section=section,
                    kind=document.kind,
                    start_line=start,
                    end_line=end,
                    text=text,
                )
            )
    return chunks


# Кусок до сборки в Chunk: раздел, первая и последняя строка (с 1), текст.
Piece = tuple[str, int, int, str]


# ── fixed ────────────────────────────────────────────────────────────────


def _fixed(document: Document, sections: list[Piece]) -> list[Piece]:
    text = document.text
    starts = _line_starts(text)
    pieces: list[Piece] = []
    position = 0
    while position < len(text):
        end = min(position + FIXED_SIZE, len(text))
        if end < len(text):
            # Не рвём слово: отступаем до ближайшего пробела или переноса.
            space = max(text.rfind(" ", position + FIXED_SIZE // 2, end), text.rfind("\n", position + FIXED_SIZE // 2, end))
            end = space if space > position else end
        body = text[position:end].strip()
        if body:
            first, last = _line_of(starts, position), _line_of(starts, max(position, end - 1))
            pieces.append((_section_at(sections, first), first, last, body))
        if end >= len(text):
            break
        position = max(end - FIXED_OVERLAP, position + 1)
    return pieces


def _section_at(sections: list[Piece], line: int) -> str:
    for section, start, end, _ in sections:
        if start <= line <= end:
            return section
    return sections[-1][0] if sections else "—"


def _line_starts(text: str) -> list[int]:
    return [0] + [match.end() for match in re.finditer("\n", text)]


def _line_of(starts: list[int], offset: int) -> int:
    low, high = 0, len(starts) - 1
    while low < high:
        middle = (low + high + 1) // 2
        if starts[middle] <= offset:
            low = middle
        else:
            high = middle - 1
    return low + 1


# ── structure ────────────────────────────────────────────────────────────


def _sections(document: Document) -> list[Piece]:
    raw = _code_sections(document.text) if document.kind == "code" else _markdown_sections(document.text)
    return _normalize(raw)


def _markdown_sections(text: str) -> list[Piece]:
    lines = text.splitlines()
    pieces: list[Piece] = []
    trail: list[tuple[int, str]] = []
    start = 1
    body: list[str] = []

    def flush(end: int) -> None:
        content = "\n".join(body).strip()
        if content:
            name = " › ".join(title for _, title in trail) or "вступление"
            pieces.append((name, start, end, content))

    in_fence = False
    for number, line in enumerate(lines, start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        heading = None if in_fence else re.match(r"^(#{1,3})\s+(.+?)\s*$", line)
        if heading:
            flush(number - 1)
            level = len(heading.group(1))
            trail = [item for item in trail if item[0] < level] + [(level, heading.group(2))]
            start, body = number, [line]
        else:
            body.append(line)
    flush(len(lines))
    return pieces


def _code_sections(text: str) -> list[Piece]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _markdown_sections(text)
    lines = text.splitlines()
    pieces: list[Piece] = []
    covered: set[int] = set()

    def take(name: str, start: int, end: int) -> None:
        pieces.append((name, start, end, "\n".join(lines[start - 1 : end])))
        covered.update(range(start, end + 1))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])
            end = node.end_lineno or node.lineno
            size = sum(len(line) + 1 for line in lines[start - 1 : end])
            if isinstance(node, ast.ClassDef) and size > STRUCTURE_MAX:
                methods = [item for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
                head_end = (min([item.lineno for item in methods] + [end + 1]) - 1) if methods else end
                take(f"class {node.name}", start, head_end)
                for method in methods:
                    method_start = min([method.lineno] + [decorator.lineno for decorator in method.decorator_list])
                    take(f"{node.name}.{method.name}", method_start, method.end_lineno or method.lineno)
            else:
                kind = "class" if isinstance(node, ast.ClassDef) else "def"
                take(f"{kind} {node.name}", start, end)
        elif isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant) and node is tree.body[0]:
            take("докстринг модуля", node.lineno, node.end_lineno or node.lineno)

    # Код верхнего уровня между функциями: импорты, константы, реестры.
    block: list[int] = []
    for number in range(1, len(lines) + 1):
        if number not in covered and lines[number - 1].strip():
            block.append(number)
        elif block:
            take("код модуля", block[0], block[-1])
            block = []
    if block:
        take("код модуля", block[0], block[-1])

    pieces.sort(key=lambda piece: piece[1])
    return pieces


def _normalize(pieces: list[Piece]) -> list[Piece]:
    """Длинные разделы — дорезать (по абзацам, а где их нет — по строкам), короткие соседние — склеить."""
    split: list[Piece] = []
    for piece in pieces:
        split.extend(_split_long(piece))

    merged: list[Piece] = []
    for piece in split:
        if merged and _fits(merged[-1], piece) and (len(merged[-1][3]) < STRUCTURE_MIN or len(piece[3]) < STRUCTURE_MIN):
            merged[-1] = _join(merged[-1], piece)
        else:
            merged.append(piece)
    return [(_label(name), start, end, text) for name, start, end, text in merged]


def _split_long(piece: Piece) -> list[Piece]:
    name, start, end, text = piece
    if len(text) <= STRUCTURE_MAX:
        return [piece]
    # Режем по строкам, но стараемся на пустой строке — там кончается абзац или блок кода.
    lines = text.split("\n")
    out: list[Piece] = []
    group: list[str] = []
    group_start = start
    for offset, line in enumerate(lines):
        size = sum(len(item) + 1 for item in group)
        boundary = not line.strip()
        if group and (size + len(line) > STRUCTURE_MAX or (boundary and size > STRUCTURE_MAX * 0.6)):
            body = "\n".join(group).strip()
            if body:
                out.append((name, group_start, start + offset - 1, body))
            group, group_start = [], start + offset
        group.append(line)
    body = "\n".join(group).strip()
    if body:
        out.append((name, group_start, end, body))
    return out


def _fits(left: Piece, right: Piece) -> bool:
    return len(left[3]) + len(right[3]) + 2 <= STRUCTURE_MAX


def _join(left: Piece, right: Piece) -> Piece:
    # Имена склеенных разделов копятся через служебный разделитель, а
    # человеческая подпись собирается из них один раз — в _label.
    return (f"{left[0]}{_GLUE}{right[0]}", left[1], right[2], left[3] + "\n\n" + right[3])


_GLUE = "\x1f"


def _label(name: str) -> str:
    """«A › B › x», «A › B › y» → «A › B › x, y»: общий путь один раз, дальше — различия."""
    names = list(dict.fromkeys(name.split(_GLUE)))
    if len(names) == 1:
        return names[0]
    chains = [item.split(" › ") for item in names]
    common = 0
    while all(len(chain) > common for chain in chains) and len({chain[common] for chain in chains}) == 1:
        common += 1
    # Раздел и его подразделы: сам раздел уже назван в общем пути — хвост у него пустой.
    tails = [tail for tail in dict.fromkeys(" › ".join(chain[common:]) for chain in chains) if tail]
    body = ", ".join(tails[:3]) + (", …" if len(tails) > 3 else "")
    head = " › ".join(chains[0][:common])
    return " › ".join(part for part in (head, body) if part)


def stats(chunks: list[Chunk]) -> dict[str, int | float]:
    sizes = [item.chars for item in chunks] or [0]
    return {
        "chunks": len(chunks),
        "avg_chars": round(sum(sizes) / len(sizes)),
        "min_chars": min(sizes),
        "max_chars": max(sizes),
        "total_chars": sum(sizes),
        "sources": len({item.source for item in chunks}),
    }

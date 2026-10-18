"""
MCP-сервер файлов проекта — день 34.

Ассистент работает с файлами сам, но правки не попадают в проект сразу:
всё, что он пишет, ложится в черновик (data/day34/draft — копии файлов
по тем же путям). Читает и ищет он уже с учётом черновика, а человек
видит diff и решает: применить или выбросить.

Инструменты:

- list_files — файлы по папке и маске, сколько в каждой подпапке;
- search — строка или регулярное выражение по многим файлам сразу:
  файл, номер строки, сама строка;
- read_file — файл кусками с номерами строк;
- write_file — новый файл или полная замена — в черновик;
- edit_file — точечная правка: заменить фрагмент, который встречается
  ровно один раз, — в черновик;
- draft_diff — что уже подготовлено: файлы, +/− строк, unified diff.

Видно только текстовые файлы; .git, data/, .venv, .env и служебные папки
скрыты. Писать можно только в docs/ и в .md в корне — код ассистент
читает, но не трогает.

Запуск: python -m advent.mcp.servers.files (stdio). Корень —
ADVENT_FILES_ROOT, черновик — ADVENT_FILES_DRAFT.
"""

from __future__ import annotations

import difflib
import fnmatch
import os
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field


DEFAULT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DRAFT = DEFAULT_ROOT / "data" / "day34" / "draft"

TEXT = {".py", ".md", ".html", ".css", ".js", ".json", ".yml", ".yaml", ".txt", ".toml", ".cfg", ".ini"}
SKIP_DIRS = {".git", "data", ".venv", "venv", "__pycache__", "node_modules", ".claude", ".idea", ".pytest_cache"}
SKIP_FILES = {".env", "HANDOFF.md"}
READ_LINES = 400
READ_CHARS = 5500
SEARCH_LIMIT = 80
LINE_CHARS = 220

RelPath = Annotated[str, Field(max_length=300, description="Путь от корня проекта, например advent/days/day31/routes.py")]
Folder = Annotated[str, Field(default="", max_length=300, description="Папка от корня проекта; пусто — весь проект")]


class WorkspaceError(ToolError):
    """Путь недоступен или правка невозможна — текст дойдёт до модели как ошибка инструмента."""


class Workspace:
    """Файлы проекта плюс черновик поверх них. Сервер и страница дня 34 работают через него."""

    def __init__(self, root: str | Path, draft: str | Path) -> None:
        self.root = Path(root).resolve()
        self.draft = Path(draft)

    # ── пути ─────────────────────────────────────────────────────────────

    def rel(self, path: str, *, folder: bool = False) -> str:
        raw = path.strip().replace("\\", "/").strip("/")
        if raw in ("", "."):
            if folder:
                return ""
            raise WorkspaceError("Нужен путь к файлу")
        parts = PurePosixPath(raw).parts
        if raw.startswith(("~", "-")) or ".." in parts or any(part in SKIP_DIRS for part in parts) or parts[-1] in SKIP_FILES:
            raise WorkspaceError(f"Путь недоступен: {path}")
        return "/".join(parts)

    def writable(self, rel: str) -> bool:
        return rel.startswith("docs/") and rel.endswith(".md") or ("/" not in rel and rel.endswith(".md"))

    def _drafted(self) -> list[str]:
        if not self.draft.exists():
            return []
        return sorted(str(path.relative_to(self.draft)).replace(os.sep, "/") for path in self.draft.rglob("*") if path.is_file())

    def files(self) -> list[str]:
        found = set()
        for folder, dirs, names in os.walk(self.root):
            dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS and not name.startswith("."))
            base = Path(folder).relative_to(self.root)
            for name in names:
                if name in SKIP_FILES or Path(name).suffix not in TEXT:
                    continue
                found.add(str(base / name).replace(os.sep, "/").removeprefix("./"))
        return sorted(found | set(self._drafted()))

    def text(self, rel: str) -> str | None:
        """Версия с учётом черновика: сначала черновик, потом проект."""
        for base in (self.draft, self.root):
            path = base / rel
            if path.is_file():
                try:
                    return path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    return None
        return None

    def original(self, rel: str) -> str | None:
        path = self.root / rel
        return path.read_text(encoding="utf-8") if path.is_file() else None

    # ── чтение ───────────────────────────────────────────────────────────

    def list(self, path: str = "", pattern: str = "", limit: int = 200) -> dict[str, Any]:
        prefix = self.rel(path, folder=True)
        files = [name for name in self.files() if not prefix or name == prefix or name.startswith(prefix + "/")]
        if pattern:
            files = [name for name in files if fnmatch.fnmatch(PurePosixPath(name).name, pattern)]
        depth = len(PurePosixPath(prefix).parts) if prefix else 0
        folders: dict[str, int] = {}
        for name in files:
            parts = PurePosixPath(name).parts
            key = "/".join(parts[: depth + 1]) + ("/" if len(parts) > depth + 1 else "")
            folders[key] = folders.get(key, 0) + 1
        drafted = set(self._drafted())
        return {"path": prefix or ".", "total": len(files), "folders": folders,
                "files": files[:limit], "truncated": len(files) > limit, "in_draft": sorted(drafted & set(files))}

    def search(self, query: str, path: str = "", glob: str = "", regex: bool = False, limit: int = SEARCH_LIMIT) -> dict[str, Any]:
        if not query.strip():
            raise WorkspaceError("Пустой запрос")
        try:
            pattern = re.compile(query if regex else re.escape(query), re.IGNORECASE)
        except re.error as error:
            raise WorkspaceError(f"Регулярное выражение не разобрано: {error}") from error
        prefix = self.rel(path, folder=True)
        matches, files, total = [], set(), 0
        for name in self.files():
            if prefix and not (name == prefix or name.startswith(prefix + "/")):
                continue
            if glob and not fnmatch.fnmatch(PurePosixPath(name).name, glob) and not fnmatch.fnmatch(name, glob):
                continue
            text = self.text(name)
            if text is None:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    total += 1
                    files.add(name)
                    if len(matches) < limit:
                        matches.append({"file": name, "line": number, "text": line.strip()[:LINE_CHARS]})
        return {"query": query, "total": total, "files": len(files), "matches": matches, "truncated": total > len(matches)}

    def read(self, path: str, start: int = 1, end: int | None = None) -> dict[str, Any]:
        rel = self.rel(path)
        text = self.text(rel)
        if text is None:
            raise WorkspaceError(f"Нет такого текстового файла: {rel}")
        lines = text.splitlines()
        start = max(1, start)
        last = min(len(lines), end or start + READ_LINES - 1, start + READ_LINES - 1)
        # Кусок режется и по символам: агент получает не больше 6000 символов результата,
        # и обрезанный посередине файл модель начинает перечитывать по кругу.
        shown, size = [], 0
        for number in range(start, last + 1):
            row = f"{number:>4} {lines[number - 1]}"
            if shown and size + len(row) > READ_CHARS:
                break
            shown.append(row)
            size += len(row) + 1
        stop = start + len(shown) - 1
        result = {"path": rel, "total_lines": len(lines), "start": start, "end": stop, "in_draft": (self.draft / rel).is_file(), "text": "\n".join(shown)}
        if stop < len(lines):
            result["next_start"] = stop + 1
        return result

    # ── черновик ─────────────────────────────────────────────────────────

    def _check_writable(self, rel: str) -> None:
        if not self.writable(rel):
            raise WorkspaceError(f"Писать можно только в docs/*.md и в .md в корне проекта, а не в {rel}")

    def write(self, path: str, content: str) -> dict[str, Any]:
        rel = self.rel(path)
        self._check_writable(rel)
        target = self.draft / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
        return {"path": rel, "new": self.original(rel) is None, "lines": len(content.splitlines())}

    def edit(self, path: str, old: str, new: str) -> dict[str, Any]:
        rel = self.rel(path)
        self._check_writable(rel)
        text = self.text(rel)
        if text is None:
            raise WorkspaceError(f"Нет такого файла: {rel} — для нового файла есть write_file")
        count = text.count(old) if old else 0
        if count != 1:
            raise WorkspaceError(f"Фрагмент для замены должен встречаться ровно один раз, а встречается {count} раз — возьмите кусок длиннее")
        target = self.draft / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        return {"path": rel, "replaced": 1}

    def changes(self) -> list[dict[str, Any]]:
        result = []
        for rel in self._drafted():
            before = self.original(rel)
            after = (self.draft / rel).read_text(encoding="utf-8")
            if before == after:
                continue
            diff = "".join(difflib.unified_diff(
                (before or "").splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile="/dev/null" if before is None else f"a/{rel}", tofile=f"b/{rel}",
            ))
            added = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
            removed = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
            result.append({"path": rel, "new": before is None, "added": added, "removed": removed, "diff": diff})
        return result

    def apply(self, paths: list[str] | None = None) -> list[str]:
        """Черновик → проект. Только разрешённые пути — проверка ещё раз, на случай подложенного файла."""
        applied = []
        for rel in self._drafted():
            if paths is not None and rel not in paths:
                continue
            self._check_writable(self.rel(rel))
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.draft / rel, target)
            (self.draft / rel).unlink()
            applied.append(rel)
        return applied

    def discard(self) -> int:
        count = len(self._drafted())
        shutil.rmtree(self.draft, ignore_errors=True)
        return count


def build_server(workspace: Workspace) -> MCPServer:
    server = MCPServer(
        "advent-files",
        version="34",
        instructions=(
            "Файлы проекта AI Advent. Начинай с search или list_files, читай только нужные куски read_file — "
            "не открывай всё подряд. Правки — write_file или edit_file: они идут в черновик, человек увидит diff. "
            "Писать можно только в docs/*.md и .md в корне."
        ),
    )

    @server.tool(title="Файлы")
    def list_files(
        path: Folder = "",
        pattern: Annotated[str, Field(default="", max_length=100, description="Маска имени: *.py, day3*.html; пусто — все")] = "",
        limit: Annotated[int, Field(ge=1, le=500, description="Сколько путей вернуть")] = 200,
    ) -> dict[str, Any]:
        """Текстовые файлы проекта по папке и маске имени — и сколько файлов в каждой подпапке. Файлы из черновика тоже видны."""
        return workspace.list(path, pattern, limit)

    @server.tool(title="Поиск по файлам")
    def search(
        query: Annotated[str, Field(min_length=1, max_length=200, description="Что искать: строка (без учёта регистра) или регулярное выражение")],
        path: Folder = "",
        glob: Annotated[str, Field(default="", max_length=100, description="Маска файлов: *.py, *.html, tests/*.py; пусто — все")] = "",
        regex: Annotated[bool, Field(description="true — query как регулярное выражение")] = False,
        limit: Annotated[int, Field(ge=1, le=200, description="Сколько совпадений вернуть")] = SEARCH_LIMIT,
    ) -> dict[str, Any]:
        """Поиск по многим файлам сразу: файл, номер строки и сама строка для каждого совпадения, плюс сколько всего совпадений и в скольких файлах."""
        return workspace.search(query, path, glob, regex, limit)

    @server.tool(title="Прочитать файл")
    def read_file(
        path: RelPath,
        start: Annotated[int, Field(ge=1, description="С какой строки")] = 1,
        end: Annotated[int | None, Field(ge=1, description="По какую строку включительно; не больше 400 строк за раз")] = None,
    ) -> dict[str, Any]:
        """Файл с номерами строк — целиком, если короткий, или кусок start–end (не больше ~5500 символов; next_start — откуда продолжить). Учитывает черновик."""
        return workspace.read(path, start, end)

    @server.tool(title="Записать файл")
    def write_file(
        path: RelPath,
        content: Annotated[str, Field(min_length=1, max_length=60000, description="Полный текст файла")],
    ) -> dict[str, Any]:
        """Новый файл или полная замена — в черновик, не в проект. Только docs/*.md и .md в корне."""
        return workspace.write(path, content)

    @server.tool(title="Поправить файл")
    def edit_file(
        path: RelPath,
        old: Annotated[str, Field(min_length=1, max_length=20000, description="Фрагмент, который встречается в файле ровно один раз")],
        new: Annotated[str, Field(max_length=20000, description="Чем его заменить")],
    ) -> dict[str, Any]:
        """Точечная правка: заменить фрагмент, который встречается ровно один раз. В черновик; только docs/*.md и .md в корне."""
        return workspace.edit(path, old, new)

    @server.tool(title="Черновик")
    def draft_diff() -> dict[str, Any]:
        """Что уже подготовлено в черновике: файлы, сколько строк добавлено и удалено, unified diff."""
        changes = workspace.changes()
        return {"files": [{key: item[key] for key in ("path", "new", "added", "removed")} for item in changes],
                "diff": "".join(item["diff"] for item in changes)[:12000]}

    return server


def workspace() -> Workspace:
    return Workspace(os.getenv("ADVENT_FILES_ROOT") or DEFAULT_ROOT, os.getenv("ADVENT_FILES_DRAFT") or DEFAULT_DRAFT)


def main() -> None:
    build_server(workspace()).run()


if __name__ == "__main__":
    main()

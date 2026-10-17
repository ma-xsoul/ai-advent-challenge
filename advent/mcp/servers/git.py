"""
MCP-сервер git нашего проекта — день 31.

Пять инструментов, все только читают — в репозитории ничего не меняют:

- git_branch — текущая ветка, последний коммит, есть ли незакоммиченные
  изменения и на сколько коммитов ветка ушла от main;
- git_status — какие файлы изменены, добавлены, удалены, ещё не в git;
- git_diff — сами изменения: незакоммиченные или всей ветки от main,
  по всем файлам или по одному; длинный diff обрезается;
- git_log — последние коммиты: хэш, дата, сообщение;
- list_files — файлы под git по папке и маске — структура проекта.

git зовётся процессом со списком аргументов, без оболочки; пути из
аргументов идут после «--», поэтому имя файла не может стать флагом.
Корень — ADVENT_GIT_ROOT или корень проекта.

Запуск: python -m advent.mcp.servers.git (stdio).
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field


DEFAULT_ROOT = Path(__file__).resolve().parents[3]
BASE = "main"
TIMEOUT_SECONDS = 10
DIFF_CHARS = 12000

STATES = {"M": "изменён", "A": "добавлен", "D": "удалён", "R": "переименован", "C": "скопирован", "?": "не в git", "U": "конфликт"}

Path_ = Annotated[str, Field(default="", max_length=300, description="Путь внутри проекта, например advent/mcp или README.md; пусто — весь проект")]


class GitError(ToolError):
    """git не ответил. Наследник ToolError — текст дойдёт до модели как ошибка инструмента."""


class Repo:
    """Вызовы git в одной папке. В тестах — временный репозиторий."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def run(self, *args: str, check: bool = True) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), *args],
                capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GitError(f"git не запустился: {error}") from error
        if check and result.returncode != 0:
            raise GitError(f"git {args[0]}: {result.stderr.strip() or result.returncode}")
        return result.stdout

    def has(self, ref: str) -> bool:
        return self.run("rev-parse", "--verify", "--quiet", ref, check=False).strip() != ""


def safe_path(path: str) -> str:
    """Путь внутри проекта: без выхода наверх и без абсолютных путей."""
    path = path.strip()
    if path.startswith(("-", "/", "~")) or ".." in Path(path).parts:
        raise GitError(f"Недопустимый путь: {path}")
    return path.rstrip("/")


def status(repo: Repo) -> list[dict[str, Any]]:
    files = []
    for line in repo.run("status", "--porcelain=v1", "--untracked-files=all").splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        letter = code.strip()[:1] or "M"
        files.append({"path": path.strip('"'), "state": STATES.get(letter, code.strip()), "staged": code[0] not in (" ", "?")})
    return files


def numstat(repo: Repo, *args: str) -> list[dict[str, Any]]:
    files = []
    for line in repo.run("diff", "--numstat", *args).splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            added, removed, path = parts
            files.append({"path": path, "added": int(added) if added.isdigit() else None, "removed": int(removed) if removed.isdigit() else None})
    return files


def build_server(repo: Repo) -> MCPServer:
    server = MCPServer(
        "advent-git",
        version="31",
        instructions=(
            "Инструменты про git-репозиторий нашего проекта, только чтение. "
            "Над чем сейчас идёт работа — git_branch и git_status; сами изменения — git_diff; "
            "история — git_log; какие файлы и папки есть — list_files. "
            "Про устройство кода и документацию спрашивай поиск по базе, а не git."
        ),
    )

    @server.tool(title="Текущая ветка")
    def git_branch() -> dict[str, Any]:
        """Текущая git-ветка проекта, последний коммит, есть ли незакоммиченные изменения и на сколько коммитов ветка ушла от main."""
        branch = repo.run("rev-parse", "--abbrev-ref", "HEAD").strip()
        head = repo.run("log", "-1", "--date=short", "--pretty=format:%h\x1f%ad\x1f%s").split("\x1f")
        changed = status(repo)
        result: dict[str, Any] = {
            "branch": branch,
            "head": {"hash": head[0], "date": head[1], "subject": head[2]} if len(head) == 3 else None,
            "uncommitted_files": len(changed),
        }
        if branch != BASE and repo.has(BASE):
            ahead, behind = repo.run("rev-list", "--left-right", "--count", f"HEAD...{BASE}").split()
            result.update({"base": BASE, "ahead_of_base": int(ahead), "behind_base": int(behind)})
        return result

    @server.tool(title="Изменённые файлы")
    def git_status() -> dict[str, Any]:
        """Незакоммиченные изменения рабочей копии: какие файлы изменены, добавлены, удалены или ещё не в git."""
        files = status(repo)
        return {"branch": repo.run("rev-parse", "--abbrev-ref", "HEAD").strip(), "files": files, "count": len(files)}

    @server.tool(title="Diff")
    def git_diff(
        scope: Annotated[Literal["working", "branch"], Field(description="working — незакоммиченные изменения; branch — вся ветка относительно main")] = "working",
        path: Path_ = "",
    ) -> dict[str, Any]:
        """Изменения в коде: незакоммиченные (working) или всей текущей ветки относительно main (branch), по всему проекту или по одному пути. Сначала список файлов с числом строк, потом сам diff — длинный обрезается."""
        target = safe_path(path)
        if scope == "branch":
            if not repo.has(BASE):
                raise GitError(f"Нет ветки {BASE} — сравнивать не с чем")
            args = [f"{BASE}...HEAD"]
        else:
            args = ["HEAD"]
        tail = ["--", target] if target else []
        files = numstat(repo, *args, *tail)
        text = repo.run("diff", *args, *tail)
        untracked = [item["path"] for item in status(repo) if item["state"] == "не в git" and item["path"].startswith(target)] if scope == "working" else []
        return {
            "scope": scope,
            "base": BASE if scope == "branch" else "HEAD",
            "files": files,
            "untracked": untracked,
            "diff": text[:DIFF_CHARS],
            "truncated": len(text) > DIFF_CHARS,
        }

    @server.tool(title="История")
    def git_log(
        limit: Annotated[int, Field(ge=1, le=50, description="Сколько последних коммитов, от 1 до 50")] = 10,
        path: Path_ = "",
    ) -> dict[str, Any]:
        """Последние коммиты — всего проекта или одного пути: короткий хэш, дата, сообщение."""
        target = safe_path(path)
        raw = repo.run("log", f"-{limit}", "--date=short", "--pretty=format:%h\x1f%ad\x1f%s", *(["--", target] if target else []))
        commits = [dict(zip(("hash", "date", "subject"), line.split("\x1f"))) for line in raw.splitlines() if line]
        return {"commits": commits}

    @server.tool(title="Файлы проекта")
    def list_files(
        path: Path_ = "",
        pattern: Annotated[str, Field(default="", max_length=100, description="Маска имени, например *.py или day3*; пусто — все")] = "",
        limit: Annotated[int, Field(ge=1, le=500, description="Сколько путей вернуть, от 1 до 500")] = 200,
    ) -> dict[str, Any]:
        """Файлы проекта под git — по папке и маске имени. Плюс сколько файлов в каждой подпапке: так видна структура."""
        target = safe_path(path)
        files = repo.run("ls-files", *(["--", target] if target else [])).splitlines()
        if pattern:
            files = [name for name in files if fnmatch.fnmatch(Path(name).name, pattern)]
        prefix = len(Path(target).parts) if target else 0
        folders: dict[str, int] = {}
        for name in files:
            parts = Path(name).parts
            key = "/".join(parts[: prefix + 1]) + ("/" if len(parts) > prefix + 1 else "")
            folders[key] = folders.get(key, 0) + 1
        return {"path": target or ".", "total": len(files), "folders": folders, "files": files[:limit], "truncated": len(files) > limit}

    return server


def root() -> Path:
    return Path(os.getenv("ADVENT_GIT_ROOT") or DEFAULT_ROOT)


def main() -> None:
    build_server(Repo(root())).run()


if __name__ == "__main__":
    main()

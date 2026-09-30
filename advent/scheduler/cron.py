"""
Строка в crontab пользователя — расписание дня 18.

Лаборатория ставит в crontab одну строку и помечает её маркером в конце:
по нему строка находится, заменяется при повторной установке и удаляется.
Чужие строки crontab не трогаются. Запись в crontab — только по явной
команде `python -m advent.scheduler install`: это системная настройка
пользователя, страница её лишь показывает.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..mcp.client import PROJECT_ROOT


MARKER = "# advent-day18"

Runner = Callable[..., subprocess.CompletedProcess]


def cron_line(every: int, summary_every: int, log_path: Path) -> str:
    python = shlex.quote(sys.executable)
    root = shlex.quote(str(PROJECT_ROOT))
    log = shlex.quote(str(log_path))
    return (
        f"*/{every} * * * * cd {root} && {python} -m advent.scheduler tick "
        f"--summary-every {summary_every} >> {log} 2>&1 {MARKER}"
    )


def read(run: Runner = subprocess.run) -> str:
    """Текущий crontab. «no crontab for …» — это пустой crontab, а не ошибка."""
    result = run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        if "no crontab" in (result.stderr or ""):
            return ""
        raise RuntimeError(f"crontab -l: {result.stderr.strip() or result.returncode}")
    return result.stdout


def write(text: str, run: Runner = subprocess.run) -> None:
    result = run(["crontab", "-"], input=text, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"crontab: {result.stderr.strip() or result.returncode}")


def without_ours(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.rstrip().endswith(MARKER)]


def install(every: int, summary_every: int, log_path: Path, run: Runner = subprocess.run) -> str:
    """Поставить (или заменить) нашу строку. Возвращает её."""
    line = cron_line(every, summary_every, log_path)
    lines = [*without_ours(read(run)), line]
    write("\n".join(lines) + "\n", run)
    return line


def uninstall(run: Runner = subprocess.run) -> bool:
    """Убрать нашу строку. False — её и не было."""
    current = read(run)
    lines = without_ours(current)
    if len(lines) == len(current.splitlines()):
        return False
    write("\n".join(lines) + ("\n" if lines else ""), run)
    return True


def status(run: Runner = subprocess.run) -> dict[str, Any]:
    """Что стоит в crontab сейчас — только чтение."""
    try:
        current = read(run)
    except (OSError, RuntimeError) as error:
        return {"installed": False, "line": None, "every": None, "summary_every": None, "error": str(error)}
    line = next((line for line in current.splitlines() if line.rstrip().endswith(MARKER)), None)
    every = summary_every = None
    if line:
        head = line.split()[0]
        every = int(head[2:]) if head.startswith("*/") and head[2:].isdigit() else None
        parts = line.split()
        if "--summary-every" in parts:
            value = parts[parts.index("--summary-every") + 1]
            summary_every = int(value) if value.isdigit() else None
    return {"installed": line is not None, "line": line, "every": every, "summary_every": summary_every, "error": None}

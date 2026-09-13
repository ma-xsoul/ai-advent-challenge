"""
Хранилище стека на диске: один JSON-файл на сессию.

Агент о диске не знает — записью и чтением занимается реестр. Это держит
агента однозадачным и проверяемым, а формат хранения — заменяемым: сегодня
JSON, завтра SQLite, агент не заметит разницы.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent import Agent


STORE_FORMAT_VERSION = 1

# Имя файла берётся из идентификатора сессии, поэтому он не должен
# содержать ничего, кроме безопасных символов: "../" тут недопустимо.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class StoreError(RuntimeError):
    """Файл сессии не удалось прочитать или он повреждён."""


@dataclass(frozen=True)
class StoredSession:
    """Что лежит на диске по сессии — для дебаг-панели."""

    path: str
    file: str
    saved_at: str | None
    messages: int
    bytes: int


class JsonStore:
    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)

    @property
    def directory(self) -> Path:
        return self._directory

    def save(self, session_id: str, agent: Agent) -> Path:
        """Пишет слепок агента. Атомарно: сначала во временный файл, потом замена."""
        path = self._path(session_id)
        self._directory.mkdir(parents=True, exist_ok=True)

        data = {
            "version": STORE_FORMAT_VERSION,
            "session_id": session_id,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **agent.snapshot(),
        }

        # Упавший посреди записи процесс не должен оставить полфайла:
        # os.replace на одной файловой системе атомарен.
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, path)
        return path

    def load(self, session_id: str) -> dict[str, Any] | None:
        """Слепок сессии или None, если файла нет. Битый файл — StoreError."""
        path = self._path(session_id)
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise StoreError(f"Не удалось прочитать {path.name}: {error}") from error

        if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
            raise StoreError(f"Файл {path.name} не похож на сохранённую сессию агента")
        return data

    def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def describe(self, session_id: str) -> StoredSession | None:
        """Сведения о файле без разбора всего стека — для дебаг-панели."""
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            data = self.load(session_id)
        except StoreError:
            return StoredSession(path=str(path), file=path.name, saved_at=None, messages=0, bytes=path.stat().st_size)
        return StoredSession(
            path=str(path),
            file=path.name,
            saved_at=data.get("saved_at") if isinstance(data.get("saved_at"), str) else None,
            messages=len(data.get("messages", [])),
            bytes=path.stat().st_size,
        )

    def _path(self, session_id: str) -> Path:
        if not _SAFE_ID.match(session_id):
            raise StoreError("Недопустимый идентификатор сессии")
        return self._directory / f"{session_id}.json"

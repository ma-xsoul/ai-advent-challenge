"""
Ветки диалога — день 10, Branching.

Диалог — не лента, а дерево: checkpoint фиксирует точку в истории,
ветка — отдельный агент, выросший из слепка этой точки. Само дерево —
маленький индекс рядом с файлами агентов: какие ветки есть, откуда
выросли и какая сейчас активна. Агенты про дерево не знают.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAIN_BRANCH = "main"

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@dataclass(frozen=True)
class Branch:
    id: str
    title: str
    parent: str | None  # id ветки-родителя; у основной — None
    forked_at: int  # сколько сообщений родителя вошло в ветку
    created_at: str


@dataclass(frozen=True)
class Checkpoint:
    id: str
    branch: str
    message: int  # длина стека ветки в момент checkpoint — всегда чётная
    title: str
    created_at: str


@dataclass
class DialogTree:
    """Ветки, checkpoint'ы, активная ветка и таблица прогонов одной сессии."""

    active: str = MAIN_BRANCH
    branches: list[Branch] = field(default_factory=list)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    # Строки сравнения стратегий: что показала каждая на сценарии.
    runs: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def fresh(cls, runs: list[dict[str, Any]] | None = None) -> DialogTree:
        return cls(
            branches=[Branch(MAIN_BRANCH, "Основная", None, 0, _now())],
            runs=list(runs or []),
        )

    def branch(self, branch_id: str) -> Branch | None:
        return next((item for item in self.branches if item.id == branch_id), None)

    def checkpoint(self, checkpoint_id: str) -> Checkpoint | None:
        return next((item for item in self.checkpoints if item.id == checkpoint_id), None)

    def add_checkpoint(self, branch_id: str, message: int, title: str | None = None) -> Checkpoint:
        if self.branch(branch_id) is None:
            raise ValueError("Нет такой ветки")
        if message < 0 or message % 2:
            raise ValueError("Checkpoint ставится после ответа агента")
        item = Checkpoint(
            id=f"c{len(self.checkpoints) + 1}",
            branch=branch_id,
            message=message,
            title=title or f"после {message} сообщений",
            created_at=_now(),
        )
        self.checkpoints.append(item)
        return item

    def add_branch(self, checkpoint_id: str, title: str | None = None) -> Branch:
        """Новая ветка от checkpoint. Агента для неё создаёт реестр — дерево лишь запоминает."""
        point = self.checkpoint(checkpoint_id)
        if point is None:
            raise ValueError("Нет такого checkpoint")
        number = len(self.branches)  # основная — нулевая, ветки — b1, b2, …
        item = Branch(
            id=f"b{number}",
            title=title or f"Ветка {number}",
            parent=point.branch,
            forked_at=point.message,
            created_at=_now(),
        )
        self.branches.append(item)
        return item

    def switch(self, branch_id: str) -> Branch:
        item = self.branch(branch_id)
        if item is None:
            raise ValueError("Нет такой ветки")
        self.active = branch_id
        return item

    def as_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "branches": [asdict(item) for item in self.branches],
            "checkpoints": [asdict(item) for item in self.checkpoints],
            "runs": list(self.runs),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DialogTree:
        branches = [
            Branch(
                id=str(item["id"]),
                title=str(item.get("title") or item["id"]),
                parent=item.get("parent"),
                forked_at=int(item.get("forked_at", 0) or 0),
                created_at=str(item.get("created_at") or ""),
            )
            for item in data.get("branches", [])
            if isinstance(item, dict) and item.get("id")
        ]
        checkpoints = [
            Checkpoint(
                id=str(item["id"]),
                branch=str(item.get("branch") or MAIN_BRANCH),
                message=int(item.get("message", 0) or 0),
                title=str(item.get("title") or item["id"]),
                created_at=str(item.get("created_at") or ""),
            )
            for item in data.get("checkpoints", [])
            if isinstance(item, dict) and item.get("id")
        ]
        tree = cls(
            active=str(data.get("active") or MAIN_BRANCH),
            branches=branches or [Branch(MAIN_BRANCH, "Основная", None, 0, _now())],
            checkpoints=checkpoints,
            runs=[item for item in data.get("runs", []) if isinstance(item, dict)],
        )
        if tree.branch(tree.active) is None:
            tree.active = tree.branches[0].id
        return tree


class TreeStore:
    """Индекс дерева: один JSON-файл на сессию, рядом с файлами агентов."""

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)

    def load(self, session_id: str) -> DialogTree:
        path = self._path(session_id)
        if not path.exists():
            return DialogTree.fresh()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return DialogTree.fresh()
        return DialogTree.from_dict(data) if isinstance(data, dict) else DialogTree.fresh()

    def save(self, session_id: str, tree: DialogTree) -> Path:
        path = self._path(session_id)
        self._directory.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(tree.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, path)
        return path

    def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def _path(self, session_id: str) -> Path:
        if not _SAFE_ID.match(session_id):
            raise ValueError("Недопустимый идентификатор сессии")
        return self._directory / f"{session_id}-tree.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

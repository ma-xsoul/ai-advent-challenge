"""
«Входящие» дня 18 — куда агент кладёт сводки, и журнал тиков расписания.

Это канал доставки, а не данные MCP-сервера: снимки курса живут в базе
сервера (rates.sqlite3), а здесь — только то, что агент прислал
пользователю, и отметки «тик был, курс снят / не снят».
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class Inbox:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    trigger TEXT NOT NULL,
                    minutes INTEGER NOT NULL,
                    ok INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    stats TEXT NOT NULL,
                    calls TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ticks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    trigger TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    price REAL,
                    error TEXT,
                    ms INTEGER NOT NULL,
                    summary_id INTEGER
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def add_summary(
        self, *, ts: int, trigger: str, minutes: int, ok: bool, text: str, stats: dict[str, Any], calls: list[dict[str, Any]]
    ) -> dict[str, Any]:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO summaries (ts, trigger, minutes, ok, text, stats, calls) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, trigger, minutes, int(ok), text, json.dumps(stats, ensure_ascii=False), json.dumps(calls, ensure_ascii=False)),
            )
            summary_id = cursor.lastrowid
        return self.summary(summary_id)

    def summary(self, summary_id: int) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM summaries WHERE id = ?", (summary_id,)).fetchone()
        return _summary(row)

    def summaries(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM summaries ORDER BY ts DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [_summary(row) for row in rows]

    def last_summary_ts(self) -> int | None:
        with self._connect() as db:
            row = db.execute("SELECT MAX(ts) FROM summaries").fetchone()
        return row[0]

    def clear_summaries(self) -> int:
        with self._connect() as db:
            return db.execute("DELETE FROM summaries").rowcount

    def clear(self) -> dict[str, int]:
        """Всё: и сводки, и журнал тиков — для кнопки «сбросить всё»."""
        with self._connect() as db:
            summaries = db.execute("DELETE FROM summaries").rowcount
            ticks = db.execute("DELETE FROM ticks").rowcount
        return {"summaries": summaries, "ticks": ticks}

    def add_tick(
        self, *, ts: int, trigger: str, ok: bool, price: float | None, error: str | None, ms: int, summary_id: int | None = None
    ) -> dict[str, Any]:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO ticks (ts, trigger, ok, price, error, ms, summary_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, trigger, int(ok), price, error, ms, summary_id),
            )
            tick_id = cursor.lastrowid
        return {"id": tick_id, "ts": ts, "trigger": trigger, "ok": ok, "price": price, "error": error, "ms": ms, "summary_id": summary_id}

    def link_summary(self, tick_id: int, summary_id: int) -> None:
        with self._connect() as db:
            db.execute("UPDATE ticks SET summary_id = ? WHERE id = ?", (summary_id, tick_id))

    def ticks(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM ticks ORDER BY ts DESC, id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "ok": bool(row["ok"])} for row in rows]

    def first_tick_ts(self) -> int | None:
        with self._connect() as db:
            row = db.execute("SELECT MIN(ts) FROM ticks").fetchone()
        return row[0]


def _summary(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["ok"] = bool(data["ok"])
    data["stats"] = json.loads(data["stats"])
    data["calls"] = json.loads(data["calls"])
    return data

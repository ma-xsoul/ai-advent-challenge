"""
MCP-сервер курса доллара к рублю — день 18.

Три инструмента:

- collect_rate — снять курс сейчас и сохранить снимок в SQLite. Его на
  каждом тике дёргает cron — без модели, напрямую через MCP;
- rate_summary — агрегат за последние N минут: первое и последнее
  значение, изменение, минимум и максимум со временем, среднее и отрыв
  рынка от официального курса ЦБ. Из него модель пишет сводку;
- rate_history — сами снимки за период, прореженные до нужного числа точек.

Рынок — Yahoo Finance (RUB=X, раз в минуту, без ключа, API неофициальный).
Официальный курс ЦБ — cbr-xml-daily.ru, раз в день; кэшируется в той же
базе, чтобы не спрашивать его на каждом тике.

Данные копятся между запусками в SQLite — сервер живёт один тик, база
живёт всегда. Путь — ADVENT_RATES_DB или data/day18/rates.sqlite3.

Запуск: python -m advent.mcp.servers.rates (stdio).
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import requests
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field


MARKET_URL = "https://query1.finance.yahoo.com/v8/finance/chart/RUB=X"
OFFICIAL_URL = "https://www.cbr-xml-daily.ru/daily_json.js"
TIMEOUT_SECONDS = 15

DEFAULT_DB = Path(__file__).resolve().parents[3] / "data" / "day18" / "rates.sqlite3"

Minutes = Annotated[int, Field(ge=1, le=1440, description="За сколько последних минут, от 1 до 1440 (сутки)")]


class RateError(ToolError):
    """Курс не получен. Наследник ToolError — текст дойдёт до модели как ошибка инструмента."""


class RateSource:
    """Откуда берётся курс. В тестах подменяется заглушкой."""

    def __init__(self, *, timeout: int = TIMEOUT_SECONDS) -> None:
        self._timeout = timeout

    def market(self) -> tuple[float, int]:
        """Рыночный курс и время котировки (unix-секунды)."""
        data = self._get(MARKET_URL, {"interval": "1m", "range": "1d"}, headers={"User-Agent": "Mozilla/5.0"})
        try:
            meta = data["chart"]["result"][0]["meta"]
            return float(meta["regularMarketPrice"]), int(meta["regularMarketTime"])
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise RateError(f"Yahoo Finance вернул неожиданный ответ: {error}") from error

    def official(self) -> tuple[str, float]:
        """Официальный курс ЦБ: дата, на которую он установлен, и значение."""
        data = self._get(OFFICIAL_URL)
        try:
            return str(data["Date"])[:10], float(data["Valute"]["USD"]["Value"])
        except (KeyError, TypeError, ValueError) as error:
            raise RateError(f"ЦБ вернул неожиданный ответ: {error}") from error

    def _get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
        try:
            response = requests.get(url, params=params, headers=headers, timeout=self._timeout)
        except requests.RequestException as error:
            raise RateError(f"Нет связи с источником курса: {error}") from error
        if not response.ok:
            raise RateError(f"Источник курса ответил {response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise RateError("Источник курса ответил не JSON") from error


class RatesStore:
    """SQLite со снимками курса и кэшем официального курса."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    price REAL NOT NULL,
                    market_ts INTEGER,
                    source TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS snapshots_ts ON snapshots (ts);
                CREATE TABLE IF NOT EXISTS official (
                    date TEXT PRIMARY KEY,
                    value REAL NOT NULL,
                    fetched_ts INTEGER NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def add(self, ts: int, price: float, market_ts: int | None, source: str) -> dict[str, Any]:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO snapshots (ts, price, market_ts, source) VALUES (?, ?, ?, ?)",
                (ts, price, market_ts, source),
            )
            return {"id": cursor.lastrowid, "ts": ts, "price": price, "market_ts": market_ts, "source": source}

    def since(self, ts: int) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT id, ts, price, market_ts, source FROM snapshots WHERE ts >= ? ORDER BY ts", (ts,))
            return [dict(row) for row in rows]

    def count(self) -> int:
        with self._connect() as db:
            return db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]

    def last(self) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT id, ts, price, market_ts, source FROM snapshots ORDER BY ts DESC, id DESC LIMIT 1").fetchone()
            return dict(row) if row else None

    def official(self) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT date, value, fetched_ts FROM official ORDER BY date DESC LIMIT 1").fetchone()
            return dict(row) if row else None

    def clear(self) -> int:
        """Снести снимки и кэш официального курса. Возвращает, сколько снимков было."""
        with self._connect() as db:
            removed = db.execute("DELETE FROM snapshots").rowcount
            db.execute("DELETE FROM official")
        return removed

    def save_official(self, date: str, value: float, fetched_ts: int) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO official (date, value, fetched_ts) VALUES (?, ?, ?) "
                "ON CONFLICT(date) DO UPDATE SET value = excluded.value, fetched_ts = excluded.fetched_ts",
                (date, value, fetched_ts),
            )


def clock(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def aggregate(points: list[dict[str, Any]], minutes: int, official: dict[str, Any] | None) -> dict[str, Any]:
    """Итог за период — то, из чего модель пишет сводку. Чистая функция, без базы и сети."""
    official_part = {"date": official["date"], "value": official["value"]} if official else None
    if not points:
        return {
            "period_minutes": minutes,
            "points": 0,
            "official": official_part,
            "note": "За период нет ни одного снимка — расписание не срабатывало.",
        }

    prices = [point["price"] for point in points]
    first, last = points[0], points[-1]
    low = min(points, key=lambda point: point["price"])
    high = max(points, key=lambda point: point["price"])
    change = last["price"] - first["price"]
    result: dict[str, Any] = {
        "period_minutes": minutes,
        "points": len(points),
        "from": clock(first["ts"]),
        "to": clock(last["ts"]),
        "first": round(first["price"], 4),
        "last": round(last["price"], 4),
        "change": round(change, 4),
        "change_pct": round(change / first["price"] * 100, 3) if first["price"] else None,
        "min": {"price": round(low["price"], 4), "at": clock(low["ts"])},
        "max": {"price": round(high["price"], 4), "at": clock(high["ts"])},
        "average": round(sum(prices) / len(prices), 4),
        "official": official_part,
    }
    if official_part:
        result["last_vs_official"] = round(last["price"] - official_part["value"], 4)
    return result


def thin(points: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Не больше limit точек, равномерно по периоду; первая и последняя — всегда."""
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    return [points[round(index * step)] for index in range(limit)]


def build_server(
    store: RatesStore,
    source: RateSource,
    *,
    now: Callable[[], float] = time.time,
) -> MCPServer:
    server = MCPServer(
        "advent-rates",
        version="18",
        instructions=(
            "Инструменты про курс доллара к рублю. Снимки копит расписание через collect_rate; "
            "для сводки бери rate_summary за нужный период, rate_history — только если нужны подробности. "
            "Описывай движение курса цифрами, без советов покупать или продавать."
        ),
    )

    def official_today() -> dict[str, Any] | None:
        """Официальный курс из кэша; раз в сутки — свежий от ЦБ. Сбой ЦБ снимок не срывает."""
        cached = store.official()
        today = datetime.fromtimestamp(now()).strftime("%Y-%m-%d")
        if cached and datetime.fromtimestamp(cached["fetched_ts"]).strftime("%Y-%m-%d") == today:
            return cached
        try:
            date, value = source.official()
        except RateError:
            return cached
        store.save_official(date, value, int(now()))
        return store.official()

    @server.tool(title="Снять курс")
    def collect_rate() -> dict[str, Any]:
        """Снять текущий рыночный курс доллара к рублю и сохранить снимок в базу. Его вызывает расписание на каждом тике."""
        price, market_ts = source.market()
        snapshot = store.add(int(now()), price, market_ts, "yahoo:RUB=X")
        official = official_today()
        return {
            "saved": {"at": clock(snapshot["ts"]), "price": round(price, 4), "market_time": clock(market_ts)},
            "snapshots_total": store.count(),
            "official": {"date": official["date"], "value": official["value"]} if official else None,
        }

    @server.tool(title="Сводка за период")
    def rate_summary(minutes: Minutes = 60) -> dict[str, Any]:
        """Итог по сохранённым снимкам за последние N минут: первое и последнее значение, изменение в рублях и процентах, минимум и максимум со временем, среднее и отрыв от официального курса ЦБ."""
        points = store.since(int(now()) - minutes * 60)
        return aggregate(points, minutes, store.official())

    @server.tool(title="История курса")
    def rate_history(
        minutes: Minutes = 60,
        limit: Annotated[int, Field(ge=2, le=60, description="Сколько точек вернуть, от 2 до 60 — лишние прорежаются")] = 20,
    ) -> dict[str, Any]:
        """Снимки курса за последние N минут — время и цена, прореженные до limit точек. Для подробностей, а не для сводки."""
        points = thin(store.since(int(now()) - minutes * 60), limit)
        return {"period_minutes": minutes, "points": [{"at": clock(point["ts"]), "price": round(point["price"], 4)} for point in points]}

    return server


def db_path() -> Path:
    return Path(os.getenv("ADVENT_RATES_DB") or DEFAULT_DB)


def main() -> None:
    build_server(RatesStore(db_path()), RateSource()).run()


if __name__ == "__main__":
    main()

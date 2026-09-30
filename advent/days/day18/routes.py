"""
День 18: планировщик и фоновые задачи — агент, который сам присылает сводку.

Работает всё без страницы: cron раз в несколько минут запускает
`python -m advent.scheduler tick` — тот снимает курс через MCP-инструмент
и, когда подошло время, зовёт агента с моделью за сводкой. Сводка ложится
во «Входящие» (data/day18/inbox.sqlite3), снимки — в базу MCP-сервера
(data/day18/rates.sqlite3).

Страница только показывает: что стоит в crontab, когда был тик, график
курса и входящие — и опрашивает состояние, чтобы новая сводка появилась
сама. Кнопки «снять сейчас» и «сводка сейчас» делают то же, что тик,
но вручную. crontab страница не меняет — это команда в терминале.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, render_template, request

from ...mcp import RATES_SERVER
from ...mcp.servers.rates import RatesStore
from ...navigation import get_day
from ...scheduler import DATA_DIR, SUMMARY_EVERY, TICK_EVERY, Paths, collect, cron, summarize
from ...scheduler.inbox import Inbox


bp = Blueprint("day18", __name__, url_prefix="/days/18")

CHART_MINUTES = 60
MAX_SUMMARY_MINUTES = 1440


def _dir() -> Path:
    return Path(current_app.config.get("DAY18_DIR") or DATA_DIR)


def _inbox() -> Inbox:
    return Inbox(Paths.under(_dir()).inbox)


def _rates() -> RatesStore:
    return RatesStore(_dir() / "rates.sqlite3")


def _runner():
    # Тесты подставляют свой crontab — настоящий трогать нельзя.
    return current_app.config.get("CRONTAB_RUNNER") or subprocess.run


def _factory():
    return current_app.config.get("MCP_TRANSPORT_FACTORY")


def _state() -> dict[str, Any]:
    inbox, rates = _inbox(), _rates()
    now = int(time.time())
    schedule = cron.status(_runner())
    summary_every = schedule["summary_every"] or SUMMARY_EVERY
    anchor = inbox.last_summary_ts() or inbox.first_tick_ts()
    return {
        "now": now,
        "cron": {
            **schedule,
            "install": f".venv/bin/python -m advent.scheduler install --every {TICK_EVERY} --summary-every {SUMMARY_EVERY}",
            "uninstall": ".venv/bin/python -m advent.scheduler uninstall",
        },
        "summary_every": summary_every,
        "next_summary_ts": anchor + summary_every * 60 if anchor and schedule["installed"] else None,
        "chart": {
            "minutes": CHART_MINUTES,
            "points": [{"ts": point["ts"], "price": point["price"]} for point in rates.since(now - CHART_MINUTES * 60)],
            "official": rates.official(),
        },
        "snapshots_total": rates.count(),
        "ticks": inbox.ticks(10),
        "summaries": inbox.summaries(20),
    }


@bp.get("/")
def index():
    return render_template("days/day18.html", active_day=get_day(18), server=RATES_SERVER, state=_state())


@bp.get("/api/state")
def state():
    return jsonify(_state())


@bp.post("/api/collect")
def collect_now():
    """То же, что первый шаг тика: снять курс через MCP-инструмент, без модели."""
    record = collect(_inbox(), trigger="manual", factory=_factory())
    return jsonify({"tick": record, "state": _state()}), 200 if record["ok"] else 502


@bp.post("/api/summary")
def summary_now():
    """То же, что второй шаг тика: агент с моделью пишет сводку за период."""
    payload = request.get_json(silent=True)
    minutes = payload.get("minutes", SUMMARY_EVERY) if isinstance(payload, dict) else SUMMARY_EVERY
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= MAX_SUMMARY_MINUTES:
        return jsonify({"error": f"Период — целое число минут от 1 до {MAX_SUMMARY_MINUTES}"}), 400
    item = summarize(_inbox(), minutes=minutes, trigger="manual", factory=_factory(), transport=current_app.config.get("AGENT_TRANSPORT"))
    return jsonify({"summary": item, "state": _state()}), 200 if item["ok"] else 502


@bp.post("/api/inbox/clear")
def clear_inbox():
    removed = _inbox().clear_summaries()
    return jsonify({"removed": removed, "state": _state()})

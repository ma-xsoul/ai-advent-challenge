"""
Планировщик дня 18: то, что запускает cron.

У агента нет heartbeat — между ходами он не живёт и времени не видит.
Поэтому будит его не он сам, а системный cron: раз в несколько минут
запускается `python -m advent.scheduler tick`, и каждый такой запуск —
отдельный короткий процесс.

Тик делает две вещи:

1. снимает курс — вызывает MCP-инструмент collect_rate напрямую, без
   модели: решать тут нечего, и платить за запрос к модели каждые две
   минуты незачем;
2. если подошло время сводки — запускает агента с моделью (цикл дня 17):
   модель сама берёт у MCP-сервера итог за период и пишет сводку, а она
   ложится во «Входящие».
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..agent.config import AgentConfig
from ..agent.transport import DeepSeekTransport, Transport
from ..mcp.agent import ToolDialog, run_turn
from ..mcp.client import PROJECT_ROOT, RATES_SERVER, TransportFactory, call_server_tool
from .inbox import Inbox


DATA_DIR = PROJECT_ROOT / "data" / "day18"

# Шаг cron и период сводки по умолчанию — режим для видео: минута — самый
# частый шаг, какой умеет cron, и первая сводка приходит минуты через две
# после установки. Для жизни хватит тика раз в 5 минут и сводки раз в час:
# install --every 5 --summary-every 60.
TICK_EVERY = 1
SUMMARY_EVERY = 1

# cron срабатывает в начале минуты, но процесс стартует с задержкой —
# без запаса сводка «раз в 10 минут» съезжала бы на каждый шестой тик.
SLACK_SECONDS = 45

SUMMARY_CONFIG = AgentConfig(
    name="d18",
    system_prompt=(
        "Ты присылаешь пользователю регулярную сводку по курсу доллара к рублю. Данные бери только "
        "из инструментов MCP-сервера, не по памяти. Пиши по-русски, коротко, 3–5 строк: как менялся "
        "курс за период (изменение в рублях и процентах, минимум и максимум со временем) и как рынок "
        "соотносится с официальным курсом ЦБ. Без советов покупать или продавать и без прогнозов. "
        "Если снимков за период нет — так и скажи."
    ),
    temperature=0.3,
    max_tokens=500,
)


@dataclass
class Paths:
    inbox: Path

    @classmethod
    def under(cls, directory: str | Path) -> Paths:
        return cls(inbox=Path(directory) / "inbox.sqlite3")


def reset(directory: str | Path) -> dict[str, int]:
    """
    Чистый лист дня 18 для кнопки «сбросить всё»: входящие, журнал тиков,
    снимки курса и лог cron. Строку в crontab не трогает — это настройка
    системы, а не данные: если расписание включено, тики пойдут дальше.
    """
    directory = Path(directory)
    if not directory.exists():
        return {"summaries": 0, "ticks": 0, "snapshots": 0}

    from ..mcp.servers.rates import RatesStore

    cleared = Inbox(Paths.under(directory).inbox).clear()
    cleared["snapshots"] = RatesStore(directory / "rates.sqlite3").clear()
    log = directory / "cron.log"
    if log.exists():
        log.write_text("", encoding="utf-8")
    return cleared


def summary_due(inbox: Inbox, every_minutes: int, now: float) -> bool:
    """Пора ли сводке: с прошлой сводки (или с первого тика) прошёл период."""
    anchor = inbox.last_summary_ts() or inbox.first_tick_ts()
    return anchor is not None and now - anchor >= every_minutes * 60 - SLACK_SECONDS


def collect(
    inbox: Inbox,
    *,
    trigger: str,
    factory: TransportFactory | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Снять курс через MCP-инструмент — без модели — и отметить тик."""
    result = call_server_tool(RATES_SERVER, "collect_rate", transport_factory=factory)
    price = None
    if result.ok and isinstance(result.data, dict):
        price = (result.data.get("saved") or {}).get("price")
    return inbox.add_tick(ts=int(now()), trigger=trigger, ok=result.ok, price=price, error=result.error, ms=result.ms)


def summarize(
    inbox: Inbox,
    *,
    minutes: int,
    trigger: str,
    factory: TransportFactory | None = None,
    transport: Transport | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Агент с моделью пишет сводку за период; она ложится во «Входящие» — и удачная, и нет."""
    question = f"Сейчас {datetime.fromtimestamp(now()).strftime('%H:%M')}. Составь сводку по курсу доллара к рублю за последние {minutes} минут."
    events = run_turn(
        ToolDialog(),
        question,
        config=SUMMARY_CONFIG,
        transport=transport or DeepSeekTransport(),
        server=RATES_SERVER,
        factory=factory,
    )
    answer = next((event for event in events if event["type"] == "answer"), None)
    done = next((event for event in events if event["type"] == "done"), None)
    failure = next((event for event in events if event["type"] == "error"), None)
    calls = [
        {key: event[key] for key in ("name", "arguments", "ok", "ms", "chars", "preview")}
        for event in events
        if event["type"] == "tool_result"
    ]

    ok = answer is not None and failure is None
    text = answer["content"] if answer else f"Сводка не собрана: {failure['error'] if failure else 'нет ответа'}"
    return inbox.add_summary(
        ts=int(now()), trigger=trigger, minutes=minutes, ok=ok, text=text, stats=done["stats"] if done else {}, calls=calls
    )


def tick(
    inbox: Inbox,
    *,
    summary_every: int = SUMMARY_EVERY,
    trigger: str = "cron",
    factory: TransportFactory | None = None,
    transport: Transport | None = None,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Один запуск по расписанию: снять курс и, если пора, прислать сводку."""
    record = collect(inbox, trigger=trigger, factory=factory, now=now)
    summary = None
    if summary_due(inbox, summary_every, now()):
        summary = summarize(inbox, minutes=summary_every, trigger=trigger, factory=factory, transport=transport, now=now)
        inbox.link_summary(record["id"], summary["id"])
    return {"tick": record, "summary": summary}

"""
Контрольные вопросы для сравнения стратегий нарезки — день 21.

У каждого вопроса — файлы, в которых лежит ответ. Поиск хорош, если
кусок из нужного файла оказывается наверху выдачи. Так стратегии
сравниваются не «на глаз», а числами:

- hit@1 — доля вопросов, где первый же кусок из нужного файла;
- hit@5 — доля вопросов, где нужный файл есть среди первых пяти;
- MRR   — средняя обратная позиция первого попадания (1, 1/2, 1/3…):
          чем выше нужный кусок, тем ближе к 1.

Эти же вопросы станут основой десяти контрольных вопросов дня 22 —
там к ним добавится, что именно должно быть в ответе.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .chunking import STRATEGIES
from .embeddings import Embedder
from .index import RagIndex


@dataclass(frozen=True)
class Probe:
    question: str
    sources: tuple[str, ...]


PAGE = "advent/templates/days/day{:02d}.html"

# Источник — любой документ, где ответ действительно есть: и код, и
# страница дня, которая объясняет то же самое словами.
PROBES = (
    Probe("Как агент вызывает инструменты MCP и возвращает результат модели?", ("advent/mcp/agent.py", PAGE.format(17))),
    Probe("Как устроено расписание через cron и что делает тик?", ("advent/scheduler/__init__.py", "advent/scheduler/cron.py", PAGE.format(18))),
    Probe("Как старая часть истории сворачивается в конспект?", ("advent/agent/agent.py", "advent/agent/config.py", PAGE.format(9))),
    Probe("Какие слои памяти есть у агента и сколько живёт каждый?", ("advent/agent/memory.py", PAGE.format(11))),
    Probe("Какие переходы между этапами задачи разрешены и что такое guard?", ("advent/agent/task.py", PAGE.format(15))),
    Probe("Как ловятся нарушения инвариантов и кто выносит вердикт?", ("advent/agent/invariants.py", PAGE.format(14))),
    Probe("Как сервер курса доллара считает итог за период?", ("advent/mcp/servers/rates.py",)),
    Probe("Как проверяется, что данные дошли по цепочке инструментов без искажений?", ("advent/mcp/chain.py", PAGE.format(19))),
    Probe("Как оценить число токенов в тексте без токенизатора?", ("advent/agent/tokens.py",)),
    Probe(
        "Как агент решает, на какой MCP-сервер отправить вызов, если серверов несколько?",
        ("advent/mcp/agent.py", "advent/mcp/orchestration.py", PAGE.format(20)),
    ),
)


def iter_evaluate(
    index: RagIndex, embedder: Embedder, *, k: int = 5, probes: tuple[Probe, ...] = PROBES
) -> Iterator[dict[str, Any]]:
    """
    Прогон по шагам — для страницы, которая показывает вопросы по одному:
    probe_start → (probe_question → probe_result × стратегии)* → probe_summary.
    """
    totals = {strategy: {"hit1": 0, "hit5": 0, "rr": 0.0} for strategy in STRATEGIES}
    yield {"type": "probe_start", "total": len(probes), "k": k}
    for number, probe in enumerate(probes, start=1):
        yield {"type": "probe_question", "index": number, "question": probe.question, "sources": list(probe.sources)}
        for strategy in STRATEGIES:
            hits = index.search(probe.question, embedder, strategy, k=k)
            rank = next((hit.rank for hit in hits if hit.source in probe.sources), None)
            top = hits[0] if hits else None
            totals[strategy]["hit1"] += rank == 1
            totals[strategy]["hit5"] += rank is not None
            totals[strategy]["rr"] += 1 / rank if rank else 0.0
            yield {
                "type": "probe_result",
                "index": number,
                "strategy": strategy,
                "rank": rank,
                "top": {"source": top.source, "section": top.section, "score": top.score} if top else None,
            }
    count = len(probes) or 1
    summary = {
        strategy: {
            "hit1": round(values["hit1"] / count, 2),
            "hit5": round(values["hit5"] / count, 2),
            "mrr": round(values["rr"] / count, 3),
        }
        for strategy, values in totals.items()
    }
    yield {"type": "probe_summary", "k": k, "summary": summary}


def evaluate(index: RagIndex, embedder: Embedder, *, k: int = 5, probes: tuple[Probe, ...] = PROBES) -> dict[str, Any]:
    """Тот же прогон целиком: позиция первого куска из нужного файла по каждому вопросу и стратегии."""
    rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    for event in iter_evaluate(index, embedder, k=k, probes=probes):
        if event["type"] == "probe_question":
            rows.append({"question": event["question"], "sources": event["sources"]})
        elif event["type"] == "probe_result":
            rows[-1][event["strategy"]] = {"rank": event["rank"], "top": event["top"]}
        elif event["type"] == "probe_summary":
            summary = event["summary"]
    return {"k": k, "questions": rows, "summary": summary}

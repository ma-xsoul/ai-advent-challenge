"""
Контрольные вопросы для сравнения стратегий нарезки — день 21.

У каждого вопроса — файлы, в которых лежит ответ. Поиск хорош, если
кусок из нужного файла оказывается наверху выдачи. Так стратегии
сравниваются не «на глаз», а числами:

- hit@1 — доля вопросов, где первый же кусок из нужного файла;
- hit@5 — доля вопросов, где нужный файл есть среди первых пяти;
- MRR   — средняя обратная позиция первого попадания (1, 1/2, 1/3…):
          чем выше нужный кусок, тем ближе к 1.

День 22 берёт эти же вопросы контрольными: у каждого есть ожидание —
что должно быть в ответе — и факты, которые код ищет в ответе модели.
Факт — несколько допустимых формулировок; «a & b» значит, что в ответе
должны быть обе части. Сам файл в индекс не попадает (corpus.EXCLUDED):
иначе база отвечала бы на вопросы их же ответами.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .chunking import STRATEGIES
from .embeddings import Embedder
from .index import RagIndex


@dataclass(frozen=True)
class Fact:
    """Что должно прозвучать в ответе: подпись для людей и варианты для кода."""

    label: str
    variants: tuple[str, ...]

    def found_in(self, answer: str) -> bool:
        text = _plain(answer)
        return any(all(part in text for part in _plain(variant).split(" & ")) for variant in self.variants)


def _plain(text: str) -> str:
    return text.casefold().replace("ё", "е").replace("`", "")


@dataclass(frozen=True)
class Probe:
    question: str
    sources: tuple[str, ...]
    expected: str = ""
    facts: tuple[Fact, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "sources": list(self.sources),
            "expected": self.expected,
            "facts": [fact.label for fact in self.facts],
        }


PAGE = "advent/templates/days/day{:02d}.html"

# Источник — любой документ, где ответ действительно есть: и код, и
# страница дня, которая объясняет то же самое словами.
PROBES = (
    Probe(
        "Как агент вызывает инструменты MCP и возвращает результат модели?",
        ("advent/mcp/agent.py", PAGE.format(17)),
        "Агент берёт у сервера tools/list и отдаёт схемы модели; модель просит tool_calls, а вызывает "
        "инструмент агент — через MCP (tools/call), результат кладёт в историю сообщением role=tool; "
        "кругов не больше MAX_ROUNDS = 4, на последнем инструменты запрещены — tool_choice=none.",
        (
            Fact("схемы инструментов — из tools/list", ("tools/list", "list_tools")),
            Fact("вызов через MCP — tools/call", ("tools/call", "call_tool")),
            Fact("кругов не больше MAX_ROUNDS = 4", ("max_rounds & 4", "4 круга", "четыре круга")),
            Fact("на последнем круге tool_choice=none", ("tool_choice",)),
        ),
    ),
    Probe(
        "Как устроено расписание через cron и что делает тик?",
        ("advent/scheduler/__init__.py", "advent/scheduler/cron.py", PAGE.format(18)),
        "Строка в crontab пользователя с маркером # advent-day18 раз в N минут запускает "
        "python -m advent.scheduler tick; тик снимает курс инструментом collect_rate без модели, "
        "а когда подошло время — агент пишет сводку во «Входящие».",
        (
            Fact("будит системный cron — у агента нет heartbeat", ("heartbeat", "системный cron", "crontab", "advent-day18")),
            Fact("запуск python -m advent.scheduler tick", ("advent.scheduler", "scheduler tick")),
            Fact("тик снимает курс — collect_rate, без модели", ("collect_rate", "снимок курса", "снимает курс")),
            Fact("сводка ложится во «Входящие»", ("входящ", "inbox")),
        ),
    ),
    Probe(
        "Как старая часть истории сворачивается в конспект?",
        ("advent/agent/agent.py", "advent/agent/config.py", PAGE.format(9)),
        "CompressionPolicy: каждые every=10 сообщений всё, кроме последних keep_last=4, сворачивается "
        "в конспект отдельным запросом к модели; конспект подставляется вместо старых сообщений, "
        "потолок — summary_max_tokens=800, стек при этом не удаляется.",
        (
            Fact("последние keep_last остаются как есть", ("keep_last", "последние 4", "4 последних", "четыре последн")),
            Fact("сжатие каждые every сообщений", ("every", "каждые 10", "10 сообщений", "каждые десять")),
            Fact("конспект — отдельным запросом к модели", ("отдельным запросом", "отдельный запрос", "отдельного запроса")),
            Fact("стек не удаляется — сжимается только запрос", ("стек не", "не трога", "не удаля", "сжимается контекст")),
            Fact("потолок конспекта — summary_max_tokens", ("summary_max_tokens",)),
        ),
    ),
    Probe(
        "Какие слои памяти есть у агента и сколько живёт каждый?",
        ("advent/agent/memory.py", PAGE.format(11)),
        "Три слоя по времени жизни: chat — текущий диалог, умирает по «Новому диалогу»; task — текущая "
        "задача, умирает по «Новой задаче»; user — сам пользователь, живёт, пока не удалят руками.",
        (
            Fact("три слоя: chat, task, user", ("chat & task & user",)),
            Fact("chat живёт до «Нового диалога»", ("новому диалогу", "новый диалог", "нового диалога", "новом диалоге", "конца диалога", "текущий диалог")),
            Fact("task живёт до «Новой задачи»", ("новой задаче", "новая задача", "новой задачи", "конца задачи", "текущая задача", "текущей задачи")),
            Fact("user живёт, пока не удалят руками", ("руками", "вручную", "пока не удал")),
        ),
    ),
    Probe(
        "Какие переходы между этапами задачи разрешены и что такое guard?",
        ("advent/agent/task.py", PAGE.format(15)),
        "planning → execution (план утверждён), execution → validation (все шаги закрыты), validation → done "
        "(проверка принята) и возврат validation → execution; guard — функция-условие перехода, которая "
        "возвращает причину отказа: например, план и финал подтверждает только пользователь.",
        (
            Fact("этапы planning → execution → validation → done", ("planning & execution & validation & done",)),
            Fact("возврат validation → execution", ("возврат", "вернуть на шаг", "вернуться на шаг", "validation → execution")),
            Fact("переход дальше — когда все шаги закрыты", ("шаги закрыты", "закрыты все шаги", "не закрыты шаги", "все шаги")),
            Fact("утверждает только пользователь", ("только пользователь",)),
        ),
    ),
    Probe(
        "Как ловятся нарушения инвариантов и кто выносит вердикт?",
        ("advent/agent/invariants.py", PAGE.format(14)),
        "Два рубежа: инварианты уходят в системный промпт требованием сверить запрос и отказать с названием "
        "правила; после ответа проверяемое ловит код регулярками, а непроверяемое — ревизор, отдельный "
        "запрос к модели.",
        (
            Fact("инварианты — в системном промпте", ("системный промпт", "системном промпте", "system prompt")),
            Fact("проверяемое — регулярками в коде", ("регуляр", "regex")),
            Fact("непроверяемое — ревизор, отдельный запрос к модели", ("ревизор", "reviewer")),
        ),
    ),
    Probe(
        "Как сервер курса доллара считает итог за период?",
        ("advent/mcp/servers/rates.py",),
        "Функция aggregate по снимкам за период: первый и последний курс, изменение в рублях и процентах, "
        "минимум и максимум со временем, среднее и официальный курс ЦБ с разницей к последнему снимку.",
        (
            Fact("функция aggregate", ("aggregate",)),
            Fact("первый и последний снимок", ("перв & последн", "first & last")),
            Fact("минимум и максимум", ("минимум & максимум", "min & max", "минимальн & максимальн")),
            Fact("среднее", ("средн", "average", "avg")),
            Fact("отрыв последнего снимка от курса ЦБ", ("last_vs_official", "отрыв", "от официального", "с официальным", "к официальному")),
        ),
    ),
    Probe(
        "Как проверяется, что данные дошли по цепочке инструментов без искажений?",
        ("advent/mcp/chain.py", PAGE.format(19)),
        "check_chain сверяет журнал вызовов хода: шаги поиск → сравнение → сохранение по порядку, в сравнение "
        "ушли ровно имена из поиска, таблица попала в отчёт дословно, файл на диске совпадает с переданным "
        "в save_report.",
        (
            Fact("сверка по журналу вызовов, а не по словам модели", ("журнал", "check_chain")),
            Fact("порядок: поиск → сравнение → сохранение", ("порядк", "по очереди")),
            Fact("в сравнение ушли имена из поиска", ("имена", "ровно те")),
            Fact("таблица в отчёте дословно", ("дословно", "таблиц")),
            Fact("файл на диске совпадает", ("на диске", "байт")),
        ),
    ),
    Probe(
        "Как оценить число токенов в тексте без токенизатора?",
        ("advent/agent/tokens.py",),
        "estimate_tokens считает символы: около 2.5 символа кириллицы на токен и около 4 — латиницы и прочего, "
        "плюс 4 токена обвязки на сообщение; калибровка — по реальным ответам DeepSeek.",
        (
            Fact("функция estimate_tokens", ("estimate_tokens",)),
            Fact("≈2.5 символа кириллицы на токен", ("2.5", "2,5")),
            Fact("латиница и прочее — ≈4 символа на токен", ("латин", "остальн", "прочи", "other_chars")),
            Fact("обвязка сообщения — +4 токена", ("обвязк", "overhead")),
        ),
    ),
    Probe(
        "Как агент решает, на какой MCP-сервер отправить вызов, если серверов несколько?",
        ("advent/mcp/agent.py", "advent/mcp/orchestration.py", PAGE.format(20)),
        "Модель видит инструменты с префиксом сервера — github__search_repositories, rates__rate_summary — и "
        "называет только инструмент; агент по префиксу (ROUTE_SEPARATOR «__») находит сервер в таблице "
        "маршрутов и отправляет вызов туда.",
        (
            Fact("префикс сервера в имени инструмента", ("префикс", "prefix")),
            Fact("разделитель «__»: сервер__инструмент", ("route_separator", "github__", "rates__", "gitmcp__", "сервер__", "server__")),
            Fact("таблица маршрутов", ("маршрут", "route")),
        ),
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

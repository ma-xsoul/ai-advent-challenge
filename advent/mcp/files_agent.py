"""
Ассистент для работы с файлами проекта — день 34.

Задача ставится на уровне цели («проверь, что у каждого дня есть всё
нужное»), а не «открой файл X». Агент дня 17 с MCP-сервером файлов
(advent/mcp/servers/files.py) сам решает, что искать и что читать, и
готовит изменения в черновике. Каждый прогон начинается с чистого
черновика и сохраняется: цель, вызовы инструментов, отчёт и diff — ту же
цель можно запустить снова и сравнить.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from ..agent.config import AgentConfig
from ..agent.transport import Transport
from ..local.rag import run_id
from .agent import ToolDialog, stream_turn
from .client import FILES_SERVER, TransportFactory
from .servers.files import Workspace


MODEL = "deepseek-v4-flash"
MAX_ROUNDS = 16

SYSTEM_PROMPT = """Ты — ассистент, который сам работает с файлами проекта AI Advent: Flask-лаборатория домашних заданий.
Устройство: advent/navigation.py — список дней; advent/days/dayNN/routes.py — blueprint дня; advent/templates/days/dayNN.html — страница; advent/static/css/dayNN.css и advent/static/js/dayNN.js — стили и скрипт; tests/ — тесты; README.md и docs/ — документация; advent/mcp, advent/rag, advent/local, advent/agent — модули.

Тебе дают цель. Как работать:
1. Сам реши, какие файлы нужны: начинай с search или list_files, потом read_file только нужных кусков. Не проси пользователя открыть файл. Не перечитывай то, что уже прочитал.
Бюджет — около 12 шагов. Если цель требует записи: 3–6 шагов на факты, потом сразу write_file или edit_file, потом отчёт. Лучше записать по собранным фактам, чем не записать ничего.
2. Проверяй факты в файлах, ничего не выдумывай: каждое утверждение — с путём и номером строки.
3. Если цель требует изменить или создать файл — write_file (новый файл) или edit_file (точечная правка). Это черновик: человек увидит diff и решит. Писать можно только в docs/*.md и .md в корне. Если цель не просит изменений — ничего не пиши.
4. В конце — короткий отчёт по-русски в markdown: что сделал, какие файлы смотрел, что нашёл (таблица, если находок много), какие изменения подготовил."""

SCENARIOS = (
    {
        "key": "usages",
        "title": "Найти все использования API",
        "goal": "Найди все места, где используется ndjson_stream: где он объявлен, какие дни его вызывают и что они стримят. Ответ — таблица: файл, строка, что стримит.",
    },
    {
        "key": "invariants",
        "title": "Проверить правила проекта",
        "goal": (
            "Проверь правило проекта: у каждого дня из advent/navigation.py есть blueprint advent/days/dayNN/routes.py, "
            "шаблон advent/templates/days/dayNN.html, свои стили advent/static/css/dayNN.css, свой скрипт "
            "advent/static/js/dayNN.js и строка dayNN/routes.py в README.md. Хватит нескольких вызовов: list_files по "
            "каждой папке и поиск по README. Для каждого нарушения загляни только в шаблон этого дня — какие стили и "
            "скрипт он подключает — и скажи, ошибка это или сознательное исключение. Итог — таблица нарушений."
        ),
    },
    {
        "key": "changelog",
        "title": "Сгенерировать CHANGELOG",
        "goal": (
            "Сгенерируй CHANGELOG.md в корне проекта: раздел «Неделя 8 — продуктовая» с днями 31–34, по пункту на "
            "день — что появилось: модули, MCP-серверы, страницы, API. Факты бери из описаний «Day 31» … «Day 34» в "
            "README.md (найди их поиском), код открывай, только если в README чего-то нет."
        ),
    },
    {
        "key": "docs",
        "title": "Обновить документацию под код",
        "goal": (
            "Обнови docs/api.md под код дня 34: строка дня 34 в таблице адресов по дням, короткий раздел «День 34: "
            "файлы проекта» — адреса из advent/days/day34/routes.py и что они принимают, — и сервер files с его "
            "инструментами из advent/mcp/servers/files.py в таблице MCP-серверов. Только то, что есть в коде; "
            "правь edit_file, не переписывай файл целиком."
        ),
    },
)


def run_goal(
    goal: str,
    *,
    workspace: Workspace,
    transport: Transport,
    factory: TransportFactory | None = None,
    model: str = MODEL,
) -> Iterator[dict[str, Any]]:
    """События: start → connected → (model, tool_call, tool_result)* → answer → changes → run (сохраняемая запись)."""
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    dropped = workspace.discard()
    yield {"type": "start", "id": run_id(started_at), "started_at": started_at, "goal": goal, "discarded": dropped}
    started = time.perf_counter()
    config = AgentConfig(model=model, system_prompt=SYSTEM_PROMPT, temperature=0.0, max_tokens=3000, name="day34")
    calls: list[dict[str, Any]] = []
    answer, stats = "", {}
    for event in stream_turn(ToolDialog(), goal, config=config, transport=transport, server=FILES_SERVER,
                             factory=factory, max_rounds=MAX_ROUNDS):
        if event["type"] == "tool_result":
            calls.append({key: event.get(key) for key in ("name", "arguments", "ok", "chars", "ms")})
        elif event["type"] == "answer":
            answer = event["content"]
        elif event["type"] == "done":
            stats = event["stats"]
            continue
        yield event
    changes = workspace.changes()
    yield {"type": "changes", "files": changes}
    files_read = sorted({str(call["arguments"].get("path")) for call in calls
                         if call["name"] == "read_file" and isinstance(call["arguments"], dict) and call["arguments"].get("path")})
    yield {
        "type": "run",
        "run": {
            "id": run_id(started_at),
            "started_at": started_at,
            "goal": goal,
            "calls": calls,
            "files_read": files_read,
            "answer": answer,
            "changes": [{key: item[key] for key in ("path", "new", "added", "removed")} for item in changes],
            "diff": "".join(item["diff"] for item in changes),
            "stats": {
                "ms": round((time.perf_counter() - started) * 1000),
                "llm_calls": stats.get("llm_calls"),
                "tool_calls": stats.get("tool_calls"),
                "tokens": (stats.get("prompt_tokens") or 0) + (stats.get("completion_tokens") or 0),
                "model": model,
            },
        },
    }

"""
Ассистент разработчика — день 31.

RAG для документации, MCP для git, ядро — агент:

1. /help <вопрос> — поиск по свежему индексу проекта (README, docs/*.md,
   код, страницы дней; нарезка structure, bge-m3) — top-6 кусков уходят
   агенту вместе с вопросом;
2. агент (advent/mcp/agent.py) подключается к своему MCP-серверу git и сам
   решает, нужен ли он: «над чем я сейчас работаю» — ветка и изменения,
   «как устроен проект» — хватит документации;
3. ответ ссылается на куски [n] — страница показывает, какие из найденных
   кусков пошли в дело.

Короткие команды (/branch, /files, /diff, /log) отвечают прямо из
MCP-сервера git, без модели: решать там нечего.

Индекс — свой, data/day31/index.sqlite3: индекс дня 21 собран до docs/ и
до кода последних недель, а пересобирать его — значит менять данные
другого дня. Кэш векторов по хэшу текста делает пересборку быстрой.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from ..agent.config import AgentConfig
from ..agent.transport import Transport
from ..mcp.agent import ToolDialog, stream_turn
from ..mcp.client import GIT_SERVER, PROJECT_ROOT, TransportFactory, call_server_tool
from .corpus import collect
from .embeddings import Embedder
from .index import Hit, RagIndex


DEFAULT_INDEX = PROJECT_ROOT / "data" / "day31" / "index.sqlite3"
STRATEGY = "structure"
TOP_K = 6
CHUNK_CHARS = 1600
MODEL = "deepseek-v4-flash"

SYSTEM_PROMPT = (
    "Ты — ассистент разработчика проекта AI Advent: Flask-лаборатория домашних заданий, где каждый день — "
    "отдельная страница со своим API. В сообщении пользователя — найденные фрагменты документации и кода "
    "проекта с номерами [n] и вопрос.\n"
    "Правила:\n"
    "- Про устройство проекта отвечай по фрагментам и ставь ссылки [n] на те, откуда взят факт.\n"
    "- Инструменты git зови, только когда вопрос про состояние репозитория — ветку, изменения, коммиты — "
    "или нужно проверить, какие файлы и папки есть (list_files). Не выдумывай ветку и изменения.\n"
    "- На вопросы об устройстве кода git не нужен — и не упоминай ветку, если о ней не спрашивали.\n"
    "- Если ни во фрагментах, ни в git ответа нет — так и скажи и подскажи, где искать.\n"
    "- Пиши по-русски, коротко и по делу; пути файлов и имена функций — в `обратных кавычках`."
)

COMMANDS = {
    "/branch": ("git_branch", {}, "текущая ветка и последний коммит"),
    "/files": ("git_status", {}, "незакоммиченные изменения"),
    "/diff": ("git_diff", {"scope": "working"}, "diff незакоммиченных изменений"),
    "/log": ("git_log", {"limit": 10}, "последние 10 коммитов"),
}

EXAMPLES = (
    "Как устроен проект и где что лежит?",
    "Где хранится память агента и какие у неё слои?",
    "Как работает очередь в приватном сервисе дня 30?",
    "Над чем я сейчас работаю?",
    "Какие MCP-серверы есть в проекте и что они умеют?",
)

CITATION = re.compile(r"\[(\d{1,2})\]")

Emit = Callable[[dict[str, Any]], None]


def build_index(index: RagIndex, embedder: Embedder, emit: Emit) -> dict[str, Any]:
    """Свежий индекс проекта: только нарезка structure — её и ищет ассистент."""
    return index.build(collect(), embedder, strategies=(STRATEGY,), progress=emit)


def context_message(question: str, hits: list[Hit]) -> str:
    blocks = []
    for hit in hits:
        text = hit.text if len(hit.text) <= CHUNK_CHARS else hit.text[:CHUNK_CHARS] + "\n…"
        blocks.append(f"[{hit.rank}] {hit.source} · {hit.section} · строки {hit.start_line}–{hit.end_line}\n{text}")
    return "Фрагменты документации и кода проекта:\n\n" + "\n\n---\n\n".join(blocks) + f"\n\nВопрос: {question}"


def hit_card(hit: Hit) -> dict[str, Any]:
    return {
        "n": hit.rank,
        "source": hit.source,
        "section": hit.section,
        "kind": hit.kind,
        "lines": f"{hit.start_line}–{hit.end_line}",
        "score": round(hit.score, 3),
    }


def cited(answer: str, total: int) -> list[int]:
    return sorted({int(n) for n in CITATION.findall(answer) if 1 <= int(n) <= total})


def help_turn(
    question: str,
    *,
    index: RagIndex,
    embedder: Embedder,
    transport: Transport,
    factory: TransportFactory | None = None,
    model: str = MODEL,
) -> Iterator[dict[str, Any]]:
    """События: retrieved → connected → (model, tool_call, tool_result)* → answer → done."""
    hits = index.search(question, embedder, STRATEGY, k=TOP_K)
    yield {"type": "retrieved", "hits": [hit_card(hit) for hit in hits]}
    config = AgentConfig(model=model, system_prompt=SYSTEM_PROMPT, temperature=0.3, max_tokens=1200, name="day31")
    for event in stream_turn(
        ToolDialog(), context_message(question, hits), config=config, transport=transport, server=GIT_SERVER, factory=factory,
    ):
        if event["type"] == "answer":
            event = {**event, "cited": cited(event["content"], len(hits))}
        yield event


def command(text: str, *, factory: TransportFactory | None = None) -> dict[str, Any]:
    """Короткая команда — один вызов инструмента git через MCP, без модели."""
    name = text.strip().split()[0].lower() if text.strip() else ""
    if name not in COMMANDS:
        raise ValueError(f"Нет такой команды: {name or '—'}. Есть: /help, " + ", ".join(COMMANDS))
    tool, arguments, title = COMMANDS[name]
    result = call_server_tool(GIT_SERVER, tool, arguments, transport_factory=factory)
    data = result.data
    if data is None and result.ok:
        try:
            data = json.loads(result.text)
        except ValueError:
            data = None
    return {"command": name, "tool": tool, "title": title, "ok": result.ok, "ms": result.ms,
            "data": data, "error": result.error}


def index_path(configured: str | Path | None) -> Path:
    return Path(configured or DEFAULT_INDEX)

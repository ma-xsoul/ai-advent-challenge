"""
Ассистент поддержки — день 33.

Продукт — приватный AI-сервис дня 30. На каждое сообщение клиента:

1. RAG по документации продукта — docs/support/*.md (FAQ): свой
   маленький индекс data/day33/index.sqlite3, нарезка structure, bge-m3.
   Кода в нём нет: клиенту нужны правила продукта, а не реализация.
   Индекс пересобирается сам, если FAQ изменился;
2. агент дня 17 с MCP-сервером CRM (advent/mcp/servers/crm.py): номер
   тикета он получает в сообщении и сам смотрит тикет, журнал запросов
   клиента и состояние сервиса;
3. ответ клиенту — причина по его данным и шаги по FAQ со ссылками [n].

Переписка по тикету хранится в data/day33/dialogs/<тикет>-d33.json:
второе сообщение клиента агент видит вместе с первым.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..agent.config import AgentConfig
from ..agent.transport import Transport
from ..mcp.agent import ToolDialog, ToolDialogStore, stream_turn
from ..mcp.client import CRM_SERVER, PROJECT_ROOT, TransportFactory
from .corpus import Document
from .embeddings import Embedder
from .index import Hit, RagIndex


DOCS = PROJECT_ROOT / "docs" / "support"
DEFAULT_INDEX = PROJECT_ROOT / "data" / "day33" / "index.sqlite3"
DEFAULT_DIALOGS = PROJECT_ROOT / "data" / "day33" / "dialogs"
STRATEGY = "structure"
TOP_K = 4
MODEL = "deepseek-v4-flash"
MESSAGE_MARK = "Сообщение клиента:"

SYSTEM_PROMPT = (
    "Ты — первая линия поддержки «Приватного AI-сервиса»: языковая модель на сервере владельца, чат в браузере и HTTP API, "
    "доступ по ключу. Тебе пишет клиент по тикету; номер тикета и найденные фрагменты FAQ — в сообщении.\n"
    "Как работать:\n"
    "1. Сначала посмотри тикет в CRM (get_ticket), затем журнал запросов клиента (client_events). Если сайт не открывается, "
    "адрес не работает или ответы долгие — ещё и service_status.\n"
    "2. Найди причину в его данных: коды 401, 413, 429, 503, статус ключа, тариф и лимит, смена адреса, очередь.\n"
    "3. Ответь клиенту на «вы», по-русски: сначала причина — что именно видно в его данных (время, код, подробность), "
    "потом что сделать — короткие шаги. Правила продукта бери только из фрагментов FAQ и ставь ссылки [n].\n"
    "Ответ — готовый текст для клиента, его отправит оператор: обращайся только к клиенту, без заголовка с номером тикета "
    "и без приписок для оператора.\n"
    "Нельзя: выдумывать данные клиента, обещать то, чего нет в FAQ, вызывать reply_to_ticket — ответ отправляет оператор. "
    "Если причину не видно — так и скажи и спроси, что именно клиент делал."
)

CITATION = re.compile(r"\[(\d{1,2})\]")


# ── индекс FAQ ───────────────────────────────────────────────────────────


def documents(folder: Path = DOCS) -> list[Document]:
    result = []
    for path in sorted(folder.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("# ")), path.stem)
        result.append(Document(str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else path.name, "doc", title, text))
    return result


def ensure_index(index: RagIndex, embedder: Embedder, docs: list[Document]) -> dict[str, Any]:
    """Индекс FAQ: собрать, если его нет, он другой моделью или FAQ поменялся."""
    info = index.info()
    chars = sum(len(doc.text) for doc in docs)
    fresh = (
        info.get("built")
        and info.get("model") == embedder.name
        and STRATEGY in (info.get("strategies") or {})
        and (info.get("corpus") or {}).get("chars") == chars
        and (info.get("corpus") or {}).get("documents") == len(docs)
    )
    return info if fresh else index.build(docs, embedder, strategies=(STRATEGY,))


def hit_card(hit: Hit) -> dict[str, Any]:
    return {"n": hit.rank, "source": hit.source, "section": hit.section, "score": round(hit.score, 3)}


# ── ход ──────────────────────────────────────────────────────────────────


def message(ticket_id: str, text: str, hits: list[Hit]) -> str:
    blocks = [f"[{hit.rank}] {hit.section}\n{hit.text}" for hit in hits]
    return (
        f"Тикет: {ticket_id}\n\nФрагменты FAQ продукта:\n\n" + "\n\n---\n\n".join(blocks)
        + f"\n\n{MESSAGE_MARK} {text}"
    )


def turn(
    ticket_id: str,
    text: str,
    *,
    dialog: ToolDialog,
    index: RagIndex,
    embedder: Embedder,
    transport: Transport,
    factory: TransportFactory | None = None,
    subject: str = "",
    model: str = MODEL,
) -> Iterator[dict[str, Any]]:
    """События: retrieved → connected → (model, tool_call, tool_result)* → answer → done."""
    hits = index.search(f"{subject} {text}".strip(), embedder, STRATEGY, k=TOP_K)
    yield {"type": "retrieved", "hits": [hit_card(hit) for hit in hits]}
    config = AgentConfig(model=model, system_prompt=SYSTEM_PROMPT, temperature=0.3, max_tokens=1000, name="day33")
    for event in stream_turn(dialog, message(ticket_id, text, hits), config=config, transport=transport, server=CRM_SERVER, factory=factory):
        if event["type"] == "answer":
            cited = sorted({int(n) for n in CITATION.findall(event["content"]) if 1 <= int(n) <= len(hits)})
            event = {**event, "cited": cited}
        yield event


def history(dialog: ToolDialog) -> list[dict[str, Any]]:
    """Переписка для страницы: сообщения клиента без фрагментов FAQ, ответы ассистента и его вызовы CRM."""
    result: list[dict[str, Any]] = []
    calls = iter(dialog.calls)
    for item in dialog.messages:
        if item["role"] == "user":
            content = item.get("content") or ""
            result.append({"role": "client", "text": content.split(MESSAGE_MARK, 1)[-1].strip()})
        elif item["role"] == "assistant" and item.get("tool_calls"):
            for _ in item["tool_calls"]:
                call = next(calls, None)
                if call:
                    result.append({"role": "tool", "name": call["tool"], "arguments": call["arguments"], "ok": call["ok"]})
        elif item["role"] == "assistant":
            result.append({"role": "assistant", "text": item.get("content") or ""})
    return result


def dialogs(directory: str | Path | None) -> ToolDialogStore:
    return ToolDialogStore(directory or DEFAULT_DIALOGS, "d33")

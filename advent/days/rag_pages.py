"""
Общее для страниц RAG (дни 22–23): индекс, эмбеддер, транспорт из конфига,
готовность индекса и NDJSON-поток из рабочего потока.
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any

from flask import Response, current_app, stream_with_context

from ..agent.transport import DeepSeekTransport, TransportError
from ..rag import DEFAULT_PATH, EmbeddingError, IndexNotReady, OllamaEmbedder, RagIndex
from ..rag.answer import STRATEGY


def rag_index() -> RagIndex:
    return RagIndex(current_app.config.get("DAY21_INDEX_PATH") or DEFAULT_PATH)


def embedder():
    return current_app.config.get("RAG_EMBEDDER") or OllamaEmbedder()


def transport():
    return current_app.config.get("AGENT_TRANSPORT") or DeepSeekTransport()


def index_state(index: RagIndex, embedder) -> dict[str, Any]:
    """Готов ли индекс для вопросов: собран, той же моделью, со стратегией structure."""
    info = index.info()
    state = {"ready": False, "model": info.get("model"), "built_at": info.get("built_at")}
    if not info["built"]:
        return {**state, "reason": "Индекс ещё не собран — соберите его на странице дня 21."}
    if info.get("model") != embedder.name:
        return {**state, "reason": f"Индекс собран моделью {info['model']}, а вопросы — {embedder.name}. Пересоберите на дне 21."}
    strategy = (info.get("strategies") or {}).get(STRATEGY)
    if not strategy:
        return {**state, "reason": f"В индексе нет стратегии {STRATEGY} — пересоберите на дне 21."}
    return {**state, "ready": True, "chunks": strategy["chunks"], "documents": info["corpus"]["documents"]}


def ndjson_stream(work: Callable[[Callable[[dict[str, Any]], None]], None]) -> Response:
    """Работа идёт в своём потоке, события — через очередь: страница видит их сразу."""
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def worker() -> None:
        try:
            work(events.put)
        except (IndexNotReady, EmbeddingError, TransportError) as error:
            events.put({"type": "error", "error": str(error)})
        except Exception as error:  # noqa: BLE001 — до страницы должна дойти любая причина
            events.put({"type": "error", "error": f"{type(error).__name__}: {error}"})
        finally:
            events.put(None)

    threading.Thread(target=worker, name="rag-answer", daemon=True).start()

    def generate() -> Iterator[str]:
        while (event := events.get()) is not None:
            yield json.dumps(event, ensure_ascii=False) + "\n"

    response = Response(stream_with_context(generate()), mimetype="application/x-ndjson")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response

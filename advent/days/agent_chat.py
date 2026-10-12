"""
Общая часть маршрутов чата с агентом для дней 7+.

Каждый день добавляет своё — лимит, стратегию, сценарий, — а сессия,
поток ответа, сохранение и слепок состояния у всех одинаковые.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from flask import Response, current_app, jsonify, request, stream_with_context

from ..agent import (
    Agent,
    AgentRegistry,
    CompressionFailed,
    CompressionRecord,
    CompressionStarted,
    ContextOverflowError,
    FactsFailed,
    FactsRecord,
    FactsStarted,
    StoreError,
    TransportError,
)


SESSION_COOKIE = "agent_session"
MAX_MESSAGE_LENGTH = 10_000


def registry(name: str = "persistent_agent_registry") -> AgentRegistry:
    return current_app.extensions[name]


def session_id() -> str:
    return request.cookies.get(SESSION_COOKIE) or uuid.uuid4().hex


def remember_session(response, sid: str) -> None:
    if request.cookies.get(SESSION_COOKIE) != sid:
        response.set_cookie(SESSION_COOKIE, sid, samesite="Lax", max_age=60 * 60 * 24 * 30)


def state(reg: AgentRegistry, sid: str, agent: Agent) -> dict[str, Any]:
    """Слепок агента плюс то, что знает только приложение: реестр и файл."""
    snapshot = agent.snapshot()
    snapshot["agents_in_registry"] = len(reg)
    stored = reg.stored(sid)
    snapshot["storage"] = asdict(stored) if stored else None
    return snapshot


def message_from_request() -> tuple[str | None, tuple[Any, int] | None]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, (jsonify({"error": "Ожидался JSON-объект с сообщением"}), 400)

    text = payload.get("message", "")
    if not isinstance(text, str) or not text.strip():
        return None, (jsonify({"error": "Введите сообщение"}), 400)
    if len(text.strip()) > MAX_MESSAGE_LENGTH:
        return None, (
            jsonify({"error": f"Сообщение не должно превышать {MAX_MESSAGE_LENGTH:,} символов".replace(",", " ")}),
            400,
        )
    return text.strip(), None


def stream_chat(
    reg: AgentRegistry,
    sid: str,
    agent: Agent,
    text: str,
    *,
    state_fn: Callable[[Agent], dict[str, Any]] | None = None,
    store_id: str | None = None,
    after_turn: Callable[[Agent, str], Any] | None = None,
) -> Response:
    """
    Потоковый ответ агента в NDJSON. После полного ответа стек уходит на
    диск; ошибка записи не прячет уже полученный ответ и уезжает отдельным
    полем. Переполнение контекста — отдельное событие с числами. События
    сжатия (день 9) идут в том же потоке до или после текста ответа.

    store_id — под каким идентификатором агент живёт в реестре, если это
    не сама сессия: день 9 держит на одну сессию двух агентов.

    after_turn — генератор событий, который прогоняется после ответа и до
    события done: день 11 раскладывает реплику по слоям памяти отдельным
    запросом и показывает это в ленте.
    """
    store_id = store_id or sid
    snapshot_of = state_fn or (lambda a: state(reg, store_id, a))

    def generate_events():
        try:
            reply = yield from _stream_reply(agent, text)
        except ContextOverflowError as error:
            # Агент мог успеть сжать историю перед отказом — это уже стоило
            # токенов, и терять эту запись при перезапуске не хочется.
            _save_quietly(reg, store_id)
            yield _event(
                {
                    "type": "error",
                    "error": str(error),
                    "overflow": {"estimated": error.estimated, "limit": error.limit},
                    "snapshot": snapshot_of(agent),
                }
            )
            return
        except TransportError as error:
            yield _event({"type": "error", "error": str(error), "snapshot": snapshot_of(agent)})
            return

        save_error = None
        try:
            reg.save(store_id)
        except OSError as error:
            save_error = f"Ответ получен, но не сохранён на диск: {error}"

        if after_turn is not None:
            for extra in after_turn(agent, text):
                yield _event(extra)

        yield _event(
            {
                "type": "done",
                "reply": {
                    "content": reply.content,
                    "finish_reason": reply.finish_reason,
                    "model": reply.model,
                    "input_tokens": reply.input_tokens,
                    "output_tokens": reply.output_tokens,
                },
                "turn": agent.turns[-1].as_dict() if agent.turns else None,
                "snapshot": snapshot_of(agent),
                "save_error": save_error,
            }
        )

    response = Response(
        stream_with_context(generate_events()),
        mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    remember_session(response, sid)
    return response


def _stream_reply(agent: Agent, text: str):
    generator = agent.stream(text)
    try:
        while True:
            try:
                chunk = next(generator)
            except StopIteration as stop:
                return stop.value
            yield _event(_stream_event(chunk))
    finally:
        # Закрытие HTTP-потока должно дойти до агента и откатить незавершённый ход.
        generator.close()


def _stream_event(chunk: Any) -> dict[str, Any]:
    """Кусок текста — delta; события сжатия — свои типы, чтобы лента показала их отдельно."""
    if isinstance(chunk, str):
        return {"type": "delta", "content": chunk}
    if isinstance(chunk, CompressionStarted):
        return {"type": "compressing", "reason": chunk.reason, "messages": chunk.messages}
    if isinstance(chunk, CompressionRecord):
        return {"type": "compressed", "compression": asdict(chunk)}
    if isinstance(chunk, CompressionFailed):
        return {"type": "compression_failed", "reason": chunk.reason, "error": chunk.error}
    if isinstance(chunk, FactsStarted):
        return {"type": "facts_updating", "keys": chunk.keys}
    if isinstance(chunk, FactsRecord):
        return {"type": "facts_updated", "update": asdict(chunk)}
    if isinstance(chunk, FactsFailed):
        return {"type": "facts_failed", "error": chunk.error}
    raise TypeError(f"Агент отдал в поток неизвестное событие: {type(chunk).__name__}")


def _save_quietly(reg: AgentRegistry, sid: str) -> None:
    try:
        reg.save(sid)
    except OSError:
        pass


def _event(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False) + "\n"


def store_error_response(error: StoreError):
    return jsonify({"error": str(error)}), 500


def reset_everything() -> dict[str, Any]:
    """
    Кнопка «сбросить всё» в боковой панели: снести все файлы сессий —
    диалоги, слои памяти, профили, задачи, деревья веток — и выкинуть
    агентов из памяти обоих реестров. То, что раньше делалось руками
    через rm -rf data/agents перед записью видео. Плюс данные дня 18:
    входящие, журнал тиков, снимки курса и лог cron, — и отчёты дней 19 и 20.
    """
    directory = Path(current_app.config["AGENT_STORE_DIR"])
    files = 0
    if directory.exists():
        for path in directory.glob("*.json"):
            try:
                path.unlink()
                files += 1
            except OSError:
                pass
        for path in directory.glob("*.tmp"):
            try:
                path.unlink()
            except OSError:
                pass
    agents = 0
    for name in ("agent_registry", "persistent_agent_registry", "local_agent_registry"):
        reg = current_app.extensions.get(name)
        if reg is not None:
            agents += reg.clear()

    from ..mcp.servers.github import DEFAULT_REPORTS
    from ..scheduler import DATA_DIR, reset as reset_day18

    day18 = reset_day18(current_app.config.get("DAY18_DIR") or DATA_DIR)
    reports = _unlink_reports(Path(current_app.config.get("DAY19_REPORTS_DIR") or DEFAULT_REPORTS))
    reports20 = _unlink_reports(Path(current_app.config.get("DAY20_REPORTS_DIR") or DATA_DIR.parent / "day20" / "reports"))
    return {"files": files, "agents": agents, "day18": day18, "day19_reports": reports, "day20_reports": reports20}


def _unlink_reports(directory: Path) -> int:
    removed = 0
    for path in directory.glob("*.md"):
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed

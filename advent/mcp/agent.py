"""
Агент с инструментами MCP — день 17.

Один ход пользователя — это цикл, а не один запрос к модели:

1. агент открывает сессию MCP (свой сервер поднимается процессом) и берёт
   tools/list — схемы инструментов;
2. отдаёт модели историю и схемы в поле tools;
3. модель ответила текстом — ход окончен. Попросила tool_calls — агент
   вызывает каждый инструмент через MCP (tools/call), кладёт результат
   в историю сообщением role=tool и снова идёт к модели;
4. кругов не больше MAX_ROUNDS: на последнем инструменты запрещены
   (tool_choice=none), и модель отвечает тем, что уже собрала.

Модель по MCP не ходит: она видит схемы и просит вызов, а вызывает агент.
Поэтому весь MCP здесь — на стороне агента, а модель получает только
текст результата.

SDK асинхронный, Flask — нет. Ход целиком живёт в одном anyio.run в
отдельном потоке, события уходят наружу через очередь — так страница
видит вызовы по мере того, как они происходят.
"""

from __future__ import annotations

import json
import os
import queue
import re
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio
from mcp import Client
from mcp.shared.exceptions import MCPError

from ..agent.config import AgentConfig
from ..agent.tokens import estimate_tokens
from ..agent.transport import Transport, TransportError
from .client import CLIENT_INFO, DEFAULT_TIMEOUT_SECONDS, McpServer, ToolInfo, TransportFactory, _describe, _list_tools, open_transport


# Сколько раз за ход модель может сходить к инструментам. Защита от
# зацикливания: модель, которая раз за разом просит одно и то же.
MAX_ROUNDS = 4

# Сколько текста результата получает модель и сколько видит страница.
RESULT_CHARS = 6000
PREVIEW_CHARS = 1500

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

Emit = Callable[[dict[str, Any]], None]


class DialogError(RuntimeError):
    """Файл диалога не удалось прочитать или записать."""


@dataclass
class ToolDialog:
    """
    История в формате chat completions — с тем, чего нет у обычного агента:
    сообщения assistant с tool_calls и сообщения tool с результатами.
    Плюс журнал вызовов и статистика ходов для панели.
    """

    messages: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    turns: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"messages": self.messages, "calls": self.calls, "turns": self.turns}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolDialog:
        return cls(
            messages=list(data.get("messages") or []),
            calls=list(data.get("calls") or []),
            turns=list(data.get("turns") or []),
        )


class ToolDialogStore:
    """<сессия>-<name>.json — диалог дня с инструментами."""

    def __init__(self, directory: str | Path, name: str) -> None:
        self._directory = Path(directory)
        self._name = name

    def load(self, session_id: str) -> ToolDialog:
        path = self._path(session_id)
        if not path.exists():
            return ToolDialog()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ToolDialog.from_dict(data["dialog"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise DialogError(f"{path.name}: {error}") from error

    def save(self, session_id: str, dialog: ToolDialog) -> None:
        path = self._path(session_id)
        self._directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": session_id,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dialog": dialog.as_dict(),
        }
        temp = path.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, path)
        except OSError as error:
            raise DialogError(f"Не удалось сохранить диалог: {error}") from error

    def delete(self, session_id: str) -> bool:
        try:
            self._path(session_id).unlink()
            return True
        except FileNotFoundError:
            return False

    def describe(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id)
        exists = path.exists()
        return {"file": path.name, "exists": exists, "bytes": path.stat().st_size if exists else 0}

    def _path(self, session_id: str) -> Path:
        if not _SAFE_ID.match(session_id):
            raise DialogError("Недопустимый идентификатор сессии")
        return self._directory / f"{session_id}-{self._name}.json"


def tool_spec(tool: ToolInfo) -> dict[str, Any]:
    """Инструмент MCP в том виде, в каком его ждёт модель: функция в поле tools."""
    return {
        "type": "function",
        "function": {"name": tool.name, "description": tool.description, "parameters": tool.schema},
    }


def stream_turn(
    dialog: ToolDialog,
    question: str,
    *,
    config: AgentConfig,
    transport: Transport,
    server: McpServer,
    factory: TransportFactory | None = None,
    on_done: Callable[[], None] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Iterator[dict[str, Any]]:
    """
    Ход в отдельном потоке, события — по мере появления. on_done зовётся в
    том же потоке после хода, даже если страница уже закрыла соединение:
    так ход, дошедший до конца, не теряется.
    """
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def worker() -> None:
        # «done» придерживаем до сохранения: страница получает итог, когда
        # диалог уже на диске.
        finished: list[dict[str, Any]] = []

        def emit(event: dict[str, Any]) -> None:
            if event["type"] == "done":
                finished.append(event)
            else:
                events.put(event)

        try:
            anyio.run(_run_turn, dialog, question, config, transport, server, factory or open_transport, emit, timeout)
            if on_done is not None:
                on_done()
            for event in finished:
                events.put(event)
        except BaseException as error:  # noqa: BLE001 — до страницы должна дойти любая причина
            events.put({"type": "error", "error": _turn_error(error, timeout)})
        finally:
            events.put(None)

    threading.Thread(target=worker, name="mcp-turn", daemon=True).start()
    while (event := events.get()) is not None:
        yield event


def run_turn(dialog: ToolDialog, question: str, **kwargs: Any) -> list[dict[str, Any]]:
    """Тот же ход, но целиком: список событий. Для тестов и простых вызовов."""
    return list(stream_turn(dialog, question, **kwargs))


async def _run_turn(
    dialog: ToolDialog,
    question: str,
    config: AgentConfig,
    transport: Transport,
    server: McpServer,
    factory: TransportFactory,
    emit: Emit,
    timeout: float,
) -> None:
    started = time.perf_counter()
    checkpoint = len(dialog.messages)
    turn_number = len(dialog.turns) + 1
    stats = {"turn": turn_number, "llm_calls": 0, "tool_calls": 0, "tool_errors": 0, "prompt_tokens": 0, "completion_tokens": 0}

    try:
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
            with anyio.fail_after(timeout * 3):
                async with Client(factory(server, errlog), client_info=CLIENT_INFO, read_timeout_seconds=timeout) as client:
                    tools, _ = await _list_tools(client)
                    specs = [tool_spec(tool) for tool in tools]
                    schema_tokens = estimate_tokens(json.dumps(specs, ensure_ascii=False))
                    emit(
                        {
                            "type": "connected",
                            "server": server.title,
                            "protocol": client.protocol_version,
                            "tools": [tool.name for tool in tools],
                            "schema_tokens": schema_tokens,
                            "ms": _ms(started),
                        }
                    )

                    dialog.messages.append({"role": "user", "content": question})
                    system = _system_text(config, server, client.instructions)
                    await _loop(dialog, config, transport, client, specs, system, stats, emit, turn_number)
    except BaseException:
        # Недоделанный ход не оставляет в истории хвостов: вызов без ответа
        # модель на следующем ходу не поймёт.
        del dialog.messages[checkpoint:]
        raise

    stats["schema_tokens"] = schema_tokens
    stats["schema_tokens_sent"] = schema_tokens * stats["llm_calls"]
    stats["ms"] = _ms(started)
    stats["messages"] = len(dialog.messages)
    dialog.turns.append(stats)
    emit({"type": "done", "stats": stats})


async def _loop(
    dialog: ToolDialog,
    config: AgentConfig,
    transport: Transport,
    client: Client,
    specs: list[dict[str, Any]],
    system: str,
    stats: dict[str, Any],
    emit: Emit,
    turn_number: int,
) -> None:
    for round_number in range(1, MAX_ROUNDS + 1):
        last = round_number == MAX_ROUNDS
        payload = _payload(config, system, dialog.messages, specs, tool_choice="none" if last else "auto")
        response = await anyio.to_thread.run_sync(transport.send, payload)
        message, finish_reason, usage = _parse(response)
        stats["llm_calls"] += 1
        stats["prompt_tokens"] += usage.get("prompt_tokens") or 0
        stats["completion_tokens"] += usage.get("completion_tokens") or 0

        calls = _tool_calls(message)
        content = message.get("content") if isinstance(message.get("content"), str) else ""

        if not calls or last:
            answer = content.strip() or f"Не уложился в {MAX_ROUNDS} круга вызовов инструментов — сформулируйте вопрос уже."
            dialog.messages.append({"role": "assistant", "content": answer})
            emit({"type": "answer", "content": answer, "round": round_number, "finish_reason": finish_reason, "usage": usage})
            return

        dialog.messages.append({"role": "assistant", "content": content, "tool_calls": calls})
        emit(
            {
                "type": "model",
                "round": round_number,
                "content": content.strip(),
                "calls": [{"id": call["id"], "name": call["function"]["name"]} for call in calls],
                "usage": usage,
            }
        )

        for call in calls:
            record = await _execute(client, call, round_number, turn_number, emit)
            stats["tool_calls"] += 1
            stats["tool_errors"] += 0 if record["ok"] else 1
            dialog.calls.append(record)
            dialog.messages.append({"role": "tool", "tool_call_id": call["id"], "content": record["for_model"]})


async def _execute(client: Client, call: dict[str, Any], round_number: int, turn_number: int, emit: Emit) -> dict[str, Any]:
    """Один tools/call. Любая неудача — не исключение, а текст ошибки для модели."""
    name = call["function"]["name"]
    raw_arguments = call["function"].get("arguments") or "{}"
    started = time.perf_counter()

    try:
        arguments = json.loads(raw_arguments)
        if not isinstance(arguments, dict):
            raise ValueError("аргументы должны быть JSON-объектом")
    except ValueError as error:
        arguments = None
        ok, text = False, f"Аргументы не разобраны как JSON: {error}"

    emit({"type": "tool_call", "id": call["id"], "name": name, "arguments": arguments if arguments is not None else raw_arguments})

    if arguments is not None:
        try:
            result = await client.call_tool(name, arguments)
            ok = not bool(getattr(result, "is_error", False))
            text = _result_text(result)
        except MCPError as error:
            ok, text = False, f"Сервер отклонил вызов: {error.code} {error.message}"

    for_model = text if len(text) <= RESULT_CHARS else text[:RESULT_CHARS] + "\n…(обрезано)"
    record = {
        "id": call["id"],
        "turn": turn_number,
        "round": round_number,
        "name": name,
        "arguments": arguments if arguments is not None else raw_arguments,
        "ok": ok,
        "chars": len(text),
        "tokens": estimate_tokens(for_model),
        "ms": _ms(started),
        "preview": text[:PREVIEW_CHARS] + ("…" if len(text) > PREVIEW_CHARS else ""),
        "for_model": for_model,
    }
    emit({"type": "tool_result", **{key: value for key, value in record.items() if key != "for_model"}})
    return record


def _result_text(result: Any) -> str:
    """Текст результата: текстовые блоки, иначе структурный ответ как JSON."""
    parts = [block.text for block in (result.content or []) if getattr(block, "type", None) == "text"]
    if parts:
        return "\n".join(parts)
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False)
    return "(пустой результат)"


def _tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    """tool_calls из ответа — в том виде, в каком их надо вернуть модели в истории."""
    calls = []
    for index, raw in enumerate(message.get("tool_calls") or []):
        function = raw.get("function") if isinstance(raw, dict) else None
        if not isinstance(function, dict) or not function.get("name"):
            continue
        calls.append(
            {
                "id": raw.get("id") or f"call_{index}",
                "type": "function",
                "function": {"name": function["name"], "arguments": function.get("arguments") or "{}"},
            }
        )
    return calls


def _payload(
    config: AgentConfig,
    system: str,
    messages: list[dict[str, Any]],
    specs: list[dict[str, Any]],
    *,
    tool_choice: str,
) -> dict[str, Any]:
    return {
        "model": config.model,
        "messages": [{"role": "system", "content": system}, *messages],
        "tools": specs,
        "tool_choice": tool_choice,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "thinking": {"type": "enabled" if config.thinking else "disabled"},
        "stream": False,
    }


def _parse(response: dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any]]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise TransportError("DeepSeek не вернул вариант ответа")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise TransportError("DeepSeek не вернул сообщение ответа")
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    return message, choices[0].get("finish_reason") or "не указан", usage


def _system_text(config: AgentConfig, server: McpServer, instructions: str | None) -> str:
    parts = [config.system_prompt or ""]
    if instructions:
        # Инструкции сервера MCP написаны для модели — отдаём их ей как есть.
        parts.append(f"Инструкции MCP-сервера «{server.title}»: {instructions}")
    return "\n\n".join(part for part in parts if part)


def _turn_error(error: BaseException, timeout: float) -> str:
    leaf = error
    while isinstance(leaf, BaseExceptionGroup) and leaf.exceptions:
        leaf = leaf.exceptions[0]
    if isinstance(leaf, TransportError):
        return str(leaf)
    return _describe(error, timeout)


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)

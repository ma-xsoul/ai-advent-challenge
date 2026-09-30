"""
Агент с инструментами MCP — день 17.

Один ход пользователя — это цикл, а не один запрос к модели:

1. агент открывает сессию MCP (свой сервер поднимается процессом) и берёт
   tools/list — схемы инструментов;
2. отдаёт модели историю и схемы в поле tools;
3. модель ответила текстом — ход окончен. Попросила tool_calls — агент
   вызывает каждый инструмент через MCP (tools/call), кладёт результат
   в историю сообщением role=tool и снова идёт к модели;
4. кругов не больше max_rounds (по умолчанию MAX_ROUNDS): на последнем инструменты запрещены
   (tool_choice=none), и модель отвечает тем, что уже собрала.

Модель по MCP не ходит: она видит схемы и просит вызов, а вызывает агент.
Поэтому весь MCP здесь — на стороне агента, а модель получает только
текст результата.

SDK асинхронный, Flask — нет. Ход целиком живёт в одном anyio.run в
отдельном потоке, события уходят наружу через очередь — так страница
видит вызовы по мере того, как они происходят.

День 20 — несколько серверов за одним агентом. Агент подключается ко
всем, и модель видит инструменты с префиксом сервера: github__search_…,
deepwiki__ask_…. Модель называет только инструмент, а на какой сервер
отправить вызов, решает агент — по префиксу, через таблицу маршрутов.
Префикс заодно разводит одинаковые имена у разных серверов.
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

from collections.abc import Sequence
from contextlib import AsyncExitStack

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

# «сервер__инструмент» — так модель видит инструменты нескольких серверов.
ROUTE_SEPARATOR = "__"

# Инструкции серверов уходят модели в системный промпт; у чужих они
# бывают на несколько тысяч символов — берём начало.
INSTRUCTIONS_CHARS = 1200


@dataclass(frozen=True)
class Route:
    """Куда отправить вызов: какой сервер, через какое соединение, под каким именем."""

    server: McpServer
    client: Client
    tool: str

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


def tool_spec(tool: ToolInfo, name: str | None = None) -> dict[str, Any]:
    """Инструмент MCP в том виде, в каком его ждёт модель: функция в поле tools."""
    return {
        "type": "function",
        "function": {"name": name or tool.name, "description": tool.description, "parameters": tool.schema},
    }


def stream_turn(
    dialog: ToolDialog,
    question: str,
    *,
    config: AgentConfig,
    transport: Transport,
    server: McpServer | None = None,
    servers: Sequence[McpServer] | None = None,
    factory: TransportFactory | None = None,
    on_done: Callable[[], None] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_rounds: int = MAX_ROUNDS,
) -> Iterator[dict[str, Any]]:
    """
    Ход в отдельном потоке, события — по мере появления. on_done зовётся в
    том же потоке после хода, даже если страница уже закрыла соединение:
    так ход, дошедший до конца, не теряется.

    server — один сервер, имена инструментов как есть (дни 17 и 19);
    servers — несколько, имена с префиксом сервера (день 20).
    """
    if (server is None) == (servers is None):
        raise ValueError("Нужен либо server, либо servers")
    namespaced = servers is not None
    targets = list(servers) if servers is not None else [server]
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
            anyio.run(
                _run_turn,
                dialog,
                question,
                config,
                transport,
                targets,
                namespaced,
                factory or open_transport,
                emit,
                timeout,
                max_rounds,
            )
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
    servers: list[McpServer],
    namespaced: bool,
    factory: TransportFactory,
    emit: Emit,
    timeout: float,
    max_rounds: int = MAX_ROUNDS,
) -> None:
    started = time.perf_counter()
    checkpoint = len(dialog.messages)
    turn_number = len(dialog.turns) + 1
    stats = {"turn": turn_number, "llm_calls": 0, "tool_calls": 0, "tool_errors": 0, "prompt_tokens": 0, "completion_tokens": 0}

    try:
        with anyio.fail_after(timeout * 3):
            async with AsyncExitStack() as stack:
                routes: dict[str, Route] = {}
                specs: list[dict[str, Any]] = []
                connected: list[dict[str, Any]] = []
                instructions: list[tuple[McpServer, str]] = []

                for server in servers:
                    connected_at = time.perf_counter()
                    errlog = stack.enter_context(tempfile.TemporaryFile(mode="w+", encoding="utf-8"))
                    try:
                        client = await stack.enter_async_context(
                            Client(factory(server, errlog), client_info=CLIENT_INFO, read_timeout_seconds=timeout)
                        )
                        tools, _ = await _list_tools(client)
                    except Exception as error:  # noqa: BLE001 — один недоступный сервер не валит ход
                        if not namespaced:
                            raise
                        connected.append(
                            {**_server_card(server), "ok": False, "error": _describe(error, timeout), "ms": _ms(connected_at)}
                        )
                        continue

                    prefix = f"{server.key}{ROUTE_SEPARATOR}" if namespaced else ""
                    own = [tool_spec(tool, prefix + tool.name) for tool in tools]
                    for tool in tools:
                        routes[prefix + tool.name] = Route(server, client, tool.name)
                    specs.extend(own)
                    if client.instructions:
                        instructions.append((server, client.instructions))
                    connected.append(
                        {
                            **_server_card(server),
                            "ok": True,
                            "protocol": client.protocol_version,
                            "tools": [prefix + tool.name for tool in tools],
                            "schema_tokens": estimate_tokens(json.dumps(own, ensure_ascii=False)),
                            "ms": _ms(connected_at),
                        }
                    )

                if not routes:
                    raise RuntimeError("Ни один MCP-сервер не ответил: " + "; ".join(card.get("error", "") for card in connected))

                schema_tokens = estimate_tokens(json.dumps(specs, ensure_ascii=False))
                alive = [card for card in connected if card["ok"]]
                emit(
                    {
                        "type": "connected",
                        "server": " + ".join(card["title"] for card in alive),
                        "protocol": ", ".join(dict.fromkeys(card["protocol"] for card in alive)),
                        "tools": list(routes),
                        "schema_tokens": schema_tokens,
                        "ms": _ms(started),
                        "servers": connected,
                    }
                )

                dialog.messages.append({"role": "user", "content": question})
                system = _system_text(config, instructions, namespaced)
                await _loop(dialog, config, transport, routes, specs, system, stats, emit, turn_number, max_rounds)
    except BaseException:
        # Недоделанный ход не оставляет в истории хвостов: вызов без ответа
        # модель на следующем ходу не поймёт.
        del dialog.messages[checkpoint:]
        raise

    stats["schema_tokens"] = schema_tokens
    stats["schema_tokens_sent"] = schema_tokens * stats["llm_calls"]
    stats["servers"] = connected
    stats["ms"] = _ms(started)
    stats["messages"] = len(dialog.messages)
    dialog.turns.append(stats)
    emit({"type": "done", "stats": stats})


def _server_card(server: McpServer) -> dict[str, Any]:
    return {"key": server.key, "title": server.title, "transport": server.transport}


async def _loop(
    dialog: ToolDialog,
    config: AgentConfig,
    transport: Transport,
    routes: dict[str, Route],
    specs: list[dict[str, Any]],
    system: str,
    stats: dict[str, Any],
    emit: Emit,
    turn_number: int,
    max_rounds: int = MAX_ROUNDS,
) -> None:
    for round_number in range(1, max_rounds + 1):
        last = round_number == max_rounds
        payload = _payload(config, system, dialog.messages, specs, tool_choice="none" if last else "auto")
        response = await anyio.to_thread.run_sync(transport.send, payload)
        message, finish_reason, usage = _parse(response)
        stats["llm_calls"] += 1
        stats["prompt_tokens"] += usage.get("prompt_tokens") or 0
        stats["completion_tokens"] += usage.get("completion_tokens") or 0

        calls = _tool_calls(message)
        content = message.get("content") if isinstance(message.get("content"), str) else ""

        if not calls or last:
            answer = _without_leaked_calls(content).strip() or (
                f"Не уложился в {max_rounds} кругов вызовов инструментов: модель хотела вызвать ещё, "
                "но круги кончились. Сформулируйте вопрос уже или разбейте его на части."
            )
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
            record = await _execute(routes, call, round_number, turn_number, emit)
            stats["tool_calls"] += 1
            stats["tool_errors"] += 0 if record["ok"] else 1
            dialog.calls.append(record)
            dialog.messages.append({"role": "tool", "tool_call_id": call["id"], "content": record["for_model"]})


async def _execute(
    routes: dict[str, Route], call: dict[str, Any], round_number: int, turn_number: int, emit: Emit
) -> dict[str, Any]:
    """Один tools/call — на тот сервер, которому принадлежит инструмент. Любая неудача — текст ошибки для модели."""
    name = call["function"]["name"]
    route = routes.get(name)
    raw_arguments = call["function"].get("arguments") or "{}"
    started = time.perf_counter()

    try:
        arguments = json.loads(raw_arguments)
        if not isinstance(arguments, dict):
            raise ValueError("аргументы должны быть JSON-объектом")
    except ValueError as error:
        arguments = None
        ok, text = False, f"Аргументы не разобраны как JSON: {error}"

    server_key = route.server.key if route else None
    emit(
        {
            "type": "tool_call",
            "id": call["id"],
            "name": name,
            "server": server_key,
            "arguments": arguments if arguments is not None else raw_arguments,
        }
    )

    if arguments is not None and route is None:
        # Модель выдумала инструмент или перепутала префикс — маршрута нет.
        ok, text = False, f"Нет такого инструмента: {name}. Доступны: {', '.join(routes)}"
    elif arguments is not None:
        try:
            result = await route.client.call_tool(route.tool, arguments)
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
        "server": server_key,
        "tool": route.tool if route else name,
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


# Когда инструменты запрещены (последний круг), DeepSeek иногда всё равно
# «вызывает» их — прямо в тексте ответа, своей служебной разметкой. Такой
# хвост пользователю не показываем.
_LEAKED_CALL_MARKERS = ("<｜｜DSML｜｜", "<｜DSML｜", "<|DSML|")


def _without_leaked_calls(content: str) -> str:
    cut = min((content.find(marker) for marker in _LEAKED_CALL_MARKERS if marker in content), default=-1)
    return content if cut < 0 else content[:cut]


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


def _system_text(config: AgentConfig, instructions: list[tuple[McpServer, str]], namespaced: bool) -> str:
    parts = [config.system_prompt or ""]
    if namespaced:
        parts.append(
            "Инструменты названы «сервер__инструмент»: префикс — MCP-сервер, на котором инструмент живёт. "
            "В инструкциях серверов ниже те же инструменты упомянуты без префикса."
        )
    for server, text in instructions:
        # Инструкции сервера MCP написаны для модели — отдаём их ей, у длинных — начало.
        cut = text if len(text) <= INSTRUCTIONS_CHARS else text[:INSTRUCTIONS_CHARS] + "…"
        parts.append(f"Инструкции MCP-сервера «{server.title}»: {cut}")
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

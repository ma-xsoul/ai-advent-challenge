"""
Клиент MCP: подключиться к серверу инструментов и спросить, что он умеет.

Сам протокол реализует официальный SDK (`mcp`), здесь — только то, что
нужно лаборатории: реестр общедоступных серверов, один проход «соединение →
tools/list» и журнал всего, что ушло и пришло по проводу. Журнал пишет
обёртка вокруг транспорта SDK: она видит каждое JSON-RPC сообщение в обе
стороны, ничего в них не меняя.

Вызовов инструментов здесь нет — это день 17. Схемы инструментов только
показываются и взвешиваются: столько токенов они займут в каждом запросе,
если отдать их модели.
"""

from __future__ import annotations

import json
import shlex
import shutil
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import asdict, dataclass, field
from typing import Any

import anyio
import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import Implementation

from ..agent.tokens import estimate_tokens


# Сколько ждать весь проход. Первый запуск stdio-сервера через npx качает
# пакет, поэтому запас на загрузку.
DEFAULT_TIMEOUT_SECONDS = 60.0

# Так клиент представляется серверу в рукопожатии.
CLIENT_INFO = Implementation(name="ai-advent", version="16")

# Страниц tools/list больше этого не бывает у честного сервера.
MAX_TOOL_PAGES = 20

# Хвост stderr stdio-сервера — для диагностики, не для чтения целиком.
STDERR_LINES = 20

TRANSPORTS = ("http", "stdio")


@dataclass(frozen=True)
class McpServer:
    """Общедоступный MCP-сервер из реестра лаборатории."""

    key: str
    title: str
    transport: str
    target: str
    note: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


SERVERS = (
    McpServer(
        key="deepwiki",
        title="DeepWiki",
        transport="http",
        target="https://mcp.deepwiki.com/mcp",
        note="Публичный сервер в интернете: вопросы по любому GitHub-репозиторию. Без ключа и регистрации.",
    ),
    McpServer(
        key="everything",
        title="Everything",
        transport="stdio",
        target="npx -y @modelcontextprotocol/server-everything",
        note="Эталонный сервер от авторов протокола. Клиент сам запускает его процессом и говорит через stdin/stdout.",
    ),
)


def get_server(key: str) -> McpServer | None:
    return next((server for server in SERVERS if server.key == key), None)


@dataclass(frozen=True)
class WireMessage:
    """Одно JSON-RPC сообщение: куда шло, когда и что в нём."""

    direction: str  # "out" — клиент → сервер, "in" — сервер → клиент
    at_ms: int
    kind: str  # request / response / error / notification / transport
    method: str | None
    id: int | str | None
    summary: str
    body: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolParam:
    name: str
    type: str
    required: bool
    description: str


@dataclass(frozen=True)
class ToolInfo:
    name: str
    title: str | None
    description: str
    params: tuple[ToolParam, ...]
    schema: dict[str, Any]
    tokens: int

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["params"] = [asdict(param) for param in self.params]
        return data


@dataclass(frozen=True)
class Inspection:
    """Итог одного прохода: соединение, сервер, инструменты и журнал."""

    server: McpServer
    ok: bool
    error: str | None
    elapsed_ms: int
    protocol_version: str | None = None
    server_name: str | None = None
    server_version: str | None = None
    server_title: str | None = None
    instructions: str | None = None
    capabilities: tuple[str, ...] = ()
    tools: tuple[ToolInfo, ...] = ()
    pages: int = 0
    wire: tuple[WireMessage, ...] = ()
    stderr: str = ""

    @property
    def schema_tokens(self) -> int:
        return sum(tool.tokens for tool in self.tools)

    def as_dict(self) -> dict[str, Any]:
        return {
            "server": self.server.as_dict(),
            "ok": self.ok,
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
            "protocol_version": self.protocol_version,
            "server_name": self.server_name,
            "server_version": self.server_version,
            "server_title": self.server_title,
            "instructions": self.instructions,
            "capabilities": list(self.capabilities),
            "tools": [tool.as_dict() for tool in self.tools],
            "pages": self.pages,
            "schema_tokens": self.schema_tokens,
            "required_params": sum(param.required for tool in self.tools for param in tool.params),
            "wire": [message.as_dict() for message in self.wire],
            "stderr": self.stderr,
        }


TransportFactory = Callable[[McpServer, Any], AbstractAsyncContextManager[Any]]


def inspect_server(
    server: McpServer,
    *,
    transport_factory: TransportFactory | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Inspection:
    """Подключиться, пройти рукопожатие, забрать tools/list. Не бросает: ошибка — в Inspection."""
    return anyio.run(_inspect, server, transport_factory or open_transport, timeout)


def open_transport(server: McpServer, errlog: Any) -> AbstractAsyncContextManager[Any]:
    """Транспорт SDK под запись сервера: HTTP по адресу или процесс по команде."""
    if server.transport == "http":
        return streamable_http_client(server.target)
    if server.transport == "stdio":
        command, *args = shlex.split(server.target)
        executable = shutil.which(command)
        if executable is None:
            raise FileNotFoundError(f"«{command}» не найден в PATH — для stdio-сервера нужен Node.js")
        return stdio_client(StdioServerParameters(command=executable, args=args), errlog=errlog)
    raise ValueError(f"Неизвестный транспорт: {server.transport}")


async def _inspect(server: McpServer, factory: TransportFactory, timeout: float) -> Inspection:
    wire = _Wire()
    started = time.perf_counter()
    facts: dict[str, Any] = {}
    error: str | None = None

    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errlog:
        try:
            with anyio.fail_after(timeout):
                transport = factory(server, errlog)
                async with Client(_logged(transport, wire), client_info=CLIENT_INFO, read_timeout_seconds=timeout) as client:
                    facts["protocol_version"] = client.protocol_version
                    info = client.server_info
                    if info is not None:
                        facts["server_name"] = info.name
                        facts["server_version"] = info.version
                        facts["server_title"] = info.title
                    facts["instructions"] = client.instructions
                    facts["capabilities"] = _capabilities(client.server_capabilities)
                    facts["tools"], facts["pages"] = await _list_tools(client)
        except BaseException as caught:  # noqa: BLE001 — anyio заворачивает всё в группы
            if isinstance(caught, (KeyboardInterrupt, SystemExit)):
                raise
            error = _describe(caught, timeout)

        errlog.seek(0)
        stderr = "\n".join(errlog.read().strip().splitlines()[-STDERR_LINES:])

    return Inspection(
        server=server,
        ok=error is None,
        error=error,
        elapsed_ms=round((time.perf_counter() - started) * 1000),
        protocol_version=facts.get("protocol_version"),
        server_name=facts.get("server_name"),
        server_version=facts.get("server_version"),
        server_title=facts.get("server_title"),
        instructions=facts.get("instructions"),
        capabilities=tuple(facts.get("capabilities", ())),
        tools=tuple(facts.get("tools", ())),
        pages=facts.get("pages", 0),
        wire=tuple(wire.messages),
        stderr=stderr,
    )


async def _list_tools(client: Client) -> tuple[list[ToolInfo], int]:
    """tools/list со всеми страницами: сервер вправе отдавать список кусками."""
    tools: list[ToolInfo] = []
    cursor: str | None = None
    pages = 0
    while pages < MAX_TOOL_PAGES:
        result = await client.list_tools(cursor=cursor)
        pages += 1
        tools.extend(_tool_info(tool.model_dump(by_alias=True, exclude_none=True, mode="json")) for tool in result.tools)
        cursor = result.next_cursor
        if not cursor:
            break
    return tools, pages


def _tool_info(raw: dict[str, Any]) -> ToolInfo:
    schema = raw.get("inputSchema") or {"type": "object", "properties": {}}
    required = set(schema.get("required") or ())
    params = tuple(
        ToolParam(
            name=name,
            type=_param_type(spec),
            required=name in required,
            description=str(spec.get("description") or ""),
        )
        for name, spec in (schema.get("properties") or {}).items()
        if isinstance(spec, dict)
    )
    description = str(raw.get("description") or "")
    return ToolInfo(
        name=raw["name"],
        title=raw.get("title"),
        description=description,
        params=params,
        schema=schema,
        tokens=_tool_tokens(raw["name"], description, schema),
    )


def _tool_tokens(name: str, description: str, schema: dict[str, Any]) -> int:
    """Вес инструмента так, как его увидит модель: функция в поле tools запроса."""
    spec = {"type": "function", "function": {"name": name, "description": description, "parameters": schema}}
    return estimate_tokens(json.dumps(spec, ensure_ascii=False))


def _param_type(spec: dict[str, Any]) -> str:
    kind = spec.get("type")
    if isinstance(kind, list):
        return " | ".join(str(item) for item in kind)
    if kind:
        if kind == "array" and isinstance(spec.get("items"), dict) and spec["items"].get("type"):
            return f"{spec['items']['type']}[]"
        return str(kind)
    variants = spec.get("anyOf") or spec.get("oneOf")
    if isinstance(variants, list):
        names = [_param_type(item) for item in variants if isinstance(item, dict)]
        return " | ".join(name for name in names if name != "null") or "any"
    if "enum" in spec:
        return "enum"
    return "any"


def _capabilities(capabilities: Any) -> list[str]:
    data = capabilities.model_dump(by_alias=True, exclude_none=True, mode="json")
    return [name for name, value in data.items() if not (name == "experimental" and not value)]


def _describe(error: BaseException, timeout: float) -> str:
    leaf = _leaf(error)
    if isinstance(leaf, TimeoutError):
        return f"сервер не ответил за {timeout:.0f} с"
    if isinstance(leaf, MCPError):
        return f"сервер вернул ошибку {leaf.error.code}: {leaf.error.message}"
    if isinstance(leaf, FileNotFoundError):
        return str(leaf)
    if isinstance(leaf, httpx2.TransportError):
        return f"нет связи с сервером: {leaf}"
    text = str(leaf) or type(leaf).__name__
    return f"{type(leaf).__name__}: {text}"


def _leaf(error: BaseException) -> BaseException:
    """anyio складывает исключения задач в группы — достаём первое настоящее."""
    while isinstance(error, BaseExceptionGroup) and error.exceptions:
        error = error.exceptions[0]
    return error


# ── Журнал провода ───────────────────────────────────────────────────────


@dataclass
class _Wire:
    started: float = field(default_factory=time.perf_counter)
    messages: list[WireMessage] = field(default_factory=list)
    methods: dict[Any, str] = field(default_factory=dict)

    def record(self, direction: str, item: Any) -> None:
        at_ms = round((time.perf_counter() - self.started) * 1000)
        if isinstance(item, BaseException):
            self.messages.append(
                WireMessage(direction, at_ms, "transport", None, None, f"сбой транспорта: {item}", {"error": str(item)})
            )
            return

        body = item.message.model_dump(by_alias=True, exclude_none=True, mode="json")
        message_id = body.get("id")
        method = body.get("method")

        if method and message_id is not None:
            self.methods[message_id] = method
            kind = "request"
        elif method:
            kind = "notification"
        elif "error" in body:
            kind = "error"
            method = self.methods.get(message_id)
        else:
            kind = "response"
            method = self.methods.get(message_id)

        self.messages.append(
            WireMessage(direction, at_ms, kind, method, message_id, _summary(kind, method, body), body)
        )


def _summary(kind: str, method: str | None, body: dict[str, Any]) -> str:
    """Одна строка по-человечески: что это было и чем кончилось."""
    if kind == "request":
        params = body.get("params") or {}
        version = params.get("protocolVersion") or (params.get("_meta") or {}).get(
            "io.modelcontextprotocol/protocolVersion"
        )
        return f"{method} · протокол {version}" if version else str(method)

    if kind == "notification":
        return str(method)

    if kind == "error":
        error = body.get("error") or {}
        return f"отказ на {method or 'запрос'}: {error.get('code')} {error.get('message', '')}".strip()

    result = body.get("result") or {}
    if method == "initialize":
        info = result.get("serverInfo") or {}
        return f"ответ на initialize · {info.get('name', '?')} {info.get('version', '')} · протокол {result.get('protocolVersion')}".strip()
    if method == "server/discover":
        info = (result.get("_meta") or {}).get("io.modelcontextprotocol/serverInfo") or {}
        versions = ", ".join(result.get("supportedVersions") or ()) or "?"
        name = f"{info.get('name', '?')} {info.get('version', '')}".strip()
        return f"ответ на server/discover · {name} · версии {versions}"
    if method == "tools/list":
        tools = result.get("tools") or []
        more = " · есть ещё страница" if result.get("nextCursor") else ""
        return f"ответ на tools/list · инструментов: {len(tools)}{more}"
    return f"ответ на {method}" if method else "ответ"


@asynccontextmanager
async def _logged(transport: AbstractAsyncContextManager[Any], wire: _Wire) -> AsyncIterator[Any]:
    """Транспорт SDK, у которого каждое сообщение пишется в журнал."""
    async with transport as (read_stream, write_stream):
        yield _LoggedRead(read_stream, wire), _LoggedWrite(write_stream, wire)


class _LoggedRead:
    def __init__(self, inner: Any, wire: _Wire) -> None:
        self._inner = inner
        self._wire = wire

    async def receive(self) -> Any:
        item = await self._inner.receive()
        self._wire.record("in", item)
        return item

    def __aiter__(self) -> _LoggedRead:
        return self

    async def __anext__(self) -> Any:
        try:
            return await self.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def __aenter__(self) -> _LoggedRead:
        return self

    async def __aexit__(self, *exc_info: Any) -> bool | None:
        return await self._inner.__aexit__(*exc_info)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _LoggedWrite:
    def __init__(self, inner: Any, wire: _Wire) -> None:
        self._inner = inner
        self._wire = wire

    async def send(self, item: Any) -> None:
        self._wire.record("out", item)
        await self._inner.send(item)

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def __aenter__(self) -> _LoggedWrite:
        return self

    async def __aexit__(self, *exc_info: Any) -> bool | None:
        return await self._inner.__aexit__(*exc_info)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

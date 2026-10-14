"""
Приватный сервис на локальной LLM — день 30.

Ollama слушает только localhost и пускает всех без ключа, поэтому наружу
её не открываем. Наружу смотрит обёртка (advent/local/server.py), и у
каждого запроса один путь:

1. ключ — без ключа из data/day30/keys.json ответ 401;
2. max context — слишком длинное сообщение или вся история диалога — 413;
3. rate limit — больше N запросов в минуту на ключ — 429 и через сколько
   секунд можно снова;
4. очередь — модель на маке одна и считает один ответ за раз: запрос
   встаёт в очередь и видит, сколько перед ним; очередь переполнена — 503;
5. ответ — gpt-oss через Ollama потоком; размер окна и длина ответа
   ограничены параметрами модели.

Журнал событий и снимок очереди отдаются странице дня 30 по ключу
администратора — так видно, кто и откуда сейчас пользуется моделью.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .ollama import OllamaClient, OllamaError


MODEL = "gpt-oss:20b"
PORT = 8030
KEYS_PATH = Path(__file__).resolve().parents[2] / "data" / "day30" / "keys.json"
HEARTBEAT_S = 2.0

SYSTEM_PROMPT = (
    "Ты — ассистент приватного сервиса. Работаешь на локальной модели на компьютере владельца, "
    "без облака. Отвечай на языке пользователя, по делу и коротко, если не просят подробно."
)


@dataclass(frozen=True)
class Limits:
    """Ограничения сервиса. Контекст меряем символами: токенизатора gpt-oss у нас нет."""

    rate_per_minute: int = 5
    max_message_chars: int = 2000
    max_context_chars: int = 12000
    max_messages: int = 30
    queue_size: int = 8
    num_ctx: int = 8192
    max_answer_tokens: int = 1500
    temperature: float = 0.6

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ServiceError(Exception):
    """Отказ с HTTP-кодом и понятной причиной."""

    def __init__(self, status: int, code: str, message: str, *, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.message, "code": self.code}
        if self.retry_after is not None:
            body["retry_after"] = self.retry_after
        return body


# ── ключи ────────────────────────────────────────────────────────────────


NAME = re.compile(r"^[\w .\-]{1,32}$")


class KeyStore:
    """
    data/day30/keys.json — ключи клиентов и ключ администратора. Файл общий:
    страница дня 30 выдаёт и отзывает ключи, сервис читает их на каждом
    запросе — отозванный ключ перестаёт работать сразу.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        keys = [item for item in data.get("keys") or [] if isinstance(item, dict) and item.get("key") and item.get("name")]
        return {"admin": str(data.get("admin") or ""), "keys": keys}

    def ensure(self) -> dict[str, Any]:
        """Ключ администратора появляется при первом запуске."""
        with self._lock:
            data = self.load()
            if not data["admin"]:
                data["admin"] = "adm-" + secrets.token_hex(16)
                self._save(data)
            return data

    def admin(self) -> str:
        return self.ensure()["admin"]

    def keys(self) -> list[dict[str, Any]]:
        return self.load()["keys"]

    def find(self, key: str) -> dict[str, Any] | None:
        key = key.strip()
        if not key:
            return None
        for item in self.keys():
            if hmac.compare_digest(item["key"].encode(), key.encode()):
                return item
        return None

    def add(self, name: str, *, test: bool = False) -> dict[str, Any]:
        name = " ".join(name.split())
        if not NAME.match(name):
            raise ValueError("Имя ключа — от 1 до 32 символов: буквы, цифры, пробел, точка, дефис")
        with self._lock:
            data = self.load()
            if not data["admin"]:
                data["admin"] = "adm-" + secrets.token_hex(16)
            if any(item["name"].casefold() == name.casefold() for item in data["keys"]):
                raise ValueError(f"Ключ «{name}» уже есть")
            item = {
                "name": name,
                "key": "sk-" + secrets.token_hex(8),
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "test": test,
            }
            data["keys"].append(item)
            self._save(data)
            return item

    def remove(self, name: str) -> bool:
        with self._lock:
            data = self.load()
            kept = [item for item in data["keys"] if item["name"] != name]
            if len(kept) == len(data["keys"]):
                return False
            data["keys"] = kept
            self._save(data)
            return True

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self.path)


# ── rate limit ───────────────────────────────────────────────────────────


class RateLimiter:
    """Скользящее окно в 60 секунд на каждого клиента."""

    WINDOW_S = 60.0

    def __init__(self, per_minute: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_minute = per_minute
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, client: str) -> int | None:
        """None — запрос засчитан; число — через сколько секунд освободится место."""
        with self._lock:
            now = self.clock()
            hits = self._fresh(client, now)
            if len(hits) >= self.per_minute:
                return max(1, int(hits[0] + self.WINDOW_S - now + 0.999))
            hits.append(now)
            return None

    def used(self, client: str) -> int:
        with self._lock:
            return len(self._fresh(client, self.clock()))

    def _fresh(self, client: str, now: float) -> deque[float]:
        hits = self._hits.setdefault(client, deque())
        while hits and hits[0] <= now - self.WINDOW_S:
            hits.popleft()
        return hits


# ── очередь к модели ─────────────────────────────────────────────────────


@dataclass(eq=False)
class Ticket:
    client: str
    origin: dict[str, str]
    entered: float
    id: str = field(default_factory=lambda: secrets.token_hex(4))
    started: float | None = None


class ModelQueue:
    """
    Очередь «по одному»: первый в списке говорит с моделью, остальные ждут.
    Место в очереди — сколько запросов перед тобой (0 — твоя очередь).
    """

    def __init__(self, size: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.size = size
        self.clock = clock
        self._line: list[Ticket] = []
        self._changed = threading.Condition()
        self.peak = 0

    def enter(self, client: str, origin: dict[str, str]) -> Ticket:
        with self._changed:
            if len(self._line) > self.size:
                raise ServiceError(503, "queue_full", f"Очередь переполнена: ждут уже {len(self._line) - 1}. Попробуйте позже", retry_after=10)
            ticket = Ticket(client, origin, self.clock())
            self._line.append(ticket)
            self.peak = max(self.peak, len(self._line))
            return ticket

    def position(self, ticket: Ticket) -> int:
        with self._changed:
            return self._line.index(ticket)

    def wait(self, ticket: Ticket, timeout: float) -> int:
        """Ждёт своей очереди не дольше timeout; возвращает место (0 — пора)."""
        with self._changed:
            self._changed.wait_for(lambda: self._line[0] is ticket, timeout)
            return self._line.index(ticket)

    def start(self, ticket: Ticket) -> float:
        with self._changed:
            ticket.started = self.clock()
            return ticket.started - ticket.entered

    def leave(self, ticket: Ticket) -> None:
        with self._changed:
            if ticket in self._line:
                self._line.remove(ticket)
            self._changed.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._changed:
            now = self.clock()
            line = [
                {
                    "id": ticket.id,
                    "client": ticket.client,
                    "origin": ticket.origin,
                    "position": number,
                    "waiting_s": round(now - ticket.entered, 1),
                    "answering_s": round(now - ticket.started, 1) if ticket.started is not None else None,
                }
                for number, ticket in enumerate(self._line)
            ]
        return {"active": line[0] if line and line[0]["answering_s"] is not None else None,
                "waiting": [item for item in line if item["answering_s"] is None],
                "size": self.size, "peak": self.peak}


# ── журнал ───────────────────────────────────────────────────────────────


class EventLog:
    """Последние события сервиса — для страницы дня 30."""

    def __init__(self, keep: int = 60) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=keep)
        self._lock = threading.Lock()
        self._seq = 0
        self.counts: dict[str, int] = {}

    def add(self, kind: str, client: str, origin: dict[str, str], **details: Any) -> None:
        with self._lock:
            self._seq += 1
            self.counts[kind] = self.counts.get(kind, 0) + 1
            if kind == "rejected":
                key = f"rejected_{details.get('status')}"
                self.counts[key] = self.counts.get(key, 0) + 1
            self._events.append({
                "seq": self._seq,
                "at": datetime.now().astimezone().strftime("%H:%M:%S"),
                "kind": kind,
                "client": client,
                "origin": origin,
                **details,
            })

    def recent(self, after: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [event for event in self._events if event["seq"] > after]


# ── откуда пришёл запрос ─────────────────────────────────────────────────


DEVICES = (
    ("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Macintosh", "Mac"),
    ("Windows", "Windows"), ("Linux", "Linux"), ("curl/", "curl"), ("python-requests", "Python"),
)


def origin_of(headers: Any, remote_addr: str | None) -> dict[str, str]:
    """
    Через туннель адрес клиента приходит заголовком (CF-Connecting-IP у Cloudflare,
    X-Forwarded-For у остальных); без туннеля — это localhost. В журнал —
    устройство и адрес без последних цифр.
    """
    agent = str(headers.get("User-Agent") or "")
    device = next((title for marker, title in DEVICES if marker in agent), "клиент")
    forwarded = str(headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    tunneled = headers.get("CF-Connecting-IP") or forwarded
    address = _mask(str(tunneled)) if tunneled else str(remote_addr or "?")
    return {"device": device, "address": address, "via": "интернет" if tunneled else "localhost"}


def _mask(address: str) -> str:
    if address.count(".") == 3:
        return ".".join(address.split(".")[:2] + ["x", "x"])
    if ":" in address and address not in ("::1",):
        return ":".join(address.split(":")[:2]) + ":…"
    return address


# ── сам сервис ───────────────────────────────────────────────────────────


class PrivateService:
    def __init__(
        self,
        *,
        keys: KeyStore,
        client: OllamaClient | None = None,
        model: str = MODEL,
        limits: Limits = Limits(),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.keys = keys
        self.client = client or OllamaClient()
        self.model = model
        self.limits = limits
        self.rate = RateLimiter(limits.rate_per_minute, clock=clock)
        self.queue = ModelQueue(limits.queue_size, clock=clock)
        self.log = EventLog()
        self.clock = clock
        self.public_url: str | None = None
        self.started_at = datetime.now().astimezone().isoformat(timespec="seconds")

    # ── проверки до очереди ──────────────────────────────────────────────

    def authorize(self, header: str | None, origin: dict[str, str]) -> dict[str, Any]:
        token = (header or "").removeprefix("Bearer ").strip()
        found = self.keys.find(token)
        if found is None:
            self.log.add("rejected", "?", origin, status=401, reason="нет ключа или ключ неверный")
            raise ServiceError(401, "bad_key", "Нужен ключ доступа: заголовок Authorization: Bearer <ключ>")
        return found

    def messages(self, data: Any, client: str, origin: dict[str, str]) -> list[dict[str, str]]:
        """Тело запроса: {"messages": [...]} как у OpenAI или короче — {"message": "..."}."""
        try:
            messages = _messages(data)
        except ValueError as error:
            self.log.add("rejected", client, origin, status=400, reason=str(error))
            raise ServiceError(400, "bad_request", str(error)) from error
        limits = self.limits
        longest = max(len(message["content"]) for message in messages)
        total = sum(len(message["content"]) for message in messages)
        problem = None
        if longest > limits.max_message_chars:
            problem = f"Сообщение длиннее {limits.max_message_chars} символов ({longest})"
        elif total > limits.max_context_chars:
            problem = f"Диалог длиннее {limits.max_context_chars} символов ({total}) — начните новый"
        elif len(messages) > limits.max_messages:
            problem = f"В диалоге больше {limits.max_messages} сообщений — начните новый"
        if problem:
            self.log.add("rejected", client, origin, status=413, reason=problem)
            raise ServiceError(413, "context_too_long", problem)
        return messages

    def admit(self, client: str, origin: dict[str, str]) -> Ticket:
        retry = self.rate.hit(client)
        if retry is not None:
            reason = f"больше {self.limits.rate_per_minute} запросов в минуту"
            self.log.add("rejected", client, origin, status=429, reason=reason, retry_after=retry)
            raise ServiceError(429, "rate_limited", f"Лимит: {self.limits.rate_per_minute} запросов в минуту. Попробуйте через {retry} с", retry_after=retry)
        try:
            ticket = self.queue.enter(client, origin)
        except ServiceError as error:
            self.log.add("rejected", client, origin, status=503, reason="очередь переполнена")
            raise error
        self.log.add("queued", client, origin, position=self.queue.position(ticket))
        return ticket

    # ── очередь и ответ ──────────────────────────────────────────────────

    def run(self, ticket: Ticket, messages: list[dict[str, str]]) -> Iterator[dict[str, Any]]:
        """
        События: queued (место, пока ждём; повторяется раз в пару секунд) →
        start → delta × N → done. Клиент ушёл — finally освобождает очередь.
        """
        finished = False
        try:
            position = self.queue.position(ticket)
            yield {"type": "queued", "position": position}
            while position > 0:
                position = self.queue.wait(ticket, HEARTBEAT_S)
                yield {"type": "queued", "position": position}
            waited = self.queue.start(ticket)
            self.log.add("start", ticket.client, ticket.origin, waited_s=round(waited, 1))
            yield {"type": "start", "waited_s": round(waited, 1)}
            answer: list[str] = []
            options = {"num_ctx": self.limits.num_ctx, "num_predict": self.limits.max_answer_tokens, "temperature": self.limits.temperature}
            history = [{"role": "system", "content": SYSTEM_PROMPT}, *messages]
            for event in self.client.chat_stream(self.model, history, think="low", options=options):
                if event["type"] == "delta":
                    answer.append(event["text"])
                    yield {"type": "delta", "text": event["text"]}
                elif event["type"] == "done":
                    if not "".join(answer).strip():
                        yield {"type": "delta", "text": "_(модель не успела ответить — весь лимит ушёл на размышления)_"}
                    done = {key: event.get(key) for key in ("output_tokens", "output_tps", "prompt_tokens", "load_s", "wall_s", "done_reason")}
                    self.log.add("done", ticket.client, ticket.origin, waited_s=round(waited, 1), answer_s=event.get("wall_s"),
                                 tokens=event.get("output_tokens"), truncated=event.get("done_reason") == "length")
                    finished = True
                    yield {"type": "done", "waited_s": round(waited, 1), **done}
            if not finished:
                raise OllamaError("Ollama оборвала ответ")
        except OllamaError as error:
            self.log.add("error", ticket.client, ticket.origin, reason=str(error))
            finished = True
            yield {"type": "error", "error": str(error), "code": "model_error"}
        finally:
            self.queue.leave(ticket)
            if not finished:
                self.log.add("cancelled", ticket.client, ticket.origin, reason="клиент отключился")

    def status(self) -> dict[str, Any]:
        clients = [
            {"name": item["name"], "test": bool(item.get("test")), "used": self.rate.used(item["name"])}
            for item in self.keys.keys()
        ]
        return {
            "model": self.model,
            "limits": self.limits.as_dict(),
            "queue": self.queue.snapshot(),
            "clients": clients,
            "counts": dict(self.log.counts),
            "public_url": self.public_url,
            "started_at": self.started_at,
        }


def _messages(data: Any) -> list[dict[str, str]]:
    if not isinstance(data, dict):
        raise ValueError("Тело запроса — JSON-объект: {\"message\": \"...\"} или {\"messages\": [...]}")
    if "messages" in data:
        raw = data["messages"]
        if not isinstance(raw, list) or not raw:
            raise ValueError("messages — непустой список {role, content}")
    else:
        raw = [{"role": "user", "content": data.get("message")}]
    messages = []
    for item in raw:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            raise ValueError("Каждое сообщение — {role: user | assistant, content: текст}")
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Пустое сообщение")
        messages.append({"role": item["role"], "content": content})
    if messages[-1]["role"] != "user":
        raise ValueError("Последнее сообщение должно быть от user")
    return messages

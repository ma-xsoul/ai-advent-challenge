"""
MCP-сервер CRM поддержки — день 33.

CRM на коленке, как советовал куратор: один JSON с клиентами приватного
сервиса дня 30, их ключами, тарифами и журналом запросов, тикетами с
перепиской и состоянием самого сервиса. Исходные данные — crm_seed.json
рядом; рабочая копия — data/day33/crm.json (ADVENT_CRM_PATH): ответы в
тикеты пишутся туда, а репозиторий не меняется.

Инструменты:

- list_tickets — тикеты по статусу: кто, о чём, когда;
- get_ticket — тикет с перепиской и карточкой клиента;
- get_client — клиент по id или email: тариф и его лимит, статус ключа;
- client_events — последние запросы клиента к сервису: коды ответов и
  подробности — главное, чтобы понять, что у него на самом деле;
- service_status — адрес сервиса сейчас, когда он менялся, инциденты;
- reply_to_ticket — записать ответ поддержки в тикет и сменить статус.
  Его зовёт оператор кнопкой, а не модель сама.

Запуск: python -m advent.mcp.servers.crm (stdio).
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field


SEED = Path(__file__).with_name("crm_seed.json")
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "day33" / "crm.json"

TicketId = Annotated[str, Field(pattern=r"^T-\d{1,6}$", description="Номер тикета, например T-101")]


class CrmError(ToolError):
    """Нет такого тикета или клиента. Наследник ToolError — текст дойдёт до модели."""


class CrmStore:
    """JSON-файл CRM. При первом обращении — копия исходных данных."""

    def __init__(self, path: str | Path, seed: Path = SEED) -> None:
        self.path = Path(path)
        self.seed = seed
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            self.reset()
        return json.loads(self.path.read_text(encoding="utf-8"))

    def reset(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.seed, self.path)

    def save(self, data: dict[str, Any]) -> None:
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self.path)

    # ── чтение ───────────────────────────────────────────────────────────

    def client(self, data: dict[str, Any], key: str) -> dict[str, Any]:
        key = key.strip().lower()
        found = next((item for item in data["clients"] if item["id"].lower() == key or item["email"].lower() == key), None)
        if found is None:
            raise CrmError(f"Нет клиента {key}")
        return found

    def ticket(self, data: dict[str, Any], ticket_id: str) -> dict[str, Any]:
        found = next((item for item in data["tickets"] if item["id"] == ticket_id), None)
        if found is None:
            raise CrmError(f"Нет тикета {ticket_id}")
        return found

    def card(self, data: dict[str, Any], client: dict[str, Any]) -> dict[str, Any]:
        """Клиент без журнала — тариф с его лимитом и ключ без самого ключа (его в CRM и нет)."""
        plan = data["plans"].get(client["plan"], {})
        return {
            "id": client["id"],
            "name": client["name"],
            "email": client["email"],
            "plan": client["plan"],
            "rate_per_minute": plan.get("rate_per_minute"),
            "since": client["since"],
            "key": client["key"],
        }


def build_server(store: CrmStore) -> MCPServer:
    server = MCPServer(
        "advent-crm",
        version="33",
        instructions=(
            "CRM поддержки приватного AI-сервиса. По тикету начинай с get_ticket — там переписка и клиент. "
            "Причину проблемы ищи в client_events: коды ответов 401, 413, 429, 503 и подробности; "
            "если сайт не открывается или долго отвечает — смотри service_status. "
            "reply_to_ticket вызывай только по прямой просьбе оператора: ответ клиенту отправляет человек."
        ),
    )

    @server.tool(title="Тикеты")
    def list_tickets(
        status: Annotated[Literal["open", "answered", "closed", "all"], Field(description="Какие тикеты: open, answered, closed или all")] = "open",
    ) -> dict[str, Any]:
        """Список тикетов: номер, клиент, тема, статус, когда создан."""
        data = store.load()
        names = {client["id"]: client["name"] for client in data["clients"]}
        tickets = [
            {"id": item["id"], "client": item["client"], "client_name": names.get(item["client"]), "subject": item["subject"],
             "status": item["status"], "created_at": item["created_at"]}
            for item in data["tickets"] if status == "all" or item["status"] == status
        ]
        return {"status": status, "tickets": tickets}

    @server.tool(title="Тикет")
    def get_ticket(ticket_id: TicketId) -> dict[str, Any]:
        """Тикет целиком: тема, статус, переписка и карточка клиента — тариф, лимит, статус ключа."""
        data = store.load()
        ticket = store.ticket(data, ticket_id)
        return {**ticket, "client": store.card(data, store.client(data, ticket["client"]))}

    @server.tool(title="Клиент")
    def get_client(client: Annotated[str, Field(max_length=100, description="id клиента (C-1) или email")]) -> dict[str, Any]:
        """Карточка клиента: тариф и его лимит запросов в минуту, с какого числа клиент, статус ключа и почему он такой."""
        data = store.load()
        return store.card(data, store.client(data, client))

    @server.tool(title="Журнал запросов клиента")
    def client_events(
        client: Annotated[str, Field(max_length=100, description="id клиента (C-1) или email")],
        limit: Annotated[int, Field(ge=1, le=50, description="Сколько последних запросов, от 1 до 50")] = 15,
    ) -> dict[str, Any]:
        """Последние запросы клиента к сервису: время, код ответа (200, 401, 413, 429, 503) и подробности. Плюс сводка по кодам."""
        data = store.load()
        found = store.client(data, client)
        events = found["events"][-limit:]
        counts: dict[str, int] = {}
        for event in events:
            counts[str(event["status"])] = counts.get(str(event["status"]), 0) + 1
        return {"client": found["id"], "events": events, "by_status": counts}

    @server.tool(title="Состояние сервиса")
    def service_status() -> dict[str, Any]:
        """Сервис сейчас: актуальный адрес из интернета, когда и почему он менялся, прошлый адрес, инциденты."""
        return store.load()["service"]

    @server.tool(title="Ответить в тикет")
    def reply_to_ticket(
        ticket_id: TicketId,
        text: Annotated[str, Field(min_length=1, max_length=4000, description="Текст ответа клиенту")],
        status: Annotated[Literal["answered", "closed"], Field(description="Новый статус тикета")] = "answered",
    ) -> dict[str, Any]:
        """Записать ответ поддержки в тикет и сменить статус. Только по прямой просьбе оператора."""
        with store._lock:
            data = store.load()
            ticket = store.ticket(data, ticket_id)
            ticket["messages"].append({"from": "support", "at": datetime.now().strftime("%Y-%m-%d %H:%M"), "text": text.strip()})
            ticket["status"] = status
            store.save(data)
        return {"id": ticket_id, "status": status, "messages": len(ticket["messages"])}

    return server


def path() -> Path:
    return Path(os.getenv("ADVENT_CRM_PATH") or DEFAULT_PATH)


def main() -> None:
    build_server(CrmStore(path())).run()


if __name__ == "__main__":
    main()

"""
День 33: ассистент поддержки пользователей.

Продукт — наш приватный AI-сервис дня 30. Страница — мини-сервис
поддержки: список тикетов из CRM, карточка клиента с журналом его
запросов и чат с ассистентом по тикету (advent/rag/support.py): FAQ
продукта через RAG, CRM через MCP. Ответ клиенту в тикет отправляет
оператор кнопкой — тоже через MCP, инструментом reply_to_ticket.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, render_template, request

from ...mcp.agent import DialogError
from ...mcp.client import CRM_SERVER, call_server_tool
from ...mcp.servers.crm import DEFAULT_PATH, CrmError, CrmStore
from ...navigation import get_day
from ...rag import RagIndex
from ...rag.support import DEFAULT_INDEX, MODEL, dialogs, documents, ensure_index, history, turn
from ..rag_pages import embedder, ndjson_stream, transport


bp = Blueprint("day33", __name__, url_prefix="/days/33")

MAX_MESSAGE = 1000
TICKET = re.compile(r"^T-\d{1,6}$")


def _store() -> CrmStore:
    return CrmStore(current_app.config.get("DAY33_CRM_PATH") or DEFAULT_PATH)


def _index() -> RagIndex:
    return RagIndex(current_app.config.get("DAY33_INDEX_PATH") or DEFAULT_INDEX)


def _dialogs():
    return dialogs(current_app.config.get("DAY33_DIALOGS_DIR"))


def _factory():
    # Тесты подключают CRM-сервер в памяти, к временному файлу.
    return current_app.config.get("DAY33_MCP_FACTORY")


def _docs():
    folder = current_app.config.get("DAY33_DOCS_DIR")
    return documents(Path(folder)) if folder else documents()


def _tickets(data: dict[str, Any]) -> list[dict[str, Any]]:
    names = {client["id"]: client["name"] for client in data["clients"]}
    return [
        {"id": item["id"], "subject": item["subject"], "status": item["status"], "created_at": item["created_at"],
         "client": item["client"], "client_name": names.get(item["client"])}
        for item in data["tickets"]
    ]


def _ticket_view(ticket_id: str) -> dict[str, Any]:
    store = _store()
    data = store.load()
    ticket = store.ticket(data, ticket_id)
    client = store.client(data, ticket["client"])
    try:
        chat = history(_dialogs().load(ticket_id))
    except DialogError:
        chat = []
    return {
        "ticket": ticket,
        "client": store.card(data, client),
        "events": client["events"][-12:],
        "chat": chat,
    }


@bp.get("/")
def index():
    data = _store().load()
    return render_template(
        "days/day33.html",
        active_day=get_day(33),
        tickets=_tickets(data),
        service=data["service"],
        plans=data["plans"],
        docs=[doc.source for doc in _docs()],
        model=MODEL,
        server=CRM_SERVER.as_dict(),
    )


@bp.get("/api/tickets")
def tickets():
    return jsonify(_tickets(_store().load()))


@bp.get("/api/tickets/<ticket_id>")
def ticket(ticket_id: str):
    if not TICKET.match(ticket_id):
        return jsonify({"error": "Номер тикета — T-<число>"}), 400
    try:
        return jsonify(_ticket_view(ticket_id))
    except CrmError as error:
        return jsonify({"error": str(error)}), 404


@bp.post("/api/tickets/<ticket_id>/chat")
def chat(ticket_id: str):
    data = request.get_json(silent=True)
    text = str(data.get("message") or "").strip() if isinstance(data, dict) else ""
    if not TICKET.match(ticket_id):
        return jsonify({"error": "Номер тикета — T-<число>"}), 400
    if not text:
        return jsonify({"error": "Напишите сообщение"}), 400
    if len(text) > MAX_MESSAGE:
        return jsonify({"error": f"Сообщение длиннее {MAX_MESSAGE} символов"}), 400
    store = _store()
    try:
        subject = store.ticket(store.load(), ticket_id)["subject"]
    except CrmError as error:
        return jsonify({"error": str(error)}), 404
    index, embedder_, transport_, factory, saved = _index(), embedder(), transport(), _factory(), _dialogs()
    docs = _docs()

    def work(emit):
        ensure_index(index, embedder_, docs)
        dialog = saved.load(ticket_id)
        finished = False
        for event in turn(ticket_id, text, dialog=dialog, index=index, embedder=embedder_, transport=transport_,
                          factory=factory, subject=subject):
            finished = finished or event["type"] == "done"
            emit(event)
        if finished:
            saved.save(ticket_id, dialog)

    return ndjson_stream(work)


@bp.post("/api/tickets/<ticket_id>/reply")
def reply(ticket_id: str):
    """Оператор отправляет ответ клиенту — через MCP, тем же сервером CRM."""
    data = request.get_json(silent=True)
    text = str(data.get("text") or "").strip() if isinstance(data, dict) else ""
    if not TICKET.match(ticket_id) or not text:
        return jsonify({"error": "Нужны тикет и текст ответа"}), 400
    status = data.get("status") if data.get("status") in ("answered", "closed") else "answered"
    result = call_server_tool(CRM_SERVER, "reply_to_ticket", {"ticket_id": ticket_id, "text": text, "status": status},
                              transport_factory=_factory())
    if not result.ok:
        return jsonify({"error": result.error or "CRM не ответила"}), 502
    return jsonify({"ms": result.ms, **_ticket_view(ticket_id)})


@bp.post("/api/tickets/<ticket_id>/reset")
def reset_chat(ticket_id: str):
    if not TICKET.match(ticket_id):
        return jsonify({"error": "Номер тикета — T-<число>"}), 400
    _dialogs().delete(ticket_id)
    return jsonify({"reset": ticket_id})


@bp.post("/api/crm/reset")
def reset_crm():
    """CRM — к исходным данным, переписки с ассистентом — удалить."""
    store = _store()
    data = store.load()
    store.reset()
    saved = _dialogs()
    for item in data["tickets"]:
        saved.delete(item["id"])
    return jsonify({"reset": True, "tickets": _tickets(store.load())})

"""
День 16: подключение MCP — соединение и список инструментов.

Клиент на официальном SDK встаёт в соединение с общедоступным сервером,
проходит рукопожатие и забирает tools/list. Инструменты не вызываются —
это день 17. На страницу уходит всё, что было по проводу: какие сообщения
ушли, что пришло в ответ и сколько токенов займут схемы, если отдать их
модели.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request

from ...mcp import SERVERS, get_server, inspect_server
from ...navigation import get_day


bp = Blueprint("day16", __name__, url_prefix="/days/16")


@bp.get("/")
def index():
    return render_template("days/day16.html", active_day=get_day(16), servers=SERVERS)


@bp.post("/api/inspect")
def inspect():
    payload = request.get_json(silent=True)
    key = payload.get("server") if isinstance(payload, dict) else None
    server = get_server(key) if isinstance(key, str) else None
    if server is None:
        return jsonify({"error": "Выберите сервер из списка"}), 400

    # Тесты подставляют транспорт в памяти процесса — без сети и без npx.
    factory = current_app.config.get("MCP_TRANSPORT_FACTORY")
    result = inspect_server(server, transport_factory=factory)
    return jsonify(result.as_dict()), 200 if result.ok else 502

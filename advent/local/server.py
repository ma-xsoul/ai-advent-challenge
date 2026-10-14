"""
HTTP-обёртка приватного сервиса — день 30. Отдельное маленькое приложение
на своём порту: наружу через туннель выходит только оно, а не всё
приложение с днями (там ключ DeepSeek и чужим он ни к чему).

- GET  /              — чат для людей: ключ, диалог, место в очереди;
- GET  /api/health    — жив ли сервис, модель, длина очереди (без ключа);
- GET  /api/me        — кто я по ключу и сколько запросов осталось;
- POST /api/chat      — ответ модели: {"message"} или {"messages"};
                        потоком NDJSON (queued → start → delta → done)
                        или целиком с "stream": false;
- GET  /admin/status  — очередь, журнал, клиенты — для страницы дня 30,
                        по ключу администратора в X-Admin-Token.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request

from .service import PrivateService, ServiceError, origin_of


ROOT = Path(__file__).resolve().parent.parent


def create_service_app(service: PrivateService) -> Flask:
    app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    app.extensions["private_service"] = service
    app.json.ensure_ascii = False

    def fail(error: ServiceError) -> Response:
        response = jsonify(error.as_dict())
        response.status_code = error.status
        if error.retry_after is not None:
            response.headers["Retry-After"] = str(error.retry_after)
        return response

    @app.get("/")
    def chat():
        return render_template("service/chat.html", model=service.model, limits=service.limits.as_dict())

    @app.get("/api/health")
    def health():
        queue = service.queue.snapshot()
        return jsonify({"ok": True, "model": service.model, "busy": queue["active"] is not None, "waiting": len(queue["waiting"])})

    @app.get("/api/me")
    def me():
        origin = origin_of(request.headers, request.remote_addr)
        try:
            client = service.authorize(request.headers.get("Authorization"), origin)
        except ServiceError as error:
            return fail(error)
        return jsonify({"name": client["name"], "used": service.rate.used(client["name"]), "limits": service.limits.as_dict()})

    @app.post("/api/chat")
    def answer():
        origin = origin_of(request.headers, request.remote_addr)
        data = request.get_json(silent=True)
        try:
            client = service.authorize(request.headers.get("Authorization"), origin)
            messages = service.messages(data, client["name"], origin)
            ticket = service.admit(client["name"], origin)
        except ServiceError as error:
            return fail(error)
        events = service.run(ticket, messages)
        if isinstance(data, dict) and data.get("stream") is False:
            return _whole(events)

        def lines() -> Iterator[str]:
            for event in events:
                yield json.dumps(event, ensure_ascii=False) + "\n"

        response = Response(lines(), mimetype="application/x-ndjson")
        response.headers["Cache-Control"] = "no-cache, no-transform"
        response.headers["X-Accel-Buffering"] = "no"
        return response

    @app.get("/admin/status")
    def admin_status():
        token = request.headers.get("X-Admin-Token") or ""
        if not hmac.compare_digest(token.encode(), service.keys.admin().encode()):
            return jsonify({"error": "Нужен ключ администратора"}), 403
        after = request.args.get("after", type=int) or 0
        return jsonify({**service.status(), "events": service.log.recent(after)})

    return app


def _whole(events: Iterator[dict[str, Any]]) -> Response:
    """Без потока: ждём очередь и ответ целиком — удобно для curl."""
    text: list[str] = []
    result: dict[str, Any] = {}
    first_position = None
    for event in events:
        if event["type"] == "queued" and first_position is None:
            first_position = event["position"]
        elif event["type"] == "delta":
            text.append(event["text"])
        elif event["type"] == "done":
            result = {key: value for key, value in event.items() if key != "type"}
        elif event["type"] == "error":
            return Response(json.dumps({"error": event["error"], "code": event["code"]}, ensure_ascii=False),
                            status=502, mimetype="application/json")
    return Response(json.dumps({"answer": "".join(text).strip(), "queue_position": first_position, **result}, ensure_ascii=False),
                    mimetype="application/json")

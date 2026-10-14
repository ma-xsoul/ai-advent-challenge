"""
День 30: локальная LLM как приватный сервис.

Сам сервис — отдельный процесс (service.py в корне, advent/local/server.py):
наружу через туннель выходит только он. Эта страница — пульт владельца на
localhost: выдать и отозвать ключи, посмотреть очередь и журнал запросов
(кто, с какого устройства, локально или из интернета), запустить проверку
нагрузки — десять запросов сразу от двух клиентов.
"""

from __future__ import annotations

from typing import Any

import requests
from flask import Blueprint, current_app, jsonify, render_template, request

from ...local import OllamaClient, OllamaError
from ...local import loadtest
from ...local.service import KEYS_PATH, MODEL, PORT, KeyStore, Limits
from ...navigation import get_day
from ..rag_pages import ndjson_stream


bp = Blueprint("day30", __name__, url_prefix="/days/30")


def _keys() -> KeyStore:
    return KeyStore(current_app.config.get("DAY30_KEYS_PATH") or KEYS_PATH)


def _service_url() -> str:
    return (current_app.config.get("DAY30_SERVICE_URL") or f"http://127.0.0.1:{PORT}").rstrip("/")


def _http() -> Any:
    return current_app.config.get("DAY30_HTTP") or requests


def _service_status(after: int = 0) -> dict[str, Any]:
    try:
        response = _http().get(
            f"{_service_url()}/admin/status",
            headers={"X-Admin-Token": _keys().admin()},
            params={"after": after},
            timeout=3,
        )
    except requests.RequestException:
        return {"running": False, "error": "Сервис не запущен"}
    if response.status_code != 200:
        return {"running": False, "error": f"Сервис ответил HTTP {response.status_code}"}
    return {"running": True, **response.json()}


def _ollama() -> dict[str, Any]:
    client = current_app.config.get("OLLAMA_CLIENT") or OllamaClient()
    try:
        return {"ok": True, "version": client.version(), "url": client.url}
    except OllamaError as error:
        return {"ok": False, "url": client.url, "error": str(error)}


def _public_keys() -> list[dict[str, Any]]:
    return [{key: item.get(key) for key in ("name", "key", "created_at")} for item in _keys().keys() if not item.get("test")]


@bp.get("/")
def index():
    return render_template(
        "days/day30.html",
        active_day=get_day(30),
        keys=_public_keys(),
        limits=Limits().as_dict(),
        model=MODEL,
        service_url=_service_url(),
        ollama=_ollama(),
    )


@bp.get("/api/status")
def status():
    return jsonify(_service_status(request.args.get("after", type=int) or 0))


@bp.post("/api/keys")
def add_key():
    data = request.get_json(silent=True)
    name = str(data.get("name") or "") if isinstance(data, dict) else ""
    try:
        item = _keys().add(name)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify({key: item[key] for key in ("name", "key", "created_at")}), 201


@bp.delete("/api/keys/<name>")
def remove_key(name: str):
    if not _keys().remove(name):
        return jsonify({"error": "Такого ключа нет"}), 404
    return jsonify({"removed": name})


@bp.post("/api/load")
def load():
    """Проверка нагрузки: через localhost или через публичный адрес туннеля."""
    data = request.get_json(silent=True)
    target = data.get("target") if isinstance(data, dict) else None
    state = _service_status()
    if not state["running"]:
        return jsonify({"error": "Сервис не запущен: .venv/bin/python service.py"}), 409
    if target == "public":
        if not state.get("public_url"):
            return jsonify({"error": "Нет адреса из интернета — туннель не подключён"}), 409
        base = state["public_url"]
    else:
        base = _service_url()
    limits = Limits(**{key: value for key, value in (state.get("limits") or {}).items() if key in Limits.__dataclass_fields__})
    keys, http = _keys(), _http()

    def work(emit):
        loadtest.run(base, keys, limits, emit, http=http)
        emit({"type": "done"})

    return ndjson_stream(work)

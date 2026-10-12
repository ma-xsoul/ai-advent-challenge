"""
День 26: запуск локальной LLM.

Модель живёт на маке: Ollama держит её в памяти и отдаёт по HTTP на
localhost:11434 — без ключа, без интернета, данные никуда не уходят
(advent/local/ollama.py). Страница показывает, что скачано и что сейчас
в памяти, задаёт три запроса разной сложности и замеряет ответ: прогрев
модели, время до первого токена, скорость генерации. Те же запросы — из
терминала: ollama run и curl на /api/chat.
"""

from __future__ import annotations

import os
from typing import Any

from flask import Blueprint, current_app, jsonify, render_template, request

from ...local import OllamaClient, OllamaError
from ...local.ollama import think_for
from ...navigation import get_day
from ..rag_pages import ndjson_stream


bp = Blueprint("day26", __name__, url_prefix="/days/26")

MAX_PROMPT = 2000
# По умолчанию — gpt-oss: на маке с 24 ГБ она и умнее, и быстрее gemma4:12b (MoE: ~3,6 млрд
# активных параметров на токен против 12 у плотной gemma).
PREFERRED = ("gpt-oss:20b", "gemma4:12b")

# Три запроса разной сложности: факт, рассуждение в несколько шагов, код.
PRESETS = (
    {
        "key": "simple",
        "title": "Простой",
        "prompt": "Какая столица Австралии? Ответь одним словом.",
        "expected": "Канберра",
    },
    {
        "key": "medium",
        "title": "Средний",
        "prompt": (
            "В корзине 3 яблока и вдвое больше груш. Половину груш съели, потом докупили 4 яблока. "
            "Сколько фруктов в корзине теперь? Объясни по шагам и дай ответ числом."
        ),
        "expected": "10: 7 яблок и 3 груши",
    },
    {
        "key": "hard",
        "title": "Сложный",
        "prompt": (
            "Напиши на Python функцию is_palindrome(text), которая проверяет, является ли строка палиндромом, "
            "игнорируя регистр, пробелы и знаки препинания. Добавь три assert-проверки, среди них — "
            "«А роза упала на лапу Азора»."
        ),
        "expected": "рабочая функция и три assert, фраза про Азора — палиндром",
    },
)


# macOS по умолчанию отдаёт видеочипу около ¾ оперативки (recommendedMaxWorkingSetSize):
# на маке с 24 ГБ это ≈ 17,8 ГБ. Модель плюс контекст должны влезть сюда, иначе
# часть уйдёт на процессор и в подкачку — и всё замедлится в разы.
GPU_SHARE = 0.75
# Запас под контекст и служебные буферы поверх весов модели.
HEADROOM = 1.5 * 1024**3


def _memory() -> dict[str, int]:
    # Тесты задают объём сами — иначе ответ «влезет ли» зависел бы от машины.
    ram = current_app.config.get("SYSTEM_RAM")
    if ram is None:
        try:
            ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (ValueError, OSError, AttributeError):
            ram = 0
    return {"ram": ram, "gpu_budget": int(ram * GPU_SHARE)}


def _client() -> OllamaClient:
    # Тесты подставляют клиента без сети.
    return current_app.config.get("OLLAMA_CLIENT") or OllamaClient()


def _status(client: OllamaClient) -> dict[str, Any]:
    try:
        models = client.models()
        return {"ok": True, "url": client.url, "version": client.version(), "models": models, "loaded": client.loaded()}
    except OllamaError as error:
        return {"ok": False, "url": client.url, "error": str(error), "models": [], "loaded": []}


def _default_model(models: list[dict[str, Any]]) -> str | None:
    chat = [model["name"] for model in models if model["chat"]]
    return next((name for name in PREFERRED if name in chat), chat[0] if chat else None)


@bp.get("/")
def index():
    status = _status(_client())
    memory = _memory()
    for model in status["models"]:
        model["fits"] = not memory["gpu_budget"] or model["size"] + HEADROOM <= memory["gpu_budget"]
    return render_template(
        "days/day26.html",
        active_day=get_day(26),
        status=status,
        memory=memory,
        default_model=_default_model(status["models"]),
        presets=PRESETS,
    )


@bp.get("/api/status")
def status():
    return jsonify(_status(_client()))


@bp.post("/api/ask")
def ask():
    """Один запрос к локальной модели: start → thinking/delta… → done (замеры и что в памяти)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Ожидался JSON-объект"}), 400
    prompt = str(data.get("prompt") or "").strip()
    model = str(data.get("model") or "").strip()
    if not prompt:
        return jsonify({"error": "Введите запрос"}), 400
    if len(prompt) > MAX_PROMPT:
        return jsonify({"error": f"Запрос длиннее {MAX_PROMPT} символов"}), 400
    client = _client()
    try:
        chat_models = {item["name"] for item in client.models() if item["chat"]}
    except OllamaError as error:
        return jsonify({"error": str(error)}), 502
    if model not in chat_models:
        return jsonify({"error": f"Модели {model or '—'} нет среди скачанных чат-моделей"}), 400
    think = think_for(model, bool(data.get("think")))

    def work(emit):
        emit({"type": "start", "model": model, "think": think})
        try:
            for event in client.chat_stream(model, [{"role": "user", "content": prompt}], think=think):
                if event["type"] == "done":
                    event = {**event, "loaded": client.loaded()}
                emit(event)
        except OllamaError as error:
            emit({"type": "error", "error": str(error)})

    return ndjson_stream(work)

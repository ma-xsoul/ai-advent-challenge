"""
Проверка нагрузки приватного сервиса — день 30.

Десять запросов уходят почти одновременно (с шагом 80 мс, чтобы порядок
в очереди был предсказуем) от двух клиентов — как будто два устройства:

- клиент А шлёт 6 коротких вопросов — 5 встают в очередь, 6-й упирается
  в rate limit (429);
- клиент Б шлёт 2 вопроса и одно слишком длинное сообщение — max context (413);
- запрос без верного ключа — 401.

Ключи А и Б создаются на время проверки и потом удаляются: у свежих
ключей пустой rate limit, повторный запуск даёт тот же результат.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import requests

from .service import KeyStore, Limits


STAGGER_S = 0.08
TIMEOUT_S = 180

Emit = Callable[[dict[str, Any]], None]


def scenario(limits: Limits) -> list[dict[str, Any]]:
    long_text = "Перескажи этот текст. " + "Очень длинное сообщение. " * (limits.max_message_chars // 24 + 20)
    return [
        {"client": "А", "message": "Сколько будет 2+2? Ответь одним числом.", "expect": 200},
        {"client": "Б", "message": "Столица Франции? Одно слово.", "expect": 200},
        {"client": "А", "message": "Какого цвета небо днём? Одно слово.", "expect": 200},
        {"client": "Б", "message": "Сколько дней в неделе? Одно число.", "expect": 200},
        {"client": "А", "message": "Переведи на английский: кошка.", "expect": 200},
        {"client": "А", "message": "Назови простое число больше 10. Только число.", "expect": 200},
        {"client": "А", "message": "Сколько будет 3×3? Одно число.", "expect": 200},
        {"client": "А", "message": "Сколько ног у паука? Одно число.", "expect": 429},
        {"client": "Б", "message": long_text, "expect": 413},
        {"client": "?", "message": "Привет! Я без ключа.", "expect": 401},
    ]


def run(base_url: str, keys: KeyStore, limits: Limits, emit: Emit, *, http: Any = requests) -> dict[str, Any]:
    """События: load_start → load_row (каждое изменение строки) → load_summary."""
    plan = scenario(limits)
    stamp = datetime.now().strftime("%H%M%S")
    created = {name: keys.add(f"тест-{name}-{stamp}", test=True) for name in ("А", "Б")}
    rows: list[dict[str, Any]] = [
        {"index": number, "client": item["client"], "message": _short(item["message"]), "chars": len(item["message"]),
         "expect": item["expect"], "state": "ждёт отправки"}
        for number, item in enumerate(plan)
    ]
    lock = threading.Lock()
    emit({"type": "load_start", "target": base_url, "rows": rows, "clients": {name: item["name"] for name, item in created.items()}})

    def update(number: int, **changes: Any) -> None:
        with lock:
            rows[number].update(changes)
            row = dict(rows[number])
        emit({"type": "load_row", **row})

    def send(number: int, item: dict[str, Any]) -> None:
        time.sleep(number * STAGGER_S)
        key = created[item["client"]]["key"] if item["client"] in created else "sk-wrong"
        sent = time.perf_counter()
        update(number, state="отправлен")
        try:
            response = http.post(
                f"{base_url}/api/chat",
                headers={"Authorization": f"Bearer {key}"},
                json={"message": item["message"]},
                stream=True,
                timeout=TIMEOUT_S,
            )
        except requests.RequestException as error:
            update(number, state="ошибка", status=None, error=str(error))
            return
        try:
            if response.status_code != 200:
                try:
                    body = response.json()
                except ValueError:
                    body = {}
                update(number, state="отказ", status=response.status_code, error=body.get("error") or f"HTTP {response.status_code}",
                       total_s=round(time.perf_counter() - sent, 2))
                return
            answer: list[str] = []
            for raw in response.iter_lines():
                if not raw:
                    continue
                event = json.loads(raw)
                if event["type"] == "queued" and rows[number].get("position") is None:
                    update(number, state="в очереди", status=200, position=event["position"])
                elif event["type"] == "start":
                    update(number, state="отвечает", waited_s=event["waited_s"])
                elif event["type"] == "delta":
                    answer.append(event["text"])
                elif event["type"] == "done":
                    update(number, state="ответ", answer=_short("".join(answer).strip(), 80), answer_s=event.get("wall_s"),
                           total_s=round(time.perf_counter() - sent, 2))
                    return
                elif event["type"] == "error":
                    update(number, state="ошибка", error=event["error"])
                    return
            update(number, state="ошибка", error="ответ оборвался")
        finally:
            response.close()

    threads = [threading.Thread(target=send, args=(number, item), daemon=True) for number, item in enumerate(plan)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(TIMEOUT_S + 5)
    finally:
        for item in created.values():
            keys.remove(item["name"])

    summary = summarize(rows)
    emit({"type": "load_summary", "summary": summary})
    return summary


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    answered = [row for row in rows if row["state"] == "ответ"]
    statuses: dict[str, int] = {}
    for row in rows:
        if row.get("status") and row["status"] != 200:
            statuses[str(row["status"])] = statuses.get(str(row["status"]), 0) + 1
    waits = [row["waited_s"] for row in answered if row.get("waited_s") is not None]
    return {
        "sent": len(rows),
        "answered": len(answered),
        "rejected": statuses,
        "errors": sum(row["state"] == "ошибка" for row in rows),
        "max_position": max((row.get("position") or 0 for row in answered), default=0),
        "max_wait_s": max(waits, default=0),
        "total_s": max((row.get("total_s") or 0 for row in rows), default=0),
    }


def _short(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"

"""
Клиент локальной Ollama — день 26.

Ollama — сервер на этой же машине (по умолчанию localhost:11434): держит
модели в памяти и отвечает по HTTP. Ключа нет, интернет не нужен.

- GET  /api/version — версия сервера;
- GET  /api/tags    — что скачано: размер, параметры, квантование;
- POST /api/show    — что модель умеет: completion, tools, thinking, embedding;
- GET  /api/ps      — что сейчас загружено в память и сколько занимает;
- POST /api/chat    — сообщения, ответ потоком: строки JSON, в последней —
                      замеры самой Ollama в наносекундах: прогрев модели,
                      чтение промпта, генерация.

Модели с размышлениями (thinking) отдают их отдельным полем. У gemma4
размышления выключаются (think=false), у gpt-oss — нет, только уровень:
low / medium / high.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from typing import Any

import requests


DEFAULT_URL = "http://127.0.0.1:11434"
TIMEOUT_SECONDS = 600
NS = 1_000_000_000


class OllamaError(RuntimeError):
    """Ollama не отвечает или вернула ошибку — с подсказкой, что сделать."""


class OllamaClient:
    def __init__(self, url: str | None = None, *, timeout: int = TIMEOUT_SECONDS) -> None:
        self.url = (url or os.getenv("OLLAMA_URL") or DEFAULT_URL).rstrip("/")
        self.timeout = timeout

    # ── что есть на машине ───────────────────────────────────────────────

    def version(self) -> str:
        return str(self._get("/api/version").get("version") or "?")

    def models(self) -> list[dict[str, Any]]:
        """Скачанные модели — с тем, что они умеют. Эмбеддинговые отвечать текстом не могут."""
        result = []
        for item in self._get("/api/tags").get("models") or []:
            details = item.get("details") or {}
            capabilities = self.capabilities(item["name"])
            result.append(
                {
                    "name": item["name"],
                    "size": int(item.get("size") or 0),
                    "parameters": details.get("parameter_size") or "?",
                    "quantization": details.get("quantization_level") or "?",
                    "family": details.get("family") or "?",
                    "capabilities": capabilities,
                    "chat": "completion" in capabilities,
                }
            )
        return sorted(result, key=lambda model: (not model["chat"], model["size"]))

    def capabilities(self, model: str) -> list[str]:
        try:
            return list(self._post("/api/show", {"model": model}).get("capabilities") or [])
        except OllamaError:
            return []

    def loaded(self) -> list[dict[str, Any]]:
        """Что сейчас в памяти: сколько занимает всего и сколько из этого — на видеочипе."""
        return [
            {
                "name": item["name"],
                "size": int(item.get("size") or 0),
                "vram": int(item.get("size_vram") or 0),
                "context": item.get("context_length"),
                "expires_at": item.get("expires_at"),
            }
            for item in self._get("/api/ps").get("models") or []
        ]

    # ── разговор ─────────────────────────────────────────────────────────

    def chat_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        think: bool | str = False,
        options: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """
        События: thinking / delta (кусочки текста) → done (замеры). Время до
        первого токена меряем сами: Ollama его не отдаёт.
        """
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True, "think": think}
        if options:
            payload["options"] = options
        started = time.perf_counter()
        first: float | None = None
        try:
            response = requests.post(f"{self.url}/api/chat", json=payload, stream=True, timeout=self.timeout)
        except requests.RequestException as error:
            raise OllamaError(f"Ollama недоступна по {self.url}: {error}. Запустите её: ollama serve") from error
        if response.status_code != 200:
            raise OllamaError(_error_text(response))

        with response:
            for raw in response.iter_lines():
                if not raw:
                    continue
                chunk = json.loads(raw)
                if chunk.get("error"):
                    raise OllamaError(str(chunk["error"]))
                message = chunk.get("message") or {}
                for kind, field in (("thinking", "thinking"), ("delta", "content")):
                    text = message.get(field)
                    if text:
                        first = first or time.perf_counter()
                        yield {"type": kind, "text": text}
                if chunk.get("done"):
                    yield {"type": "done", **metrics(chunk, started=started, first=first)}
                    return

    # ── HTTP ─────────────────────────────────────────────────────────────

    def _get(self, path: str) -> dict[str, Any]:
        try:
            response = requests.get(f"{self.url}{path}", timeout=10)
        except requests.RequestException as error:
            raise OllamaError(f"Ollama недоступна по {self.url}: {error}. Запустите её: ollama serve") from error
        if response.status_code != 200:
            raise OllamaError(_error_text(response))
        return response.json()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = requests.post(f"{self.url}{path}", json=payload, timeout=30)
        except requests.RequestException as error:
            raise OllamaError(f"Ollama недоступна по {self.url}: {error}") from error
        if response.status_code != 200:
            raise OllamaError(_error_text(response))
        return response.json()


def think_for(model: str, enabled: bool) -> bool | str:
    """У gpt-oss размышления не выключаются — только уровень; «выключено» для неё — low."""
    if model.startswith("gpt-oss"):
        return "medium" if enabled else "low"
    return enabled


def metrics(chunk: dict[str, Any], *, started: float, first: float | None) -> dict[str, Any]:
    """Замеры последней строки Ollama — в секундах и токенах в секунду."""

    def seconds(key: str) -> float:
        return round((chunk.get(key) or 0) / NS, 2)

    def rate(count_key: str, duration_key: str) -> float | None:
        duration = chunk.get(duration_key) or 0
        return round(chunk.get(count_key, 0) / (duration / NS), 1) if duration else None

    return {
        "load_s": seconds("load_duration"),
        "prompt_tokens": chunk.get("prompt_eval_count") or 0,
        "prompt_tps": rate("prompt_eval_count", "prompt_eval_duration"),
        "output_tokens": chunk.get("eval_count") or 0,
        "output_tps": rate("eval_count", "eval_duration"),
        "total_s": seconds("total_duration"),
        "first_token_s": round(first - started, 2) if first else None,
        "wall_s": round(time.perf_counter() - started, 2),
        "done_reason": chunk.get("done_reason"),
    }


def _error_text(response: requests.Response) -> str:
    try:
        message = response.json().get("error")
    except ValueError:
        message = None
    if response.status_code == 404 and message and "not found" in message:
        return f"{message}. Скачайте модель: ollama pull <имя>"
    return message or f"Ollama ответила HTTP {response.status_code}"

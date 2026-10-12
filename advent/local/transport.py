"""Транспорт агента к локальной Ollama через /v1/chat/completions."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import requests

from ..agent.transport import TransportError
from .ollama import DEFAULT_URL, TIMEOUT_SECONDS


DEFAULT_MODEL = "gpt-oss:20b"


class OllamaTransport:
    """Тот же контракт send/stream, что у облачного транспорта; ключ не нужен."""

    def __init__(self, url: str | None = None, *, timeout: int = TIMEOUT_SECONDS) -> None:
        self.url = (url or os.getenv("OLLAMA_URL") or DEFAULT_URL).rstrip("/")
        self.timeout = timeout
        self._endpoint = f"{self.url}/v1/chat/completions"

    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._post(_payload(payload, stream=False), stream=False)
        try:
            body = _body(response)
            if not 200 <= response.status_code < 300:
                raise TransportError(_error_message(body, response.status_code))
            return _normalize(body)
        finally:
            response.close()

    def stream(self, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        response = self._post(_payload(payload, stream=True), stream=True)
        try:
            if not 200 <= response.status_code < 300:
                raise TransportError(_error_message(_body(response), response.status_code))
            finished = False
            for data in _sse_data(response):
                if data == "[DONE]":
                    if not finished:
                        raise TransportError("Ollama завершила поток без итогового события ответа")
                    return
                try:
                    body = json.loads(data)
                except ValueError as error:
                    raise TransportError("Ollama вернула повреждённый JSON в потоке") from error
                chunk = _normalize(body, stream=True)
                if any(isinstance(choice.get("finish_reason"), str) for choice in chunk["choices"]):
                    finished = True
                yield chunk
            raise TransportError("Поток Ollama оборвался до завершения ответа. Повторите запрос.")
        except requests.RequestException as error:
            raise TransportError(f"Поток Ollama был прерван: {error}") from error
        finally:
            response.close()

    def _post(self, payload: dict[str, Any], *, stream: bool) -> requests.Response:
        try:
            return requests.post(
                self._endpoint,
                headers={"Content-Type": "application/json"},
                json=payload,
                stream=stream,
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise TransportError(
                f"Ollama недоступна по {self.url}: {error}. Запустите её: ollama serve"
            ) from error


def _payload(payload: dict[str, Any], *, stream: bool) -> dict[str, Any]:
    request = {**payload, "stream": stream}
    request.setdefault("model", DEFAULT_MODEL)
    if str(request["model"]).strip().lower().endswith((":cloud", "-cloud")):
        raise TransportError("Для локального чата выберите скачанную модель Ollama; облачные модели запрещены")
    thinking = request.pop("thinking", None)
    if isinstance(thinking, dict) and "reasoning_effort" not in request and "reasoning" not in request:
        enabled = thinking.get("type") == "enabled"
        request["reasoning_effort"] = "medium" if enabled else (
            "low" if str(request["model"]).startswith("gpt-oss") else "none"
        )
    if stream:
        request["stream_options"] = {**(request.get("stream_options") or {}), "include_usage": True}
    else:
        request.pop("stream_options", None)
    return request


def _body(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError as error:
        if not 200 <= response.status_code < 300:
            return None
        raise TransportError(f"Ollama вернула ответ не в формате JSON (HTTP {response.status_code})") from error


def _normalize(body: Any, *, stream: bool = False) -> dict[str, Any]:
    if isinstance(body, dict) and body.get("error"):
        raise TransportError(_error_message(body, 200))
    if not isinstance(body, dict) or not isinstance(body.get("choices"), list):
        raise TransportError("Ollama вернула неверную структуру ответа")
    choices = []
    for choice in body["choices"]:
        field = "delta" if stream else "message"
        if not isinstance(choice, dict) or not isinstance(choice.get(field), dict):
            raise TransportError("Ollama вернула неверную структуру сообщения")
        message = dict(choice[field])
        # Ollama использует reasoning, агент — reasoning_content. Историю,
        # tool_calls и счётчики токенов отдаём без изменений.
        if isinstance(message.get("reasoning"), str):
            message.setdefault("reasoning_content", message.pop("reasoning"))
        choices.append({**choice, field: message})
    if not choices and not stream:
        raise TransportError("Ollama не вернула вариант ответа")
    return {**body, "choices": choices}


def _sse_data(response: requests.Response) -> Iterator[str]:
    data: list[str] = []
    for raw in response.iter_lines(chunk_size=1):
        try:
            line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        except UnicodeDecodeError as error:
            raise TransportError("Ollama вернула повреждённый текст в потоке") from error
        if not isinstance(line, str):
            raise TransportError("Ollama вернула неверный формат потока")
        if not line:
            if data:
                yield "\n".join(data)
                data.clear()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    if data:
        yield "\n".join(data)


def _error_message(body: Any, status: int) -> str:
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else error
    if not isinstance(message, str) or not message.strip():
        return f"Ollama вернула HTTP {status}"
    if status == 404 and "not found" in message.lower():
        return f"Ollama: {message}. Скачайте модель: ollama pull <имя>"
    return f"Ollama: {message}"

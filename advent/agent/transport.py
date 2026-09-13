"""
Транспорт — единственное место, где агент касается сети.

Агент не знает ни URL, ни ключа, ни формата ошибок HTTP: он отдаёт транспорту
payload и получает обратно JSON. Поэтому в тестах транспорт подменяется
заглушкой, а агент проверяется без единого реального запроса.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any, Protocol

import requests


DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"


class TransportError(RuntimeError):
    """Запрос не дошёл до модели или вернулся с ошибкой."""


class Transport(Protocol):
    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Отправляет payload и возвращает JSON ответа. Ошибки — TransportError."""

    def stream(self, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Отправляет payload и отдаёт чанки ответа по мере генерации."""


class DeepSeekTransport:
    """HTTP-транспорт к DeepSeek. Ключ берётся из окружения, если не передан явно."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        url: str = DEEPSEEK_API_URL,
        timeout: int = 120,
    ) -> None:
        self._api_key = api_key
        self._url = url
        self._timeout = timeout

    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_key = self._api_key or os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise TransportError("Не задан DEEPSEEK_API_KEY в файле .env")

        try:
            response = requests.post(
                self._url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._timeout,
            )
        except requests.RequestException as error:
            raise TransportError(f"Не удалось связаться с DeepSeek: {error}") from error

        try:
            body = response.json()
        except ValueError as error:
            raise TransportError(
                f"DeepSeek вернул ответ не в формате JSON (HTTP {response.status_code})"
            ) from error

        if not 200 <= response.status_code < 300:
            raise TransportError(_error_message(body, response.status_code))

        return body

    def stream(self, payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
        api_key = self._api_key or os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise TransportError("Не задан DEEPSEEK_API_KEY в файле .env")

        try:
            response = requests.post(
                self._url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={**payload, "stream": True},
                stream=True,
                timeout=self._timeout,
            )
        except requests.RequestException as error:
            raise TransportError(f"Не удалось связаться с DeepSeek: {error}") from error

        if not 200 <= response.status_code < 300:
            try:
                body = response.json()
            except ValueError:
                body = None
            status = response.status_code
            response.close()
            raise TransportError(_error_message(body, status))

        try:
            for raw_line in response.iter_lines(chunk_size=1):
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                if not isinstance(line, str) or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue
        except requests.RequestException as error:
            raise TransportError(f"Поток DeepSeek был прерван: {error}") from error
        finally:
            response.close()


def _error_message(body: Any, status_code: int) -> str:
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    if isinstance(error, str):
        return error
    return f"DeepSeek вернул HTTP {status_code}"

"""
Доступ к моделям OpenRouter для пятого дня.

Отдельный модуль, а не расширение deepseek.py: у OpenRouter свой ключ, свои
заголовки и своё управление рассуждением (`reasoning` вместо `thinking`).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Generator
from typing import Any

import requests


OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Три весовые категории. Младшие — лестница Qwen, старшая — Kimi K3, самая
# дорогая китайская модель в каталоге OpenRouter.
#
# Раньше старшей была qwen3.8-max, но у неё рассуждение не отключается: она
# обрывала ответ на середине, упираясь в лимит токенов, и отвечала не
# по-русски. У Kimi reasoning.enabled=false принимается, поэтому все три
# модели сравниваются в одинаковых условиях.
#
# reasoning_mandatory оставлен как флаг: если модель запретит отключение,
# endpoint ответит 400 «Reasoning is mandatory for this endpoint».
DAY_FIVE_MODELS: tuple[dict[str, Any], ...] = (
    {
        "tier": "weak",
        "label": "Слабая",
        "slug": "qwen/qwen3.7-flash",
        "reasoning_mandatory": False,
    },
    {
        "tier": "medium",
        "label": "Средняя",
        "slug": "qwen/qwen3-30b-a3b-instruct-2507",
        "reasoning_mandatory": False,
    },
    {
        "tier": "strong",
        "label": "Сильная",
        "slug": "moonshotai/kimi-k3",
        "reasoning_mandatory": False,
    },
)

DAY_FIVE_TIERS = tuple(model["tier"] for model in DAY_FIVE_MODELS)

# Запас на рассуждение: у старшей модели его не отключить, и при коротком
# лимите она тратит всё на размышление и возвращает пустой content.
DAY_FIVE_MAX_TOKENS = 1200


def get_model(tier: str) -> dict[str, Any] | None:
    return next((model for model in DAY_FIVE_MODELS if model["tier"] == tier), None)


def _headers() -> dict[str, str] | None:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # Необязательные заголовки атрибуции приложения в рейтинге OpenRouter.
        "HTTP-Referer": "http://127.0.0.1:5001/days/5/",
        # Только ASCII: HTTP-заголовки кодируются latin-1, длинное тире их ломает.
        "X-Title": "AI Advent Homework Lab",
    }


def _payload(prompt: str, model: dict[str, Any], *, stream: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model["slug"],
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": DAY_FIVE_MAX_TOKENS,
        "stream": stream,
    }
    if not model["reasoning_mandatory"]:
        payload["reasoning"] = {"enabled": False}
    return payload


def stream_day_five_answers(prompt: str) -> Generator[dict[str, Any], None, None]:
    """Последовательно спрашивает три модели одним и тем же запросом."""
    headers = _headers()
    if headers is None:
        yield {"type": "error", "error": "Не задан OPENROUTER_API_KEY в файле .env"}
        return

    results: dict[str, dict[str, Any]] = {}

    for model in DAY_FIVE_MODELS:
        yield {
            "type": "model_start",
            "tier": model["tier"],
            "label": model["label"],
            "slug": model["slug"],
            "reasoning": model["reasoning_mandatory"],
        }

        answer, error = yield from _stream_one(prompt, model, headers)
        if error:
            # Одна упавшая модель не должна отменять весь прогон: остальные
            # ответы и мета-анализ по ним всё ещё имеют смысл.
            yield {"type": "model_error", "tier": model["tier"], "error": error}
            continue

        results[model["tier"]] = answer
        yield {"type": "model_done", "tier": model["tier"], "answer": answer}

    if not results:
        yield {"type": "error", "error": "Ни одна модель не ответила"}
        return

    yield {"type": "models_complete", "results": results}


def _stream_one(
    prompt: str,
    model: dict[str, Any],
    headers: dict[str, str],
) -> Generator[dict[str, Any], None, tuple[dict[str, Any] | None, str | None]]:
    started = time.perf_counter()
    first_token_at: float | None = None

    try:
        response = requests.post(
            OPENROUTER_API_URL,
            headers=headers,
            json=_payload(prompt, model, stream=True),
            stream=True,
            timeout=180,
        )
    except requests.RequestException as error:
        return None, f"Не удалось связаться с OpenRouter: {error}"

    if not 200 <= response.status_code < 300:
        message = _error_message(response)
        response.close()
        return None, message

    parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None

    try:
        for raw_line in response.iter_lines(chunk_size=1):
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if not isinstance(line, str) or not line.startswith("data:"):
                continue

            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue

            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]

            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                continue

            choice = choices[0]
            if isinstance(choice.get("finish_reason"), str):
                finish_reason = choice["finish_reason"]

            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue

            # Рассуждающие модели молчат в content по нескольку секунд, пока
            # думают. Без этих событий карточка выглядит зависшей.
            reasoning = delta.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                reasoning_parts.append(reasoning)
                yield {
                    "type": "delta",
                    "tier": model["tier"],
                    "target": "reasoning",
                    "content": reasoning,
                }

            content = delta.get("content")
            if not isinstance(content, str) or not content:
                continue

            if first_token_at is None:
                first_token_at = time.perf_counter()
            parts.append(content)
            yield {
                "type": "delta",
                "tier": model["tier"],
                "target": "answer",
                "content": content,
            }
    except requests.RequestException as error:
        return None, f"Поток OpenRouter был прерван: {error}"
    finally:
        response.close()

    elapsed = time.perf_counter() - started
    content = "".join(parts)
    if not content.strip():
        return None, (
            f"Модель {model['slug']} не вернула текст ответа — "
            "вероятно, лимит токенов ушёл на рассуждение"
        )

    return {
        "tier": model["tier"],
        "label": model["label"],
        "slug": model["slug"],
        "content": content,
        "characters": len(content),
        "finish_reason": finish_reason or "не указан",
        "seconds": round(elapsed, 2),
        "first_token_seconds": (
            round(first_token_at - started, 2) if first_token_at is not None else None
        ),
        "reasoning_characters": len("".join(reasoning_parts)),
        **_usage_summary(usage),
    }, None


def _usage_summary(usage: dict[str, Any] | None) -> dict[str, Any]:
    """OpenRouter всегда возвращает стоимость запроса — считать её самим не нужно."""
    if not isinstance(usage, dict):
        return {"input_tokens": None, "output_tokens": None, "cost": None}

    cost = usage.get("cost")
    return {
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "cost": float(cost) if isinstance(cost, (int, float)) else None,
    }


def _error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"OpenRouter вернул {response.status_code}: {response.text[:300]}"

    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    if isinstance(error, str):
        return error
    return f"OpenRouter вернул {response.status_code}"

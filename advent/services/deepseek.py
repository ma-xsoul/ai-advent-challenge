from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import requests


DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_BETA_API_URL = "https://api.deepseek.com/beta/chat/completions"

COUNTRY_TOPIC_RULE = (
    "Ты — сервис поиска и фильтрации стран. "
    "Отвечай только на запросы, цель которых — получить список стран по любому "
    "критерию: названию, региону, событию, признаку или участию в соревновании."
)
COUNTRY_ONLY_SYSTEM_PROMPT = (
    COUNTRY_TOPIC_RULE + " "
    "Если запрос не относится к списку стран, ответь строго: Некорректный запрос"
)
COUNTRY_JSON_SYSTEM_PROMPT = (
    COUNTRY_TOPIC_RULE + " "
    "Определяй допустимость только по намерению пользователя. Если пользователь просит "
    "список стран, всегда устанавливай valid=true — даже для нового или будущего события, "
    "при неполных данных либо неуверенности. Не используй valid=false как замену ответу. "
    "Для допустимого запроса верни максимально полный известный список через функцию "
    "return_country_search. Только если запрос вообще не относится к списку стран, "
    "установи valid=false и верни пустой countries."
)
META_ANALYSIS_SYSTEM_PROMPT = (
    "Ты кратко сравниваешь два способа обращения к LLM API. Проанализируй вариант "
    "с параметрами и базовый вариант без параметров: их преимущества, недостатки, "
    "подходящие сценарии и чем первый вариант лучше второго. Пиши по-русски, конкретно, "
    "без пересказа исходных ответов. Ответ дай строго в трёх коротких нумерованных "
    "пунктах: первый вариант, второй вариант, рекомендация. В каждом пункте не более "
    "двух коротких предложений. Целевой объём — 150–220 токенов. Сформулируй "
    "полностью завершённый мета-анализ не более чем за 400 токенов."
)

STRUCTURED_COUNTRY_TOOL = {
    "type": "function",
    "function": {
        "name": "return_country_search",
        "description": (
            "Validate that the user asks for a list of countries and return all matching "
            "countries. For an unrelated request set valid to false and countries to an "
            "empty array. Use ISO 3166-1 alpha-2 codes, English names and Russian names."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "valid": {
                    "type": "boolean",
                    "description": (
                        "True whenever the user asks for a list of countries. Unknown, "
                        "recent or future data is not a reason to set this to false."
                    ),
                },
                "countries": {
                    "type": "array",
                    "description": (
                        "The fullest known list matching the criterion. Do not leave it "
                        "empty merely because the data may be recent or uncertain."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "description": "Uppercase ISO 3166-1 alpha-2 code.",
                                "pattern": "^[A-Z]{2}$",
                            },
                            "name": {
                                "type": "string",
                                "description": "Official or common English country name.",
                            },
                            "nameRu": {
                                "type": "string",
                                "description": "Official or common Russian country name.",
                            },
                        },
                        "required": ["code", "name", "nameRu"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["valid", "countries"],
            "additionalProperties": False,
        },
    },
}


def get_model() -> str:
    return os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")


def _base_payload(
    prompt: str,
    *,
    model: str | None = None,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    return {
        "model": model or get_model(),
        "messages": messages,
        "thinking": {"type": "disabled"},
        "stream": False,
    }


def _post_to_deepseek(api_url: str, payload: dict[str, Any]) -> tuple[dict, int]:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return {"error": "Не задан DEEPSEEK_API_KEY в файле .env"}, 500

    try:
        response = requests.post(
            api_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
    except requests.RequestException as error:
        return {
            "error": "Не удалось связаться с DeepSeek",
            "details": str(error),
        }, 502

    try:
        return response.json(), response.status_code
    except ValueError:
        return {
            "error": "DeepSeek вернул ответ не в формате JSON",
            "status": response.status_code,
            "body": response.text[:1000],
        }, 502


def request_completion(prompt: str) -> tuple[dict, int]:
    """Обычный запрос Day 1 и базовый запрос для сравнения Day 2."""
    return _post_to_deepseek(DEEPSEEK_API_URL, _base_payload(prompt))


def request_day_two_comparison(
    prompt: str,
    *,
    model: str,
    output_format: str,
    max_tokens: int,
    stop_sequence: str,
) -> tuple[dict, int]:
    """Делает контролируемый запрос и сразу после него — такой же без параметров."""
    controlled_url, controlled_payload, baseline_payload = _day_two_payloads(
        prompt,
        model=model,
        output_format=output_format,
        max_tokens=max_tokens,
        stop_sequence=stop_sequence,
    )
    request_preview = _day_two_request_preview(
        controlled_url,
        controlled_payload,
        baseline_payload,
    )

    controlled_raw, controlled_status = _post_to_deepseek(
        controlled_url,
        controlled_payload,
    )

    if not 200 <= controlled_status < 300:
        return _with_request_preview(controlled_raw, request_preview), controlled_status

    controlled, error = _normalize_controlled_response(
        controlled_raw,
        output_format,
        prompt,
    )
    if error:
        return {"error": error, "request_preview": request_preview}, 502

    baseline_raw, baseline_status = _post_to_deepseek(
        DEEPSEEK_API_URL,
        baseline_payload,
    )
    if not 200 <= baseline_status < 300:
        return _with_request_preview(baseline_raw, request_preview), baseline_status

    baseline, error = _normalize_text_response(baseline_raw)
    if error:
        return {"error": error, "request_preview": request_preview}, 502

    settings = _day_two_settings(model, output_format, max_tokens, stop_sequence)
    return {
        "controlled": controlled,
        "baseline": baseline,
        "settings": settings,
        "analysis": _build_analysis(controlled, baseline, settings),
        "request_preview": request_preview,
    }, 200


def request_day_two_meta_analysis(
    prompt: str,
    *,
    model: str,
    output_format: str,
    max_tokens: int,
    stop_sequence: str,
    controlled: dict[str, Any],
    baseline: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    controlled_url, _, _ = _day_two_payloads(
        prompt,
        model=model,
        output_format=output_format,
        max_tokens=max_tokens,
        stop_sequence=stop_sequence,
    )
    comparison_context = {
        "original_prompt": prompt,
        "with_parameters": {
            "endpoint": controlled_url,
            "system_prompt": True,
            "output_format": output_format,
            "max_tokens": max_tokens,
            "stop_sequence": stop_sequence or None,
            "strict_json_schema": output_format == "json",
            "result": _meta_answer_snapshot(controlled),
        },
        "without_parameters": {
            "endpoint": DEEPSEEK_API_URL,
            "system_prompt": False,
            "output_format": "model_default",
            "max_tokens": None,
            "stop_sequence": None,
            "strict_json_schema": False,
            "result": _meta_answer_snapshot(baseline),
        },
    }
    meta_payload = _base_payload(
        json.dumps(comparison_context, ensure_ascii=False, indent=2),
        model=model,
        system_prompt=META_ANALYSIS_SYSTEM_PROMPT,
    )
    meta_payload["max_tokens"] = 400

    response_json, status_code = _post_to_deepseek(DEEPSEEK_API_URL, meta_payload)
    if not 200 <= status_code < 300:
        return response_json, status_code

    analysis, error = _normalize_text_response(response_json)
    if error:
        return {"error": error}, 502
    return analysis, 200


def stream_day_two_answer(
    prompt: str,
    *,
    variant: str,
    model: str,
    output_format: str,
    max_tokens: int,
    stop_sequence: str,
) -> Iterator[dict[str, Any]]:
    """Проксирует один из двух ответов DeepSeek как последовательность событий."""
    controlled_url, controlled_payload, baseline_payload = _day_two_payloads(
        prompt,
        model=model,
        output_format=output_format,
        max_tokens=max_tokens,
        stop_sequence=stop_sequence,
    )
    if variant == "controlled":
        api_url = controlled_url
        payload = dict(controlled_payload)
    elif variant == "baseline":
        api_url = DEEPSEEK_API_URL
        payload = dict(baseline_payload)
    else:
        yield {"type": "error", "variant": variant, "error": "Неизвестный тип ответа"}
        return

    payload["stream"] = True
    yield {
        "type": "start",
        "variant": variant,
        "request": {"method": "POST", "url": api_url, "body": payload},
    }

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        yield {
            "type": "error",
            "variant": variant,
            "error": "Не задан DEEPSEEK_API_KEY в файле .env",
        }
        return

    try:
        response = requests.post(
            api_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            stream=True,
            timeout=120,
        )
    except requests.RequestException as error:
        yield {
            "type": "error",
            "variant": variant,
            "error": "Не удалось связаться с DeepSeek: " + str(error),
        }
        return

    if not 200 <= response.status_code < 300:
        yield {
            "type": "error",
            "variant": variant,
            "error": _stream_error_message(response),
        }
        response.close()
        return

    content_parts: list[str] = []
    argument_parts: list[str] = []
    finish_reason: str | None = None
    response_model: str | None = None
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

            if isinstance(chunk.get("model"), str):
                response_model = chunk["model"]
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

            content = delta.get("content")
            if isinstance(content, str) and content:
                content_parts.append(content)
                yield {"type": "delta", "variant": variant, "content": content}

            tool_calls = delta.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for tool_call in tool_calls:
                function = tool_call.get("function") if isinstance(tool_call, dict) else None
                arguments = function.get("arguments") if isinstance(function, dict) else None
                if isinstance(arguments, str) and arguments:
                    argument_parts.append(arguments)
                    yield {"type": "delta", "variant": variant, "content": arguments}
    except requests.RequestException as error:
        yield {
            "type": "error",
            "variant": variant,
            "error": "Поток DeepSeek был прерван: " + str(error),
        }
        return
    finally:
        response.close()

    message: dict[str, Any]
    if variant == "controlled" and output_format == "json":
        message = {
            "tool_calls": [
                {
                    "function": {
                        "name": "return_country_search",
                        "arguments": "".join(argument_parts),
                    }
                }
            ]
        }
    else:
        message = {"content": "".join(content_parts)}

    response_json: dict[str, Any] = {
        "model": response_model or model,
        "choices": [{"finish_reason": finish_reason, "message": message}],
    }
    if usage is not None:
        response_json["usage"] = usage

    if variant == "controlled":
        answer, error = _normalize_controlled_response(response_json, output_format, prompt)
    else:
        answer, error = _normalize_text_response(response_json)
    if error:
        yield {"type": "error", "variant": variant, "error": error}
        return

    yield {"type": "done", "variant": variant, "answer": answer}


def _day_two_payloads(
    prompt: str,
    *,
    model: str,
    output_format: str,
    max_tokens: int,
    stop_sequence: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    base_system_prompt = (
        COUNTRY_JSON_SYSTEM_PROMPT
        if output_format == "json"
        else COUNTRY_ONLY_SYSTEM_PROMPT
    )
    length_instruction = (
        f"Сформулируй полностью завершённый ответ не более чем за {max_tokens} "
        "токенов вывода. Заранее планируй объём, пиши компактно и не начинай часть "
        "ответа, которую не успеешь закончить в этом лимите."
    )
    controlled_payload = _base_payload(
        prompt,
        model=model,
        system_prompt=f"{base_system_prompt} {length_instruction}",
    )
    baseline_payload = _base_payload(prompt, model=model)
    controlled_payload["max_tokens"] = max_tokens

    if output_format == "json":
        controlled_payload["tools"] = [STRUCTURED_COUNTRY_TOOL]
        controlled_payload["tool_choice"] = {
            "type": "function",
            "function": {"name": "return_country_search"},
        }
        controlled_url = DEEPSEEK_BETA_API_URL
    else:
        controlled_payload["response_format"] = {"type": "text"}
        if stop_sequence:
            controlled_payload["messages"][0]["content"] += (
                " Заверши ответ точным маркером: " + stop_sequence
            )
            controlled_payload["stop"] = [stop_sequence]
        controlled_url = DEEPSEEK_API_URL
    return controlled_url, controlled_payload, baseline_payload


def _day_two_request_preview(
    controlled_url: str,
    controlled_payload: dict[str, Any],
    baseline_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "controlled": {
            "method": "POST",
            "url": controlled_url,
            "body": controlled_payload,
        },
        "baseline": {
            "method": "POST",
            "url": DEEPSEEK_API_URL,
            "body": baseline_payload,
        },
    }


def _day_two_settings(
    model: str,
    output_format: str,
    max_tokens: int,
    stop_sequence: str,
) -> dict[str, Any]:
    return {
        "model": model,
        "output_format": output_format,
        "max_tokens": max_tokens,
        "stop_sequence": stop_sequence or None,
    }


def _stream_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"DeepSeek вернул ошибку HTTP {response.status_code}"

    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    if isinstance(error, str):
        return error
    return f"DeepSeek вернул ошибку HTTP {response.status_code}"


def _meta_answer_snapshot(answer: dict[str, Any]) -> dict[str, Any]:
    content = answer.get("content")
    content_excerpt = content[:1200] if isinstance(content, str) else ""
    return {
        "finish_reason": answer.get("finish_reason"),
        "output_tokens": answer.get("output_tokens"),
        "characters": answer.get("characters"),
        "valid_json": answer.get("valid_json"),
        "rejected": answer.get("rejected"),
        "warning": answer.get("warning"),
        "content_excerpt": content_excerpt,
    }


def _with_request_preview(response_json: dict, request_preview: dict) -> dict:
    result = dict(response_json)
    result["request_preview"] = request_preview
    return result


def _normalize_controlled_response(
    response_json: dict[str, Any], output_format: str, prompt: str
) -> tuple[dict[str, Any] | None, str | None]:
    if output_format == "json":
        return _normalize_structured_response(response_json, prompt)
    return _normalize_text_response(response_json)


def _normalize_text_response(
    response_json: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    choice, error = _first_choice(response_json)
    if error:
        return None, error

    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        return None, "DeepSeek не вернул текст ответа"

    return _response_summary(response_json, choice, content), None


def _normalize_structured_response(
    response_json: dict[str, Any], prompt: str
) -> tuple[dict[str, Any] | None, str | None]:
    choice, error = _first_choice(response_json)
    if error:
        return None, error

    message = choice.get("message")
    tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
    if not isinstance(tool_calls, list) or not tool_calls:
        return None, "DeepSeek не вернул структурированный JSON"

    function = tool_calls[0].get("function") if isinstance(tool_calls[0], dict) else None
    if not isinstance(function, dict) or function.get("name") != "return_country_search":
        return None, "DeepSeek вернул неожиданную структуру ответа"

    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        return None, "DeepSeek не вернул аргументы JSON"

    summary = _response_summary(response_json, choice, arguments)

    try:
        value = json.loads(arguments)
    except json.JSONDecodeError:
        summary["valid_json"] = False
        summary["warning"] = (
            "JSON обрезан лимитом max_tokens. Увеличьте лимит и повторите запрос."
            if choice.get("finish_reason") == "length"
            else "DeepSeek вернул некорректный JSON; ниже показан сырой фрагмент."
        )
        return summary, None

    if (
        not isinstance(value, dict)
        or not isinstance(value.get("valid"), bool)
        or not isinstance(value.get("countries"), list)
    ):
        return None, "JSON не соответствует ожидаемой схеме"

    if not value["valid"]:
        summary["content"] = "Некорректный запрос"
        summary["characters"] = len(summary["content"])
        summary["rejected"] = True
        summary["valid_json"] = False
        return summary, None

    countries = []
    for country in value["countries"]:
        normalized_country = _normalize_country(country)
        if normalized_country is not None:
            countries.append(normalized_country)
    public_value = {
        "query": {
            "original": prompt,
            "type": "semantic",
        },
        "result": {
            "total": len(countries),
            "countries": countries,
        },
    }
    content = json.dumps(public_value, ensure_ascii=False, indent=2)
    summary["content"] = content
    summary["characters"] = len(content)
    summary["valid_json"] = True
    return summary, None


def _first_choice(
    response_json: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, "DeepSeek не вернул вариант ответа"
    return choices[0], None


def _normalize_country(country: Any) -> dict[str, str] | None:
    if not isinstance(country, dict):
        return None

    code = country.get("code")
    name = country.get("name")
    name_ru = country.get("nameRu")
    if not all(isinstance(field, str) for field in (code, name, name_ru)):
        return None

    normalized_code = code.strip().upper()
    normalized_name = name.strip()
    normalized_name_ru = name_ru.strip()
    if (
        len(normalized_code) != 2
        or not normalized_code.isalpha()
        or not normalized_name
        or not normalized_name_ru
    ):
        return None

    return {
        "code": normalized_code,
        "name": normalized_name,
        "nameRu": normalized_name_ru,
    }


def _response_summary(
    response_json: dict[str, Any], choice: dict[str, Any], content: str
) -> dict[str, Any]:
    usage = response_json.get("usage")
    completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
    return {
        "content": content,
        "characters": len(content),
        "finish_reason": choice.get("finish_reason") or "не указан",
        "output_tokens": completion_tokens if isinstance(completion_tokens, int) else None,
        "model": (
            response_json.get("model")
            if isinstance(response_json.get("model"), str)
            else None
        ),
    }


def _build_analysis(
    controlled: dict[str, Any], baseline: dict[str, Any], settings: dict[str, Any]
) -> list[str]:
    controlled_characters = controlled["characters"]
    baseline_characters = baseline["characters"]
    if controlled_characters < baseline_characters:
        size = "Контролируемый ответ короче базового."
    elif controlled_characters > baseline_characters:
        size = "Контролируемый ответ длиннее базового."
    else:
        size = "Оба ответа одинаковы по длине."

    if controlled.get("rejected"):
        format_note = "Запрос отклонён: сервис отвечает только на запросы о списках стран."
    elif settings["output_format"] == "json" and controlled.get("valid_json"):
        format_note = "Формат: JSON соответствует схеме query + result.countries."
    elif settings["output_format"] == "json":
        format_note = controlled.get("warning", "JSON не удалось проверить.")
    else:
        format_note = "Формат: для контролируемого ответа явно передан обычный текст."
    length_note = f"Лимит: в API передано до {settings['max_tokens']} токенов."
    analysis = [
        f"Модель: {settings['model']}.",
        format_note,
        length_note,
        f"Длина: с параметрами — {controlled_characters} симв., без параметров — {baseline_characters} симв. {size}",
    ]
    if settings["stop_sequence"]:
        analysis.append(
            f"Стоп-последовательность передана в API: {settings['stop_sequence']}."
        )
    return analysis

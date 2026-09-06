import json

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context

from ...navigation import get_day
from ...services.deepseek import (
    DAY_FOUR_TEMPERATURE_MAX,
    DAY_FOUR_TEMPERATURE_MIN,
    DAY_FOUR_TEMPERATURES,
    format_temperature,
    get_model,
    request_day_four_answers,
    request_day_four_prompt,
    stream_day_four_answers,
)


bp = Blueprint("day04", __name__, url_prefix="/days/4")

MODELS = {"deepseek-v4-flash", "deepseek-v4-pro"}


@bp.get("/")
def index():
    return render_template(
        "days/day04.html",
        active_day=get_day(4),
        model=get_model(),
        temperatures=[
            {"value": value, "label": format_temperature(value)}
            for value in DAY_FOUR_TEMPERATURES
        ],
        temperature_min=DAY_FOUR_TEMPERATURE_MIN,
        temperature_max=DAY_FOUR_TEMPERATURE_MAX,
    )


@bp.post("/api/answer")
def answer():
    params, error_response = _answer_params()
    if error_response is not None:
        return error_response

    response_json, status_code = request_day_four_answers(
        params["prompt"],
        model=params["model"],
        temperature=params["temperature"],
    )
    return jsonify(response_json), status_code


@bp.post("/api/answer/stream")
def answer_stream():
    params, error_response = _answer_params()
    if error_response is not None:
        return error_response

    def generate_events():
        for event in stream_day_four_answers(
            params["prompt"],
            model=params["model"],
            temperature=params["temperature"],
        ):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return Response(
        stream_with_context(generate_events()),
        mimetype="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@bp.post("/api/prompt")
def generate_prompt():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Ожидался JSON-объект с моделью"}), 400

    model = payload.get("model")
    if model not in MODELS:
        return jsonify({"error": "Выберите модель DeepSeek"}), 400

    response_json, status_code = request_day_four_prompt(model=model)
    return jsonify(response_json), status_code


def _answer_params():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, (
            jsonify({"error": "Ожидался JSON-объект с параметрами запроса"}),
            400,
        )

    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        return None, (jsonify({"error": "Введите запрос"}), 400)
    if len(prompt.strip()) > 10_000:
        return None, (
            jsonify({"error": "Запрос не должен превышать 10 000 символов"}),
            400,
        )

    model = payload.get("model")
    if model not in MODELS:
        return None, (jsonify({"error": "Выберите модель DeepSeek"}), 400)

    temperature, error_response = _temperature(payload.get("temperature"))
    if error_response is not None:
        return None, error_response

    return {
        "prompt": prompt.strip(),
        "model": model,
        "temperature": temperature,
    }, None


def _temperature(value):
    """Либо "all" — все температуры задания, либо одно число из диапазона API."""
    if value == "all":
        return "all", None

    # bool — подкласс int, а True в роли температуры почти наверняка ошибка клиента.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, (jsonify({"error": "Укажите температуру числом"}), 400)

    if not DAY_FOUR_TEMPERATURE_MIN <= value <= DAY_FOUR_TEMPERATURE_MAX:
        return None, (
            jsonify(
                {
                    "error": (
                        "DeepSeek принимает температуру от "
                        f"{format_temperature(DAY_FOUR_TEMPERATURE_MIN)} до "
                        f"{format_temperature(DAY_FOUR_TEMPERATURE_MAX)}"
                    )
                }
            ),
            400,
        )

    return float(value), None

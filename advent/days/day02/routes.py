import json

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context

from ...navigation import get_day
from ...services.deepseek import (
    get_model,
    request_day_two_comparison,
    request_day_two_meta_analysis,
    stream_day_two_answer,
)


bp = Blueprint("day02", __name__, url_prefix="/days/2")


@bp.get("/")
def index():
    return render_template(
        "days/day02.html",
        active_day=get_day(2),
        model=get_model(),
    )


@bp.post("/api/compare")
def compare():
    params, error_response = _comparison_params()
    if error_response is not None:
        return error_response

    response_json, status_code = request_day_two_comparison(
        params["prompt"],
        model=params["model"],
        output_format=params["output_format"],
        max_tokens=params["max_tokens"],
        stop_sequence=params["stop_sequence"],
    )
    return jsonify(response_json), status_code


@bp.post("/api/compare/stream")
def compare_stream():
    params, error_response = _comparison_params()
    if error_response is not None:
        return error_response

    payload = request.get_json(silent=True)
    variant = payload.get("variant")
    if variant not in {"controlled", "baseline"}:
        return jsonify({"error": "Выберите тип сравниваемого ответа"}), 400

    def generate_events():
        for event in stream_day_two_answer(
            params["prompt"],
            variant=variant,
            model=params["model"],
            output_format=params["output_format"],
            max_tokens=params["max_tokens"],
            stop_sequence=params["stop_sequence"],
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


@bp.post("/api/compare/meta-analysis")
def meta_analysis():
    params, error_response = _comparison_params()
    if error_response is not None:
        return error_response

    payload = request.get_json(silent=True)
    controlled = payload.get("controlled")
    baseline = payload.get("baseline")
    if not _is_answer_summary(controlled) or not _is_answer_summary(baseline):
        return jsonify({"error": "Не получены оба ответа для мета-анализа"}), 400

    response_json, status_code = request_day_two_meta_analysis(
        params["prompt"],
        model=params["model"],
        output_format=params["output_format"],
        max_tokens=params["max_tokens"],
        stop_sequence=params["stop_sequence"],
        controlled=controlled,
        baseline=baseline,
    )
    return jsonify(response_json), status_code


def _comparison_params():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, (jsonify({"error": "Ожидался JSON-объект с параметрами запроса"}), 400)

    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        return None, (jsonify({"error": "Введите запрос"}), 400)

    output_format = payload.get("output_format")
    if output_format not in {"text", "json"}:
        return None, (jsonify({"error": "Выберите формат ответа"}), 400)

    model = payload.get("model")
    if model not in {"deepseek-v4-flash", "deepseek-v4-pro"}:
        return None, (jsonify({"error": "Выберите модель DeepSeek"}), 400)

    max_tokens = payload.get("max_tokens")
    if (
        isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or not 16 <= max_tokens <= 16384
    ):
        return None, (
            jsonify({"error": "Лимит должен быть целым числом от 16 до 16384"}),
            400,
        )

    stop_sequence = payload.get("stop_sequence", "")
    if stop_sequence not in {"", "### END ###", "<END>", "[STOP]"}:
        return None, (jsonify({"error": "Некорректная стоп-последовательность"}), 400)

    if output_format == "json" and stop_sequence:
        return None, (
            jsonify(
                {"error": "Для строгого JSON оставьте стоп-последовательность пустой"}
            ),
            400,
        )

    return (
        {
            "prompt": prompt.strip(),
            "model": model,
            "output_format": output_format,
            "max_tokens": max_tokens,
            "stop_sequence": stop_sequence,
        },
        None,
    )


def _is_answer_summary(value):
    return (
        isinstance(value, dict)
        and isinstance(value.get("content"), str)
        and len(value["content"]) <= 50_000
    )

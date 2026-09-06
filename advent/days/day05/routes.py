import json

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context

from ...navigation import get_day
from ...services.deepseek import (
    get_model,
    request_day_five_prompt,
    stream_day_five_analysis,
)
from ...services.openrouter import DAY_FIVE_MODELS, stream_day_five_answers


bp = Blueprint("day05", __name__, url_prefix="/days/5")

MODELS = {"deepseek-v4-flash", "deepseek-v4-pro"}

# Мета-анализ всегда идёт на старшую модель DeepSeek: разбор трёх ответов
# сложнее, чем сами ответы, и на flash он получается поверхностным.
ANALYST_MODEL = "deepseek-v4-pro"


@bp.get("/")
def index():
    return render_template(
        "days/day05.html",
        active_day=get_day(5),
        model=get_model(),
        models=DAY_FIVE_MODELS,
        analyst_model=ANALYST_MODEL,
    )


@bp.post("/api/prompt")
def generate_prompt():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Ожидался JSON-объект с моделью"}), 400

    model = payload.get("model")
    if model not in MODELS:
        return jsonify({"error": "Выберите модель DeepSeek"}), 400

    response_json, status_code = request_day_five_prompt(model=model)
    return jsonify(response_json), status_code


@bp.post("/api/compare/stream")
def compare_stream():
    prompt, error_response = _prompt_param()
    if error_response is not None:
        return error_response

    def generate_events():
        for event in _compare_events(prompt):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return Response(
        stream_with_context(generate_events()),
        mimetype="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _compare_events(prompt: str):
    """Три модели через OpenRouter, затем мета-анализ через DeepSeek."""
    results: dict[str, dict] = {}

    for event in stream_day_five_answers(prompt):
        if event.get("type") == "models_complete":
            results = event.get("results", {})
            continue
        yield event
        if event.get("type") == "error":
            return

    if not results:
        return

    yield {"type": "analysis_start", "model": ANALYST_MODEL}
    analysis, error = yield from stream_day_five_analysis(
        prompt,
        model=ANALYST_MODEL,
        results=results,
    )
    if error:
        yield {"type": "analysis_error", "error": error}
    else:
        yield {"type": "analysis_done", "analysis": analysis}

    yield {"type": "complete", "results_count": len(results)}


def _prompt_param():
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

    return prompt.strip(), None

import json

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context

from ...navigation import get_day
from ...services.deepseek import (
    get_model,
    request_day_three_solutions,
    request_day_three_task,
    stream_day_three_solutions,
)


bp = Blueprint("day03", __name__, url_prefix="/days/3")


@bp.get("/")
def index():
    return render_template(
        "days/day03.html",
        active_day=get_day(3),
        model=get_model(),
    )


@bp.post("/api/solve")
def solve():
    params, error_response = _solve_params()
    if error_response is not None:
        return error_response

    response_json, status_code = request_day_three_solutions(
        params["task"],
        model=params["model"],
        approach=params["approach"],
    )
    return jsonify(response_json), status_code


@bp.post("/api/solve/stream")
def solve_stream():
    params, error_response = _solve_params()
    if error_response is not None:
        return error_response

    def generate_events():
        for event in stream_day_three_solutions(
            params["task"],
            model=params["model"],
            approach=params["approach"],
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


@bp.post("/api/task")
def generate_task():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Ожидался JSON-объект с моделью"}), 400

    model = payload.get("model")
    if model not in {"deepseek-v4-flash", "deepseek-v4-pro"}:
        return jsonify({"error": "Выберите модель DeepSeek"}), 400

    response_json, status_code = request_day_three_task(model=model)
    return jsonify(response_json), status_code


def _solve_params():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, (
            jsonify({"error": "Ожидался JSON-объект с параметрами задачи"}),
            400,
        )

    task = payload.get("task", "")
    if not isinstance(task, str) or not task.strip():
        return None, (jsonify({"error": "Введите задачу"}), 400)
    if len(task.strip()) > 10_000:
        return None, (
            jsonify({"error": "Задача не должна превышать 10 000 символов"}),
            400,
        )

    model = payload.get("model")
    if model not in {"deepseek-v4-flash", "deepseek-v4-pro"}:
        return None, (jsonify({"error": "Выберите модель DeepSeek"}), 400)

    approach = payload.get("approach")
    if approach not in {
        "all",
        "direct",
        "step_by_step",
        "generated_prompt",
        "experts",
    }:
        return None, (jsonify({"error": "Выберите способ решения"}), 400)

    return {
        "task": task.strip(),
        "model": model,
        "approach": approach,
    }, None

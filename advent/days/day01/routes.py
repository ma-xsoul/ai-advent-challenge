from flask import Blueprint, jsonify, render_template, request

from ...navigation import get_day
from ...services.deepseek import get_model, request_completion


bp = Blueprint("day01", __name__, url_prefix="/days/1")


@bp.get("/")
def index():
    return render_template(
        "days/day01.html",
        active_day=get_day(1),
        model=get_model(),
    )


@bp.post("/api/chat")
def chat():
    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return jsonify({"error": "Ожидался JSON-объект с полем prompt"}), 400

    prompt = payload.get("prompt", "")

    if not isinstance(prompt, str) or not prompt.strip():
        return jsonify({"error": "Введите запрос"}), 400

    response_json, status_code = request_completion(prompt.strip())
    return jsonify(response_json), status_code

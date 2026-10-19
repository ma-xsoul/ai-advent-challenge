"""
День 35: реальная задача — AI-конспект и тренажёр курса.

Задача: вернуться к любому дню курса — вспомнить, спросить и
потренироваться, не перечитывая сотни сообщений чата, 35 веток и наши
разговоры. Источники — advent/course/sources.py, конспект, вопросы и
проверка ответов — advent/course/tutor.py; всё лежит в data/day35.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, render_template, request

from ...agent.transport import TransportError
from ...course.sources import KINDS, collect, default_chat_paths, default_talk_paths
from ...course.tutor import DATA, MODEL, CourseError, CourseStore, ask, build, grade, pick
from ...navigation import get_day
from ..rag_pages import embedder, ndjson_stream, transport


bp = Blueprint("day35", __name__, url_prefix="/days/35")

MAX_TEXT = 1500


def _store() -> CourseStore:
    return CourseStore(current_app.config.get("DAY35_DATA") or DATA)


def _sources():
    chat = Path(current_app.config.get("DAY35_CHAT_DIR") or (Path(current_app.config.get("DAY35_DATA") or DATA) / "chat"))
    talks = current_app.config.get("DAY35_TALKS")
    root = Path(current_app.config.get("DAY35_ROOT")) if current_app.config.get("DAY35_ROOT") else None
    kwargs = {"root": root} if root else {}
    return collect(chat=default_chat_paths(chat), talks=default_talk_paths() if talks is None else [Path(path) for path in talks], **kwargs)


def _days(course: dict[str, Any], progress: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"day": int(key), "title": item["title"], "questions": len(item.get("questions") or []),
         "progress": progress.get(key) or {"answers": 0, "correct": 0, "partial": 0, "last": {}}}
        for key, item in (course.get("days") or {}).items()
    ]


def _day(value: Any) -> int | None:
    try:
        day = int(value)
    except (TypeError, ValueError):
        return None
    return day if 1 <= day <= 35 else None


@bp.get("/")
def index():
    store = _store()
    course = store.course()
    return render_template(
        "days/day35.html",
        active_day=get_day(35),
        course={"built_at": course.get("built_at"), "sources": course.get("sources"), "model": course.get("model") or MODEL},
        days=_days(course, store.progress()),
        kinds=KINDS,
    )


@bp.post("/api/build")
def rebuild():
    store, embedder_, transport_, sources = _store(), embedder(), transport(), _sources()

    def work(emit):
        build(sources, store=store, embedder=embedder_, transport=transport_, emit=emit)
        emit({"type": "done"})

    return ndjson_stream(work)


@bp.get("/api/days/<int:day>")
def chapter(day: int):
    store = _store()
    found = (store.course().get("days") or {}).get(str(day))
    if not found:
        return jsonify({"error": "Этого дня в конспекте нет — соберите курс"}), 404
    return jsonify({**found, "progress": store.progress().get(str(day))})


@bp.post("/api/ask")
def ask_question():
    data = request.get_json(silent=True)
    question = str(data.get("question") or "").strip() if isinstance(data, dict) else ""
    if not question:
        return jsonify({"error": "Напишите вопрос"}), 400
    if len(question) > MAX_TEXT:
        return jsonify({"error": f"Вопрос длиннее {MAX_TEXT} символов"}), 400
    store = _store()
    if not store.course().get("days"):
        return jsonify({"error": "Сначала соберите курс"}), 409
    try:
        return jsonify(ask(question, store=store, embedder=embedder(), transport=transport(), day=_day(data.get("day"))))
    except TransportError as error:
        return jsonify({"error": str(error)}), 502


@bp.get("/api/quiz")
def quiz():
    try:
        return jsonify(pick(_store().course(), _day(request.args.get("day"))))
    except CourseError as error:
        return jsonify({"error": str(error)}), 409


@bp.post("/api/quiz")
def check():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Нужны day, index и answer"}), 400
    day, answer = _day(data.get("day")), str(data.get("answer") or "").strip()
    index = data.get("index") if isinstance(data.get("index"), int) else -1
    if day is None or not answer:
        return jsonify({"error": "Нужны день и ответ"}), 400
    if len(answer) > MAX_TEXT:
        return jsonify({"error": f"Ответ длиннее {MAX_TEXT} символов"}), 400
    store = _store()
    try:
        result = grade(store.course(), day, index, answer, transport=transport())
    except CourseError as error:
        return jsonify({"error": str(error)}), 400
    except TransportError as error:
        return jsonify({"error": str(error)}), 502
    progress = store.record(day, index, result["verdict"])
    return jsonify({**result, "progress": progress.get(str(day))})

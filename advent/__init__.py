from __future__ import annotations

from flask import Flask, redirect, url_for
from dotenv import load_dotenv

from .days.day01.routes import bp as day01_blueprint
from .days.day01.routes import chat as day01_chat
from .days.day02.routes import bp as day02_blueprint
from .navigation import DAYS


def create_app(test_config: dict | None = None) -> Flask:
    load_dotenv()

    app = Flask(__name__)

    if test_config:
        app.config.from_mapping(test_config)

    app.register_blueprint(day01_blueprint)
    app.register_blueprint(day02_blueprint)

    # Сохраняем старый адрес, чтобы существующие клиенты Day 1 не сломались.
    app.add_url_rule(
        "/api/chat",
        endpoint="legacy_day01_chat",
        view_func=day01_chat,
        methods=["POST"],
    )

    @app.get("/")
    def index():
        return redirect(url_for("day01.index"))

    @app.context_processor
    def inject_navigation():
        visible_days = tuple(day for day in DAYS if day.visible)
        completed_days = sum(day.status == "Готово" for day in visible_days)
        return {
            "advent_days": visible_days,
            "completed_days": completed_days,
        }

    return app

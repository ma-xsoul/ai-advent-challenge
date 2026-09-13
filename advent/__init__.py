from __future__ import annotations

from flask import Flask, redirect, url_for
from dotenv import load_dotenv

from .days.day01.routes import bp as day01_blueprint
from .days.day01.routes import chat as day01_chat
from .days.day02.routes import bp as day02_blueprint
from .days.day03.routes import bp as day03_blueprint
from .days.day04.routes import bp as day04_blueprint
from .days.day05.routes import bp as day05_blueprint
from .days.day06.routes import bp as day06_blueprint
from .agent import Agent, AgentConfig, AgentRegistry
from .navigation import DAYS


# Конфиг агента по умолчанию для чата дней 6+. Всё, что раньше уходило
# параметрами в каждый вызов, теперь собрано здесь.
DEFAULT_AGENT_CONFIG = AgentConfig(
    name="advent",
    system_prompt=(
        "Ты помощник в учебной лаборатории. Отвечай по-русски, по делу и без "
        "лишних вступлений. Если вопрос опирается на предыдущие реплики — "
        "опирайся на них."
    ),
    temperature=0.7,
    max_tokens=800,
)


def create_app(test_config: dict | None = None) -> Flask:
    load_dotenv()

    app = Flask(__name__)

    if test_config:
        app.config.from_mapping(test_config)

    # Один реестр на приложение. Транспорт берётся из конфига, чтобы тесты
    # подставляли заглушку и агент проверялся без сети.
    transport = app.config.get("AGENT_TRANSPORT")
    app.extensions["agent_registry"] = AgentRegistry(
        lambda: Agent(DEFAULT_AGENT_CONFIG, transport=transport)
    )

    app.register_blueprint(day01_blueprint)
    app.register_blueprint(day02_blueprint)
    app.register_blueprint(day03_blueprint)
    app.register_blueprint(day04_blueprint)
    app.register_blueprint(day05_blueprint)
    app.register_blueprint(day06_blueprint)

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

from __future__ import annotations

from pathlib import Path

from flask import Flask, redirect, url_for
from dotenv import load_dotenv

from .days.day01.routes import bp as day01_blueprint
from .days.day01.routes import chat as day01_chat
from .days.day02.routes import bp as day02_blueprint
from .days.day03.routes import bp as day03_blueprint
from .days.day04.routes import bp as day04_blueprint
from .days.day05.routes import bp as day05_blueprint
from .days.day06.routes import bp as day06_blueprint
from .days.day07.routes import bp as day07_blueprint
from .days.day08.routes import bp as day08_blueprint
from .days.day09.routes import bp as day09_blueprint
from .days.day10.routes import bp as day10_blueprint
from .days.day11.routes import bp as day11_blueprint
from .days.day12.routes import bp as day12_blueprint
from .days.day13.routes import bp as day13_blueprint
from .days.day14.routes import bp as day14_blueprint
from .days.day15.routes import bp as day15_blueprint
from .days.day16.routes import bp as day16_blueprint
from .days.day17.routes import bp as day17_blueprint
from .agent import Agent, AgentConfig, AgentRegistry, JsonStore
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

    # Транспорт берётся из конфига, чтобы тесты подставляли заглушку
    # и агент проверялся без сети.
    transport = app.config.get("AGENT_TRANSPORT")

    def make_agent(saved=None):
        if saved:
            return Agent.restore(saved, transport=transport)
        return Agent(DEFAULT_AGENT_CONFIG, transport=transport)

    # Два реестра с одной фабрикой. Первый — только память процесса, как в
    # дне 6. Второй пишет стек на диск и переживает перезапуск — дни 7+.
    app.extensions["agent_registry"] = AgentRegistry(make_agent)
    app.config.setdefault("AGENT_STORE_DIR", str(Path(app.root_path).parent / "data" / "agents"))
    app.extensions["persistent_agent_registry"] = AgentRegistry(
        make_agent,
        store=JsonStore(app.config["AGENT_STORE_DIR"]),
    )

    app.register_blueprint(day01_blueprint)
    app.register_blueprint(day02_blueprint)
    app.register_blueprint(day03_blueprint)
    app.register_blueprint(day04_blueprint)
    app.register_blueprint(day05_blueprint)
    app.register_blueprint(day06_blueprint)
    app.register_blueprint(day07_blueprint)
    app.register_blueprint(day08_blueprint)
    app.register_blueprint(day09_blueprint)
    app.register_blueprint(day10_blueprint)
    app.register_blueprint(day11_blueprint)
    app.register_blueprint(day12_blueprint)
    app.register_blueprint(day13_blueprint)
    app.register_blueprint(day14_blueprint)
    app.register_blueprint(day15_blueprint)
    app.register_blueprint(day16_blueprint)
    app.register_blueprint(day17_blueprint)

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

    # Кнопка «сбросить всё» в боковой панели — чистый лист перед записью видео.
    @app.post("/api/reset-all")
    def reset_all():
        from flask import jsonify

        from .days.agent_chat import reset_everything

        return jsonify(reset_everything())

    @app.context_processor
    def inject_navigation():
        visible_days = tuple(day for day in DAYS if day.visible)
        completed_days = sum(day.status == "Готово" for day in visible_days)
        return {
            "advent_days": visible_days,
            "completed_days": completed_days,
        }

    return app

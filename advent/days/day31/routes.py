"""
День 31: ассистент разработчика.

Ассистент знает наш проект: RAG по README, docs и коду (свежий индекс
в data/day31/), MCP-сервер git — текущая ветка и изменения, команда /help
отвечает на вопросы о проекте с источниками (advent/rag/assistant.py).
Короткие команды /branch, /files, /diff, /log — прямо из git через MCP.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, jsonify, render_template, request

from ...mcp.client import GIT_SERVER
from ...navigation import get_day
from ...rag import RagIndex
from ...rag.assistant import COMMANDS, EXAMPLES, MODEL, STRATEGY, TOP_K, build_index, command, help_turn, index_path
from ..rag_pages import embedder, ndjson_stream, transport


bp = Blueprint("day31", __name__, url_prefix="/days/31")

MAX_QUESTION = 500


def _index() -> RagIndex:
    return RagIndex(index_path(current_app.config.get("DAY31_INDEX_PATH")))


def _factory():
    # Тесты подключают git-сервер в памяти, к временному репозиторию.
    return current_app.config.get("DAY31_MCP_FACTORY")


def _state(index: RagIndex, embedder_) -> dict[str, Any]:
    info = index.info()
    state = {"ready": False, "built_at": info.get("built_at"), "model": info.get("model")}
    if not info["built"]:
        return {**state, "reason": "Индекс ещё не собран — нажмите «Собрать индекс»"}
    if info.get("model") != embedder_.name:
        return {**state, "reason": f"Индекс собран моделью {info['model']}, а поиск — {embedder_.name}: пересоберите"}
    built = (info.get("strategies") or {}).get(STRATEGY)
    if not built:
        return {**state, "reason": "В индексе нет нарезки structure — пересоберите"}
    sources = index.sources()
    return {
        **state,
        "ready": True,
        "chunks": built["chunks"],
        "documents": info["corpus"]["documents"],
        "docs": [source for source in sources if source == "README.md" or source.startswith("docs/")],
        "code": sum(source.endswith(".py") for source in sources),
        "pages": sum(source.endswith(".html") for source in sources),
    }


@bp.get("/")
def index():
    return render_template(
        "days/day31.html",
        active_day=get_day(31),
        state=_state(_index(), embedder()),
        commands=[{"name": name, "title": title} for name, (_, _, title) in COMMANDS.items()],
        examples=EXAMPLES,
        model=MODEL,
        top_k=TOP_K,
        server=GIT_SERVER.as_dict(),
    )


@bp.post("/api/index")
def rebuild():
    """Собрать индекс заново потоком: кэш векторов считает только изменившиеся куски."""
    index, embedder_ = _index(), embedder()

    def work(emit):
        build_index(index, embedder_, emit)
        emit({"type": "state", **_state(index, embedder_)})

    return ndjson_stream(work)


@bp.post("/api/help")
def ask_help():
    data = request.get_json(silent=True)
    question = str(data.get("question") or "").strip() if isinstance(data, dict) else ""
    if not question:
        return jsonify({"error": "Напишите вопрос после /help"}), 400
    if len(question) > MAX_QUESTION:
        return jsonify({"error": f"Вопрос длиннее {MAX_QUESTION} символов"}), 400
    index, embedder_ = _index(), embedder()
    state = _state(index, embedder_)
    if not state["ready"]:
        return jsonify({"error": state["reason"]}), 409
    transport_, factory = transport(), _factory()

    def work(emit):
        for event in help_turn(question, index=index, embedder=embedder_, transport=transport_, factory=factory):
            emit(event)

    return ndjson_stream(work)


@bp.post("/api/command")
def run_command():
    data = request.get_json(silent=True)
    text = str(data.get("command") or "") if isinstance(data, dict) else ""
    try:
        result = command(text, factory=_factory())
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    return jsonify(result), 200 if result["ok"] else 502

"""
День 28: локальная LLM + RAG.

В неделю RAG поиск по базе проекта и так шёл на маке — векторы считала
локальная bge-m3, — но ответ писал DeepSeek в облаке. Здесь и ответ пишет
локальная модель (advent/local/rag.py): поиск и ответ с цитатами работают
без интернета. Тот же прогон контрольных вопросов делается и с облачной
моделью — качество, скорость и стабильность рядом. Прогоны сохраняются в
data/day28/runs.json: разброс между запусками — тоже стабильность.
"""

from __future__ import annotations

import os
from typing import Any

from flask import Blueprint, current_app, jsonify, render_template, request

from ...agent.transport import DeepSeekTransport
from ...local import OllamaClient, OllamaError, OllamaTransport
from ...local.rag import MODES, STRATEGY, TOP_K, Backend, RunStore, ask, iter_compare, run_id
from ...local.transport import DEFAULT_MODEL
from ...mcp.client import PROJECT_ROOT
from ...navigation import get_day
from ...rag.answer import MODEL as CLOUD_MODEL
from ...rag.rerank import ALL_PROBES
from ..day22.routes import MAX_QUESTION
from ..rag_pages import embedder, index_state, ndjson_stream, rag_index


bp = Blueprint("day28", __name__, url_prefix="/days/28")

DEFAULT_RUNS = PROJECT_ROOT / "data" / "day28" / "runs.json"


def _local_model() -> str:
    return current_app.config.get("LOCAL_MODEL") or DEFAULT_MODEL


def _backends(modes: list[str]) -> list[Backend]:
    available = {
        "local": lambda: Backend(
            "local", "на маке", _local_model(), "localhost:11434",
            current_app.config.get("LOCAL_AGENT_TRANSPORT") or OllamaTransport(),
        ),
        "cloud": lambda: Backend(
            "cloud", "в облаке", os.getenv("DEEPSEEK_MODEL") or CLOUD_MODEL, "api.deepseek.com",
            current_app.config.get("AGENT_TRANSPORT") or DeepSeekTransport(),
        ),
    }
    return [available[mode]() for mode in MODES if mode in modes]


def _modes(data: Any) -> list[str]:
    raw = data.get("modes") if isinstance(data, dict) else None
    if raw is None:
        return list(MODES)
    if not isinstance(raw, list) or not raw or any(mode not in MODES for mode in raw):
        raise ValueError("modes — непустой список из local и cloud")
    return [mode for mode in MODES if mode in raw]


def _runs() -> RunStore:
    return RunStore(current_app.config.get("DAY28_RUNS_PATH") or DEFAULT_RUNS)


def _ollama() -> dict[str, Any]:
    client = current_app.config.get("OLLAMA_CLIENT") or OllamaClient()
    model = _local_model()
    try:
        installed = any(item["name"] == model and item["chat"] for item in client.models())
        return {"ok": installed, "version": client.version(), "url": client.url, "model": model,
                "error": None if installed else f"В Ollama нет модели {model}: ollama pull {model}"}
    except OllamaError as error:
        return {"ok": False, "url": client.url, "model": model, "error": str(error)}


def _probe_dict(number: int, probe) -> dict[str, Any]:
    return {"index": number, "negative": not probe.sources, **probe.as_dict()}


@bp.get("/")
def index():
    return render_template(
        "days/day28.html",
        active_day=get_day(28),
        state=index_state(rag_index(), embedder()),
        ollama=_ollama(),
        cloud={"model": os.getenv("DEEPSEEK_MODEL") or CLOUD_MODEL, "key": bool(os.getenv("DEEPSEEK_API_KEY")) or bool(current_app.config.get("AGENT_TRANSPORT"))},
        probes=[_probe_dict(number, probe) for number, probe in enumerate(ALL_PROBES, start=1)],
        runs=[_brief(run) for run in _runs().load()],
        strategy=STRATEGY,
        top_k=TOP_K,
    )


def _ready():
    index, embedder_ = rag_index(), embedder()
    ready = index_state(index, embedder_)
    if not ready["ready"]:
        return None, (jsonify({"error": ready["reason"]}), 409)
    return (index, embedder_), None


@bp.post("/api/ask")
def ask_question():
    """Один вопрос: probe? → retrieved → answer × модели → done."""
    data = request.get_json(silent=True)
    question = str(data.get("question") or "").strip() if isinstance(data, dict) else ""
    if not question:
        return jsonify({"error": "Введите вопрос"}), 400
    if len(question) > MAX_QUESTION:
        return jsonify({"error": f"Вопрос длиннее {MAX_QUESTION} символов"}), 400
    try:
        backends = _backends(_modes(data))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    tools, error = _ready()
    if error:
        return error
    probe = next((p for p in ALL_PROBES if p.question.casefold() == question.casefold()), None)

    def work(emit):
        if probe is not None:
            emit({"type": "probe", **_probe_dict(ALL_PROBES.index(probe) + 1, probe)})
        ask(question, index=tools[0], embedder=tools[1], backends=backends, emit=emit, probe=probe)
        emit({"type": "done"})

    return ndjson_stream(work)


@bp.post("/api/compare")
def compare():
    """Контрольные вопросы по очереди, модели рядом; итог сохраняется в прогоны."""
    try:
        backends = _backends(_modes(request.get_json(silent=True) or {}))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    tools, error = _ready()
    if error:
        return error
    store = _runs()

    def work(emit):
        rows: list[dict[str, Any]] = []
        for event in iter_compare(index=tools[0], embedder=tools[1], backends=backends):
            if event["type"] == "compare_row":
                rows.append({key: event[key] for key in ("index", "question", "negative", "modes")})
            if event["type"] == "compare_summary":
                run = {
                    "id": run_id(event["started_at"]),
                    "started_at": event["started_at"],
                    "backends": [backend.as_dict() for backend in backends],
                    "summary": event["summary"],
                    "rows": rows,
                }
                event = {**event, "runs": [_brief(item) for item in store.add(run)]}
            emit(event)

    return ndjson_stream(work)


def _brief(run: dict[str, Any]) -> dict[str, Any]:
    return {key: run.get(key) for key in ("id", "started_at", "backends", "summary")}


@bp.get("/api/runs")
def runs():
    return jsonify([_brief(run) for run in _runs().load()])


@bp.get("/api/runs/<run>")
def run_detail(run: str):
    found = next((item for item in _runs().load() if item.get("id") == run), None)
    if found is None:
        return jsonify({"error": "Такого прогона нет"}), 404
    return jsonify(found)

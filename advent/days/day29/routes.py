"""День 29: настройки и промпт установленной модели, сравнение до/после."""

from __future__ import annotations

from threading import Lock
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, render_template, request

from ...local import OllamaClient, OllamaError
from ...local.optimization import MODEL, BASELINE, OPTIMIZED, PROMPTS, FORMAT_VERSIONS, ask, iter_compare, parse_settings
from ...local.rag import RunStore
from ...mcp.client import PROJECT_ROOT
from ...navigation import get_day
from ...rag.rerank import ALL_PROBES
from ..day22.routes import MAX_QUESTION
from ..rag_pages import embedder, index_state, ndjson_stream, rag_index


bp = Blueprint("day29", __name__, url_prefix="/days/29")
DEFAULT_RUNS = PROJECT_ROOT / "data" / "day29" / "runs.json"


def _client():
    return current_app.config.get("DAY29_OLLAMA_CLIENT") or current_app.config.get("OLLAMA_CLIENT") or OllamaClient()


def _store():
    return RunStore(current_app.config.get("DAY29_RUNS_PATH") or DEFAULT_RUNS)


def _brief(run):
    return {key: run.get(key) for key in ("id", "started_at", "model", "settings", "baseline", "summary", "index", "experiment_version", "format_version")}


def _snapshot(profile):
    return {
        "experiment_version": 2,
        "format_version": {"before": FORMAT_VERSIONS[BASELINE.prompt], "after": FORMAT_VERSIONS[profile.prompt]},
        "prompts": {"before": PROMPTS[BASELINE.prompt], "after": PROMPTS[profile.prompt]},
    }


def _probe(number, probe):
    return {"index": number, "negative": not probe.sources, **probe.as_dict()}


def _ollama(client):
    try:
        models = client.models()
        model = next((item for item in models if item["name"] == MODEL and item["chat"]), None)
        return {"ok": model is not None, "version": client.version(), "url": client.url,
                "model": MODEL, "quantization": model.get("quantization", "MXFP4") if model else "—",
                "error": None if model else f"Установленная модель {MODEL} недоступна в Ollama."}
    except OllamaError as error:
        return {"ok": False, "url": client.url, "model": MODEL, "quantization": "—", "error": str(error)}


@bp.get("/")
def index():
    runs = _store().load()
    return render_template(
        "days/day29.html", active_day=get_day(29),
        state=index_state(rag_index(), embedder()), model=MODEL,
        ollama=_ollama(_client()), settings=OPTIMIZED.as_dict(), baseline=BASELINE.as_dict(),
        profiles={"before": BASELINE.as_dict(), "after": OPTIMIZED.as_dict()},
        probes=[_probe(number, probe) for number, probe in enumerate(ALL_PROBES, 1)],
        runs=[_brief(run) for run in runs], latest=runs[0] if runs else None,
    )


def _arguments(*, question=False):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify({"error": "Ожидался JSON-объект"}), 400)
    try:
        profile = parse_settings(data.get("settings"))
    except (ValueError, TypeError) as error:
        return None, (jsonify({"error": str(error)}), 400)
    text = data.get("question")
    if question:
        if not isinstance(text, str) or not text.strip():
            return None, (jsonify({"error": "Введите вопрос"}), 400)
        text = text.strip()
        if len(text) > MAX_QUESTION:
            return None, (jsonify({"error": f"Вопрос длиннее {MAX_QUESTION} символов"}), 400)
    index_, embedder_ = rag_index(), embedder()
    ready = index_state(index_, embedder_)
    if not ready["ready"]:
        return None, (jsonify({"error": ready["reason"]}), 409)
    return (text, profile, index_, embedder_, _client()), None


def _acquire():
    lock = current_app.extensions.setdefault("day29_experiment_lock", Lock())
    if not lock.acquire(blocking=False):
        return None, (jsonify({"error": "Дождитесь завершения текущего эксперимента дня 29"}), 409)
    return lock, None


@bp.post("/api/ask")
def ask_question():
    args, error = _arguments(question=True)
    if error is not None:
        return error
    lock, error = _acquire()
    if error is not None:
        return error
    text, profile, index_, embedder_, client = args
    probe = next((item for item in ALL_PROBES if item.question.casefold() == text.casefold()), None)

    def work(emit):
        try:
            if probe is not None:
                emit({"type": "probe", **_probe(ALL_PROBES.index(probe) + 1, probe)})
            ask(text, index=index_, embedder=embedder_, profile=profile, client=client, emit=emit, probe=probe)
            emit({"type": "done", "settings": profile.as_dict(), "baseline": BASELINE.as_dict()})
        finally:
            lock.release()

    return ndjson_stream(work)


@bp.post("/api/compare")
def compare():
    args, error = _arguments()
    if error is not None:
        return error
    lock, error = _acquire()
    if error is not None:
        return error
    _, profile, index_, embedder_, client = args
    store = _store()
    index_info = index_.info()
    evidence = {key: index_info.get(key) for key in ("model", "built_at")}

    def work(emit):
        rows = []
        try:
            for event in iter_compare(index=index_, embedder=embedder_, profile=profile, client=client):
                if event["type"] == "compare_row":
                    rows.append({key: event[key] for key in ("index", "question", "negative", "modes")})
                if event["type"] == "compare_summary":
                    run = {"id": uuid4().hex, "started_at": event["started_at"], "model": MODEL,
                           "settings": profile.as_dict(), "baseline": BASELINE.as_dict(),
                           "index": evidence, "summary": event["summary"], "rows": rows, **_snapshot(profile)}
                    try:
                        saved = store.add(run)
                    except OSError as error:
                        emit({**event, **_snapshot(profile), "id": run["id"], "settings": profile.as_dict(), "baseline": BASELINE.as_dict(),
                              "save_error": f"Результат получен, но не сохранён: {error}"})
                    else:
                        emit({**event, **_snapshot(profile), "id": run["id"], "settings": profile.as_dict(), "baseline": BASELINE.as_dict(),
                              "runs": [_brief(item) for item in saved]})
                    continue
                emit(event)
        finally:
            lock.release()

    return ndjson_stream(work)


@bp.get("/api/runs")
def runs():
    return jsonify([_brief(run) for run in _store().load()])


@bp.get("/api/runs/<run_id>")
def run_detail(run_id):
    found = next((run for run in _store().load() if run.get("id") == run_id), None)
    if found is None:
        return jsonify({"error": "Такого прогона нет"}), 404
    return jsonify(found)

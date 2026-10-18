"""
День 32: автоматизация ревью кода.

Пайплайн — .github/workflows/ai-review.yml: на каждый PR GitHub Actions
запускает scripts/ai_review.py, тот берёт diff ветки, ищет по
документации и коду проекта (RAG) и оставляет в PR комментарий с ревью
(advent/rag/review.py). Эта страница показывает пайплайн и последние его
запуски в GitHub и запускает тот же ревьюер здесь — на любой паре
веток, с поиском по словам, как в GitHub, или векторным по индексу дня 31.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import requests
from flask import Blueprint, current_app, jsonify, render_template, request

from ...mcp.client import PROJECT_ROOT
from ...navigation import get_day
from ...rag import RagIndex
from ...rag.assistant import index_path
from ...rag.review import MODEL, LexicalRetriever, VectorRetriever, run, to_markdown
from ..rag_pages import embedder, ndjson_stream, transport


bp = Blueprint("day32", __name__, url_prefix="/days/32")

WORKFLOW = Path(".github") / "workflows" / "ai-review.yml"
WORKFLOW_NAME = "AI review"
DAY_BRANCH = re.compile(r"^day-(\d+)$")


def _root() -> Path:
    return Path(current_app.config.get("DAY32_ROOT") or PROJECT_ROOT)


def _git(*args: str) -> str:
    result = subprocess.run(["git", "-C", str(_root()), *args], capture_output=True, text=True, timeout=10, check=False)
    return result.stdout if result.returncode == 0 else ""


def _branches() -> list[str]:
    names = _git("for-each-ref", "--format=%(refname:short)", "refs/heads").split()
    days = sorted((name for name in names if DAY_BRANCH.match(name)), key=lambda name: -int(DAY_BRANCH.match(name).group(1)))
    return days + sorted(name for name in names if not DAY_BRANCH.match(name))


def _default_pair(branches: list[str]) -> tuple[str, str]:
    """Последний день, у которого есть коммиты сверх предыдущего: его и ревьюим."""
    for name in branches:
        match = DAY_BRANCH.match(name)
        previous = f"day-{int(match.group(1)) - 1}" if match else None
        if previous in branches and _git("rev-list", "--count", f"{previous}..{name}").strip() not in ("", "0"):
            return name, previous
    return (branches[0], branches[1]) if len(branches) > 1 else ("", "")


def _repo() -> str | None:
    configured = current_app.config.get("DAY32_REPO")
    if configured:
        return configured
    found = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$", _git("remote", "get-url", "origin").strip())
    return found.group(1) if found else None


def _vector_ready() -> bool:
    info = RagIndex(index_path(current_app.config.get("DAY31_INDEX_PATH"))).info()
    return bool(info.get("built")) and info.get("model") == embedder().name and "structure" in (info.get("strategies") or {})


@bp.get("/")
def index():
    branches = _branches()
    head, base = _default_pair(branches)
    workflow = _root() / WORKFLOW
    repo = _repo()
    return render_template(
        "days/day32.html",
        active_day=get_day(32),
        branches=branches,
        head=head,
        base=base,
        workflow=workflow.read_text(encoding="utf-8") if workflow.exists() else None,
        workflow_path=str(WORKFLOW),
        repo=repo,
        vector_ready=_vector_ready(),
        model=MODEL,
    )


@bp.post("/api/review")
def review():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Нужны base и head"}), 400
    base, head = str(data.get("base") or ""), str(data.get("head") or "")
    branches = _branches()
    if base not in branches or head not in branches:
        return jsonify({"error": "Выберите ветки из списка"}), 400
    if base == head:
        return jsonify({"error": "Ветки совпадают — сравнивать нечего"}), 400
    kind = data.get("retriever") or "lexical"
    if kind == "vector":
        if not _vector_ready():
            return jsonify({"error": "Индекс дня 31 не собран — соберите его на странице дня 31 или ищите по словам"}), 409
        retriever_factory = lambda: VectorRetriever(RagIndex(index_path(current_app.config.get("DAY31_INDEX_PATH"))), embedder())  # noqa: E731
    elif kind == "lexical":
        retriever_factory = None
    else:
        return jsonify({"error": "retriever — lexical или vector"}), 400
    root, transport_, repo = _root(), transport(), _repo()
    retriever_ = retriever_factory() if retriever_factory else None
    link = (lambda file, line: f"https://github.com/{repo}/blob/{head}/{file}" + (f"#L{line}" if line else "")) if repo else None

    def work(emit):
        found = retriever_ or LexicalRetriever.from_project(root)
        result = run(base, head, transport=transport_, retriever=found, root=root, emit=emit)
        emit({"type": "markdown", "text": to_markdown(result, link=link)})
        emit({"type": "done"})

    return ndjson_stream(work)


@bp.get("/api/runs")
def runs():
    """Последние запуски пайплайна в GitHub — публичный API, без токена."""
    repo = _repo()
    if not repo:
        return jsonify({"error": "Не нашёл репозиторий GitHub в git remote"}), 404
    http = current_app.config.get("DAY32_HTTP") or requests
    try:
        response = http.get(
            f"https://api.github.com/repos/{repo}/actions/runs",
            params={"event": "pull_request", "per_page": 20},
            headers={"Accept": "application/vnd.github+json"},
            timeout=8,
        )
    except requests.RequestException as error:
        return jsonify({"error": f"GitHub недоступен: {error}"}), 502
    if response.status_code != 200:
        return jsonify({"error": f"GitHub ответил HTTP {response.status_code}"}), 502
    items: list[dict[str, Any]] = []
    for item in response.json().get("workflow_runs") or []:
        if item.get("name") != WORKFLOW_NAME:
            continue
        pulls = item.get("pull_requests") or []
        items.append({
            "number": item.get("run_number"),
            "status": item.get("status"),
            "conclusion": item.get("conclusion"),
            "title": item.get("display_title"),
            "branch": item.get("head_branch"),
            "pr": pulls[0].get("number") if pulls else None,
            "url": item.get("html_url"),
            "created_at": item.get("created_at"),
        })
    return jsonify({"repo": repo, "runs": items[:5], "pulls": f"https://github.com/{repo}/pulls"})

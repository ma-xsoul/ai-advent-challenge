"""
Локальный RAG и сравнение с облаком — день 28.

Цепочка та же, что в неделе RAG, только каждый шаг — на маке:

1. поиск — индекс дня 21, вектор вопроса считает локальная bge-m3,
   top-5 кусков нарезки structure (как в дне 22). Реранкер дня 23 не
   берём: на локальной модели он съел бы десятки секунд на вопрос;
2. ответ — JSON дня 24: сначала дословные цитаты, потом ответ по ним.
   Пишет gpt-oss через Ollama. Для сравнения тот же промпт на тех же
   кусках уходит DeepSeek — разница ответов только из-за модели;
3. проверка — кодом, без модели-судьи: прозвучали ли факты контрольного
   вопроса, нашлись ли цитаты в кусках, честно ли сказано «не знаю» на
   вопросе вне базы.

Оценка по заданию: качество (факты, цитаты, отказы), скорость (секунды
на ответ, токены в секунду), стабильность (сломанный JSON, ошибки,
разброс между прогонами — прогоны сохраняются на диск).
"""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from ..agent.transport import Transport, TransportError
from ..rag.answer import grade
from ..rag.embeddings import Embedder
from ..rag.grounded import ANSWER_PROMPT, QUOTE_OK, _json, _public, sources_of, verify_quote
from ..rag.index import Hit, RagIndex
from ..rag.probes import REFUSAL, Probe
from ..rag.rerank import ALL_PROBES


STRATEGY = "structure"
TOP_K = 5
MAX_TOKENS = 2000
TEMPERATURE = 0.1

MODES = ("local", "cloud")

Emit = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class Backend:
    """Кто пишет ответ: транспорт и модель. Промпт и куски у обоих одинаковые."""

    key: str
    title: str
    model: str
    where: str
    transport: Transport

    def as_dict(self) -> dict[str, str]:
        return {"key": self.key, "title": self.title, "model": self.model, "where": self.where}


# ── один ответ ───────────────────────────────────────────────────────────


def context_text(question: str, hits: list[Hit]) -> str:
    blocks = [f"[{hit.rank}] {hit.source} · {hit.section} · строки {hit.start_line}–{hit.end_line}\n{hit.text}" for hit in hits]
    return "Фрагменты:\n\n" + "\n\n---\n\n".join(blocks) + f"\n\nВопрос: {question}"


def answer(question: str, hits: list[Hit], backend: Backend) -> dict[str, Any]:
    """Ответ одной модели по кускам: JSON с цитатами, проверка цитат, замер времени."""
    payload = {
        "model": backend.model,
        "messages": [{"role": "system", "content": ANSWER_PROMPT}, {"role": "user", "content": context_text(question, hits)}],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "disabled"},
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    started = time.perf_counter()
    try:
        response = backend.transport.send(payload)
    except TransportError as error:
        return {"mode": backend.key, "error": str(error), "ms": round((time.perf_counter() - started) * 1000)}
    ms = round((time.perf_counter() - started) * 1000)
    choices = response.get("choices") or [{}]
    text = ((choices[0].get("message") or {}).get("content")) or ""
    usage = response.get("usage") or {}
    completion = usage.get("completion_tokens") or 0
    result: dict[str, Any] = {
        "mode": backend.key,
        "ms": ms,
        "prompt_tokens": usage.get("prompt_tokens") or 0,
        "completion_tokens": completion,
        "tps": round(completion / (ms / 1000), 1) if completion and ms else None,
        "finish_reason": choices[0].get("finish_reason"),
    }
    data = _json(text)
    if data is None:
        # Сломанный формат — это и есть нестабильность: ответ показываем, но без цитат.
        return {**result, "parsed": False, "status": "answer", "answer": text.strip(), "quotes": [], "sources": []}
    status = "unknown" if str(data.get("status", "")).strip().lower() == "unknown" else "answer"
    raw = [
        {"n": int(item["n"]), "text": str(item.get("text") or "").strip()}
        for item in data.get("quotes") or []
        if isinstance(item, dict) and str(item.get("n", "")).strip().isdigit() and str(item.get("text") or "").strip()
    ]
    quotes = [verify_quote(quote, hits) for quote in raw]
    text_answer = str(data.get("answer") or "").strip() or ("Не знаю" if status == "unknown" else "")
    public = _public({"quotes": quotes})
    return {
        **result,
        "parsed": True,
        "status": status,
        "answer": text_answer,
        "quotes": [{key: quote[key] for key in ("n", "text", "status", "source")} for quote in public["quotes"]],
        "sources": [{key: source[key] for key in ("n", "source", "section", "chunk_id")} for source in sources_of(text_answer, quotes, hits)],
    }


def check(result: dict[str, Any], probe: Probe | None) -> dict[str, Any] | None:
    """
    Вопрос по базе засчитан, если прозвучали ⅔ фактов. Вопрос вне базы —
    если модель сказала «не знаю» (статус или слова отказа). Цитаты и
    формат проверяются отдельно: это качество доказательств и стабильность.
    """
    if probe is None or "error" in result:
        return None
    quotes = result["quotes"]
    found = sum(quote["status"] in QUOTE_OK for quote in quotes)
    negative = not probe.sources
    unknown = result["status"] == "unknown"
    if negative:
        refused = unknown or REFUSAL.found_in(result["answer"])
        facts = {"found": int(refused), "total": 1, "passed": refused}
    else:
        graded = grade(probe, result["answer"], result["sources"])
        facts = {"found": graded["found"], "total": graded["total"], "passed": graded["passed"] and not unknown}
    return {
        "negative": negative,
        "unknown": unknown,
        "passed": facts["passed"],
        "facts_found": facts["found"],
        "facts_total": facts["total"],
        "quotes_found": found,
        "quotes_total": len(quotes),
        "parsed": result["parsed"],
    }


# ── вопрос: поиск один раз, ответы обеих моделей параллельно ─────────────


def retrieve(question: str, *, index: RagIndex, embedder: Embedder) -> tuple[list[Hit], int]:
    started = time.perf_counter()
    hits = index.search(question, embedder, STRATEGY, k=TOP_K)
    return hits, round((time.perf_counter() - started) * 1000)


def ask(
    question: str,
    *,
    index: RagIndex,
    embedder: Embedder,
    backends: list[Backend],
    emit: Emit,
    probe: Probe | None = None,
) -> dict[str, dict[str, Any]]:
    """События: retrieved → answer × len(backends) (по мере готовности)."""
    hits, search_ms = retrieve(question, index=index, embedder=embedder)
    emit({"type": "retrieved", "ms": search_ms, "hits": [{key: hit.as_dict()[key] for key in ("rank", "score", "source", "section", "chunk_id")} for hit in hits]})
    results: dict[str, dict[str, Any]] = {}

    def run(backend: Backend) -> None:
        result = answer(question, hits, backend)
        result["check"] = check(result, probe)
        results[backend.key] = result
        emit({"type": "answer", **result})

    with ThreadPoolExecutor(max_workers=len(backends)) as pool:
        list(pool.map(run, backends))
    return results


def iter_compare(
    *,
    index: RagIndex,
    embedder: Embedder,
    backends: list[Backend],
    probes: tuple[Probe, ...] = ALL_PROBES,
) -> Iterator[dict[str, Any]]:
    """
    compare_start → compare_row × N → compare_summary. Вопросы — по очереди:
    локальная модель одна на весь видеочип, параллельные запросы ей только
    мешают. Облако в каждом вопросе идёт параллельно с ней.
    """
    started = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    yield {"type": "compare_start", "total": len(probes), "backends": [backend.as_dict() for backend in backends], "started_at": started}
    rows: list[dict[str, Any]] = []
    for number, probe in enumerate(probes, start=1):
        yield {"type": "compare_question", "index": number}
        results = ask(probe.question, index=index, embedder=embedder, backends=backends, emit=lambda event: None, probe=probe)
        row = {"index": number, "question": probe.question, "negative": not probe.sources, "modes": results}
        rows.append(row)
        yield {"type": "compare_row", **row}
    yield {"type": "compare_summary", "started_at": started, "summary": summarize(rows, [backend.key for backend in backends])}


def summarize(rows: list[dict[str, Any]], modes: list[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for mode in modes:
        results = [row["modes"].get(mode) or {} for row in rows]
        ok = [result for result in results if "error" not in result]
        checks = [(row, (row["modes"].get(mode) or {}).get("check")) for row in rows]
        on_topic = [item for row, item in checks if item and not row["negative"]]
        off_topic = [item for row, item in checks if item and row["negative"]]
        times = [result["ms"] for result in ok]
        rates = [result["tps"] for result in ok if result.get("tps")]
        facts_total = sum(item["facts_total"] for item in on_topic)
        quotes_total = sum(item["quotes_total"] for item in on_topic)
        summary[mode] = {
            "passed": sum(item["passed"] for item in on_topic),
            "on_topic": sum(1 for row in rows if not row["negative"]),
            "facts": round(sum(item["facts_found"] for item in on_topic) / facts_total, 2) if facts_total else 0.0,
            "quotes": round(sum(item["quotes_found"] for item in on_topic) / quotes_total, 2) if quotes_total else 0.0,
            "refused": sum(item["passed"] for item in off_topic),
            "off_topic": sum(1 for row in rows if row["negative"]),
            "false_unknown": sum(item["unknown"] for item in on_topic),
            "avg_s": round(sum(times) / len(times) / 1000, 1) if times else None,
            "min_s": round(min(times) / 1000, 1) if times else None,
            "max_s": round(max(times) / 1000, 1) if times else None,
            "tps": round(sum(rates) / len(rates), 1) if rates else None,
            "broken_json": sum(not result.get("parsed", True) for result in ok),
            "errors": len(results) - len(ok),
        }
    return summary


# ── прогоны на диске: стабильность видна между запусками ─────────────────


class RunStore:
    """data/day28/runs.json — последние прогоны: время, модели, итог, строки."""

    KEEP = 8

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def add(self, run: dict[str, Any]) -> list[dict[str, Any]]:
        runs = [run, *self.load()][: self.KEEP]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(runs, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self.path)
        return runs

    def clear(self) -> int:
        count = len(self.load())
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        return count


def run_id(started_at: str) -> str:
    return re.sub(r"[^0-9]", "", started_at)[:14]

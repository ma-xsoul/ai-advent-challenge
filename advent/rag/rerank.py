"""
Второй этап после поиска — день 23.

Поиск дня 21 возвращает ближайшие куски, но «ближайший» не значит
«нужный». У bge-m3 похожести сжаты: нужный кусок набирает 0.56–0.74,
лучший лишний — до 0.65, а вопрос, которого в базе нет вовсе, всё равно
находит соседей на 0.54–0.58. Поэтому этапов три:

1. кандидаты с запасом — top-20 по вектору (топ-K до фильтрации);
2. порог похожести — грубое сито: режет откровенный мусор и экономит
   реранкер. Тонко отделить нужное от соседа он не может — шкала узкая;
3. реранкер — DeepSeek одним запросом оценивает каждого кандидата по
   вопросу от 0 до 10; ниже RERANK_MIN — вон, лучшие TOP_K идут модели
   (топ-K после фильтрации). Это вариант «отдельная модель» из задания:
   локального кросс-энкодера у Ollama нет.

Query rewrite — до поиска: модель переписывает вопрос в поисковый запрос
с терминами проекта. Ищем и по вопросу, и по переписанному: переписанный
запрос может принести нужный файл, но может и выдумать имена — тогда
выдача по исходному вопросу страхует. Реранкер оценивает куски по
исходному вопросу: важно, что спросил человек, а не что придумала модель.

Если после фильтра не осталось ничего, модель не вызывается: ответ —
честное «в базе этого нет». Дешевле и без шанса на выдумку.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from typing import Any

from ..agent.transport import Transport, TransportError
from .answer import MODEL, citations, generate, grade, messages
from .embeddings import Embedder
from .index import Hit, RagIndex
from .probes import NEGATIVE_PROBES, PROBES, Probe


STRATEGY = "structure"

MODES = ("base", "filter", "rewrite")
MODE_TITLES = {"base": "без фильтра", "filter": "порог + реранк", "rewrite": "+ query rewrite"}

RERANK_CHARS = 1200

# Десять вопросов по базе (день 22) и три вопроса вне её.
ALL_PROBES = PROBES + NEGATIVE_PROBES

# Сколько контрольных вопросов идёт одновременно: каждый — до шести
# запросов к модели, по очереди 13 вопросов шли бы полторы минуты.
CONCURRENCY = 3
NOTHING_FOUND = (
    "В базе не нашлось фрагментов по этому вопросу: все кандидаты отсеяны порогом или реранкером. "
    "Ответить по документам проекта нельзя."
)

REWRITE_PROMPT = (
    "Ты переписываешь вопрос пользователя в поисковый запрос для векторного поиска по коду и документации "
    "проекта ai-advent-challenge — учебного Flask-приложения с LLM-агентом на DeepSeek (память, задачи, "
    "инварианты, MCP-серверы, расписание, RAG). Убери вежливость и воду, оставь суть, добавь ключевые термины "
    "и синонимы по-русски и вероятные английские идентификаторы из кода — имена функций, классов, констант. "
    "На вопрос не отвечай. Верни одну строку до 25 слов без пояснений и кавычек."
)

RERANK_PROMPT = (
    "Ты — реранкер поиска по коду и документации проекта. Тебе дают вопрос и пронумерованные фрагменты. "
    "Оцени каждый фрагмент: насколько он помогает ответить именно на этот вопрос. 10 — прямо содержит "
    "ответ, 7 — содержит важную часть ответа, 4 — по соседней теме, ответа нет, 0 — не о том. "
    'Верни только JSON вида {"scores": {"1": 7, "2": 0}} — оценку для каждого номера.'
)


@dataclass(frozen=True)
class Settings:
    """Ручки второго этапа: порог, топ-K до и после фильтра, порог реранкера."""

    threshold: float = 0.50
    candidates: int = 20
    top_k: int = 5
    rerank_min: int = 5

    LIMITS = {"threshold": (0.0, 1.0), "candidates": (1, 40), "top_k": (1, 10), "rerank_min": (0, 10)}

    @classmethod
    def parse(cls, data: dict[str, Any] | None) -> "Settings":
        """Из запроса страницы. Чужие типы и выход за пределы — ValueError с понятным текстом."""
        data = data or {}
        values: dict[str, Any] = {}
        for name, (low, high) in cls.LIMITS.items():
            raw = data.get(name, getattr(cls, name))
            kind = float if name == "threshold" else int
            try:
                value = kind(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{name} — число") from None
            if not low <= value <= high:
                raise ValueError(f"{name} — от {low} до {high}")
            values[name] = value
        if values["top_k"] > values["candidates"]:
            raise ValueError("top_k после фильтра не может быть больше кандидатов до него")
        return cls(**values)

    def as_dict(self) -> dict[str, Any]:
        return {"threshold": self.threshold, "candidates": self.candidates, "top_k": self.top_k, "rerank_min": self.rerank_min}


# ── запросы к модели: переписать вопрос, оценить кандидатов ─────────────


def _call(transport: Transport, system: str, user: str, *, max_tokens: int, json_mode: bool = False) -> tuple[str, dict[str, Any]]:
    payload: dict[str, Any] = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    started = time.perf_counter()
    response = transport.send(payload)
    choices = response.get("choices") or [{}]
    text = ((choices[0].get("message") or {}).get("content")) or ""
    usage = response.get("usage") or {}
    return text, {"ms": round((time.perf_counter() - started) * 1000), "tokens": (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)}


def rewrite_query(question: str, transport: Transport) -> dict[str, Any]:
    text, cost = _call(transport, REWRITE_PROMPT, question, max_tokens=120)
    query = " ".join(text.strip().strip('"«»').split())
    return {"query": query or question, **cost}


def rerank(question: str, hits: list[Hit], transport: Transport) -> dict[str, Any]:
    """Оценки 0–10 по номерам кандидатов. Модель ответила криво — scores=None, порядок остаётся векторным."""
    blocks = [f"[{number}] {hit.source} · {hit.section}\n{hit.text[:RERANK_CHARS]}" for number, hit in enumerate(hits, start=1)]
    user = f"Вопрос: {question}\n\nФрагменты:\n\n" + "\n\n---\n\n".join(blocks)
    try:
        text, cost = _call(transport, RERANK_PROMPT, user, max_tokens=60 + 12 * len(hits), json_mode=True)
    except TransportError as error:
        return {"scores": None, "error": str(error), "ms": 0, "tokens": 0}
    scores = parse_scores(text, len(hits))
    return {"scores": scores, "error": None if scores is not None else "реранкер вернул не JSON", **cost}


def parse_scores(text: str, count: int) -> list[int] | None:
    """{"scores": {"1": 7}} или {"scores": [{"n": 1, "score": 7}]} → оценка на каждого кандидата; пропущенный — 0."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    raw = data.get("scores", data) if isinstance(data, dict) else None
    pairs: dict[int, Any] = {}
    if isinstance(raw, dict):
        pairs = {int(key): value for key, value in raw.items() if str(key).strip().isdigit()}
    elif isinstance(raw, list):
        pairs = {int(item["n"]): item.get("score") for item in raw if isinstance(item, dict) and str(item.get("n", "")).isdigit()}
    else:
        return None
    result = []
    for number in range(1, count + 1):
        try:
            result.append(max(0, min(10, round(float(pairs.get(number, 0))))))
        except (TypeError, ValueError):
            result.append(0)
    return result


# ── воронка одного режима ────────────────────────────────────────────────


def funnel(
    question: str,
    mode: str,
    *,
    index: RagIndex,
    embedder: Embedder,
    transport: Transport,
    settings: Settings,
) -> dict[str, Any]:
    """
    Что дошло до модели и почему: у каждого кандидата — похожесть, оценка
    реранкера и стадия, на которой он отсеян (threshold / rerank / top_k)
    или context — ушёл модели. context — Hit'ы с новыми номерами 1…n.
    """
    started = time.perf_counter()
    cost = {"rewrite": None, "rerank": None}

    if mode == "base":
        hits = index.search(question, embedder, STRATEGY, k=settings.top_k)
        rows = [_row(hit, "question", None, "context") for hit in hits]
        return _funnel_result(mode, question, None, rows, hits, cost, started)

    query = None
    queries = [(question, "question")]
    if mode == "rewrite":
        rewritten = rewrite_query(question, transport)
        query = rewritten["query"]
        cost["rewrite"] = {"ms": rewritten["ms"], "tokens": rewritten["tokens"]}
        queries.append((query, "rewrite"))

    merged: dict[str, tuple[Hit, set[str]]] = {}
    for text, via in queries:
        for hit in index.search(text, embedder, STRATEGY, k=settings.candidates):
            known = merged.get(hit.chunk_id)
            if known is None or hit.score > known[0].score:
                merged[hit.chunk_id] = (hit, (known[1] if known else set()) | {via})
            else:
                known[1].add(via)
    candidates = sorted(merged.values(), key=lambda item: -item[0].score)[: settings.candidates]

    passed = [(hit, via) for hit, via in candidates if hit.score >= settings.threshold]
    scores: list[int] | None = None
    if passed:
        ranked = rerank(question, [hit for hit, _ in passed], transport)
        scores = ranked["scores"]
        cost["rerank"] = {"ms": ranked["ms"], "tokens": ranked["tokens"], "error": ranked["error"]}

    order = list(range(len(passed)))
    if scores is not None:
        order.sort(key=lambda i: (-scores[i], -passed[i][0].score))
    stage: dict[str, str] = {}
    context: list[Hit] = []
    for i in order:
        hit = passed[i][0]
        if scores is not None and scores[i] < settings.rerank_min:
            stage[hit.chunk_id] = "rerank"
        elif len(context) >= settings.top_k:
            stage[hit.chunk_id] = "top_k"
        else:
            stage[hit.chunk_id] = "context"
            context.append(hit)

    rerank_of = {passed[i][0].chunk_id: scores[i] for i in range(len(passed))} if scores is not None else {}
    rows = [
        _row(hit, _via(via), rerank_of.get(hit.chunk_id), stage.get(hit.chunk_id, "threshold"))
        for hit, via in candidates
    ]
    return _funnel_result(mode, question, query, rows, context, cost, started)


def _via(via: set[str]) -> str:
    return "both" if len(via) > 1 else next(iter(via))


def _row(hit: Hit, via: str, rerank_score: int | None, stage: str) -> dict[str, Any]:
    return {**hit.as_dict(), "via": via, "rerank": rerank_score, "stage": stage}


def _funnel_result(mode, question, query, rows, context, cost, started) -> dict[str, Any]:
    renumbered = [replace(hit, rank=number) for number, hit in enumerate(context, start=1)]
    return {
        "mode": mode,
        "question": question,
        "query": query,
        "candidates": rows,
        "context": renumbered,
        "cost": cost,
        "ms": round((time.perf_counter() - started) * 1000),
    }


# ── вопрос целиком: три воронки, три ответа ──────────────────────────────

Emit = Callable[[dict[str, Any]], None]


def run_question(
    question: str,
    *,
    index: RagIndex,
    embedder: Embedder,
    transport: Transport,
    settings: Settings,
    emit: Emit,
    probe: Probe | None = None,
    stream: bool = True,
) -> dict[str, Any]:
    """
    События: funnel × 3 (по мере готовности) → delta… → answer × 3.
    Режимы независимы и идут параллельно; emit должен быть потокобезопасным.
    """
    results: dict[str, dict[str, Any]] = {}

    def run(mode: str) -> None:
        try:
            stage = funnel(question, mode, index=index, embedder=embedder, transport=transport, settings=settings)
        except TransportError as error:
            results[mode] = {"mode": mode, "error": str(error)}
            emit({"type": "answer", "mode": mode, "error": str(error)})
            return
        context = stage["context"]
        emit(
            {
                "type": "funnel",
                "mode": mode,
                "query": stage["query"],
                "candidates": stage["candidates"],
                "context": len(context),
                "found_expected": _found(probe, context),
                "cost": stage["cost"],
                "ms": stage["ms"],
            }
        )
        overhead = sum((stage["cost"][key] or {}).get("tokens", 0) for key in ("rewrite", "rerank"))
        if not context:
            result = {"answer": NOTHING_FOUND, "skipped": True, "ms": 0, "prompt_tokens": 0, "completion_tokens": 0}
        else:
            on_delta = (lambda text: emit({"type": "delta", "mode": mode, "text": text})) if stream else None
            try:
                result = generate(transport, messages(question, context), on_delta=on_delta)
            except TransportError as error:
                results[mode] = {"mode": mode, "error": str(error)}
                emit({"type": "answer", "mode": mode, "error": str(error)})
                return
        cited = citations(result["answer"], context)
        result.update(
            mode=mode,
            query=stage["query"],
            cited=cited,
            context=len(context),
            relevant=_relevant(probe, context),
            found_expected=_found(probe, context),
            stage_ms=stage["ms"],
            tokens=(result.get("prompt_tokens") or 0) + (result.get("completion_tokens") or 0) + overhead,
        )
        if probe is not None:
            result["grade"] = grade(probe, result["answer"], cited)
        results[mode] = result
        emit({"type": "answer", **result})

    with ThreadPoolExecutor(max_workers=len(MODES)) as pool:
        list(pool.map(run, MODES))
    return results


def _relevant(probe: Probe | None, context: list[Hit]) -> int | None:
    """Сколько кусков контекста — из нужных файлов: чем меньше лишнего, тем чище контекст."""
    if probe is None or not probe.sources:
        return None
    return sum(hit.source in probe.sources for hit in context)


def _found(probe: Probe | None, context: list[Hit]) -> bool | None:
    if probe is None or not probe.sources:
        return None
    return any(hit.source in probe.sources for hit in context)


# ── контрольные вопросы: три режима, итог ────────────────────────────────


def iter_compare(
    *,
    index: RagIndex,
    embedder: Embedder,
    transport: Transport,
    settings: Settings,
    probes: tuple[Probe, ...] = ALL_PROBES,
) -> Iterator[dict[str, Any]]:
    """compare_start → compare_row × N (в порядке готовности) → compare_summary."""
    yield {"type": "compare_start", "total": len(probes), "settings": settings.as_dict()}

    def one(number: int, probe: Probe) -> tuple[int, Probe, dict[str, Any]]:
        results = run_question(
            probe.question, index=index, embedder=embedder, transport=transport,
            settings=settings, emit=lambda event: None, probe=probe, stream=False,
        )
        return number, probe, results

    rows: list[tuple[Probe, dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = [pool.submit(one, number, probe) for number, probe in enumerate(probes, start=1)]
        for future in as_completed(futures):
            number, probe, results = future.result()
            rows.append((probe, results))
            yield {
                "type": "compare_row",
                "index": number,
                "negative": not probe.sources,
                "modes": {mode: _row_summary(results.get(mode, {})) for mode in MODES},
            }
    yield {"type": "compare_summary", **summarize(rows)}


def _row_summary(result: dict[str, Any]) -> dict[str, Any]:
    if "error" in result:
        return {"error": result["error"]}
    grade_ = result.get("grade") or {}
    return {
        "passed": grade_.get("passed"),
        "found": grade_.get("found"),
        "total": grade_.get("total"),
        "sources_ok": grade_.get("sources_ok"),
        "found_expected": result.get("found_expected"),
        "context": result.get("context"),
        "relevant": result.get("relevant"),
        "skipped": bool(result.get("skipped")),
        "tokens": result.get("tokens"),
        "ms": (result.get("stage_ms") or 0) + (result.get("ms") or 0),
        "query": result.get("query"),
        "answer": result.get("answer"),
    }


def summarize(rows: list[tuple[Probe, dict[str, Any]]]) -> dict[str, Any]:
    """По режимам: ответы по ожиданию на вопросах по базе, честные отказы вне её, нужный файл в контексте, цена."""
    on_topic = [results for probe, results in rows if probe.sources]
    off_topic = [results for probe, results in rows if not probe.sources]
    summary: dict[str, Any] = {}
    for mode in MODES:
        mine = [results.get(mode, {}) for _, results in rows]
        ok = [result for result in mine if "error" not in result]
        context = sum(r.get(mode, {}).get("context") or 0 for r in on_topic)
        relevant = sum(r.get(mode, {}).get("relevant") or 0 for r in on_topic)
        summary[mode] = {
            "passed": sum(bool((r.get(mode, {}).get("grade") or {}).get("passed")) for r in on_topic),
            "refused": sum(bool((r.get(mode, {}).get("grade") or {}).get("passed")) for r in off_topic),
            "found_expected": sum(bool(r.get(mode, {}).get("found_expected")) for r in on_topic),
            "avg_context": round(sum(r.get("context") or 0 for r in ok) / len(ok), 1) if ok else 0.0,
            # Доля кусков из нужных файлов в контексте вопросов по базе — точность второго этапа.
            "precision": round(relevant / context, 2) if context else 0.0,
            "tokens": sum(r.get("tokens") or 0 for r in ok),
            "avg_ms": round(sum((r.get("stage_ms") or 0) + (r.get("ms") or 0) for r in ok) / len(ok)) if ok else 0,
            "errors": len(mine) - len(ok),
        }
    return {"on_topic": len(on_topic), "off_topic": len(off_topic), "summary": summary}

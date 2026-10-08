"""
Первый RAG-запрос — день 22.

Retrieve → augment → generate:

1. retrieve — вопрос превращается в вектор той же моделью, что и база, и
   индекс дня 21 отдаёт top-K кусков. Стратегия — structure: на
   контрольных вопросах дня 21 она ставила нужный файл первым чаще;
2. augment — куски с пометками [1]…[K] (файл, раздел, строки) и вопрос
   склеиваются в одно сообщение пользователя;
3. generate — DeepSeek отвечает по кускам и ставит ссылки [n].

Режим без RAG — тот же системный промпт и тот же вопрос, только без
кусков. Поэтому разница двух ответов — ровно то, что дала база.

Качество сверяется кодом, без второй модели-судьи: у каждого
контрольного вопроса есть факты, которые должны прозвучать в ответе
(advent/rag/probes.py), и файлы, на которые ответ должен сослаться.
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..agent.transport import Transport, TransportError
from .embeddings import Embedder
from .index import Hit, RagIndex
from .probes import PROBES, Probe


MODES = ("plain", "rag")
MODE_TITLES = {"plain": "без RAG", "rag": "с RAG"}

STRATEGY = "structure"
TOP_K = 5
MAX_K = 8

MODEL = "deepseek-v4-flash"
TEMPERATURE = 0.2
MAX_TOKENS = 700

# Вопрос считается отвеченным, если прозвучали хотя бы две трети фактов.
PASS_SHARE = 2 / 3

SYSTEM_PROMPT = (
    "Ты отвечаешь на вопросы о проекте ai-advent-challenge — учебном Flask-приложении, где по дням "
    "строится LLM-агент на DeepSeek: память, задачи, инварианты, MCP-серверы, расписание, RAG. "
    "Отвечай по-русски, коротко — 3–6 предложений — и конкретно: имена функций, файлов, констант, числа."
)

RAG_RULES = (
    "Ниже — фрагменты кода и документации проекта, найденные по вопросу. Отвечай только по ним. "
    "После каждого утверждения ставь ссылку на фрагмент в квадратных скобках: [1], [2]. "
    "Если во фрагментах ответа нет — так и скажи, не додумывай."
)

Emit = Callable[[dict[str, Any]], None]


# ── retrieve → augment ───────────────────────────────────────────────────


def retrieve(index: RagIndex, embedder: Embedder, question: str, *, k: int = TOP_K) -> list[Hit]:
    return index.search(question, embedder, STRATEGY, k=k)


def augment(question: str, hits: list[Hit]) -> str:
    """Куски и вопрос — одним сообщением. Номер куска — его ранг, на него и ссылается ответ."""
    blocks = [
        f"[{hit.rank}] {hit.source} · {hit.section} · строки {hit.start_line}–{hit.end_line}\n{hit.text}"
        for hit in hits
    ]
    return f"{RAG_RULES}\n\nФрагменты:\n\n" + "\n\n---\n\n".join(blocks) + f"\n\nВопрос: {question}"


def messages(question: str, hits: list[Hit] | None) -> list[dict[str, str]]:
    """hits=None — режим без RAG: системный промпт тот же, вопрос голый."""
    content = question if hits is None else augment(question, hits)
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]


# ── generate ─────────────────────────────────────────────────────────────


def generate(transport: Transport, request: list[dict[str, str]], *, on_delta: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Один запрос к модели. С on_delta — потоком, по кусочкам текста."""
    payload = {
        "model": MODEL,
        "messages": request,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "disabled"},
        "stream": on_delta is not None,
    }
    started = time.perf_counter()
    if on_delta is None:
        response = transport.send(payload)
        choices = response.get("choices") or [{}]
        text = ((choices[0].get("message") or {}).get("content")) or ""
        usage = response.get("usage") or {}
        finish = choices[0].get("finish_reason")
    else:
        parts: list[str] = []
        usage, finish = {}, None
        for chunk in transport.stream(payload):
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                continue
            finish = choices[0].get("finish_reason") or finish
            piece = (choices[0].get("delta") or {}).get("content")
            if isinstance(piece, str) and piece:
                parts.append(piece)
                on_delta(piece)
        text = "".join(parts)
    if not text.strip():
        raise TransportError("DeepSeek не вернул текст ответа")
    return {
        "answer": text.strip(),
        "finish_reason": finish or "не указан",
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "ms": round((time.perf_counter() - started) * 1000),
    }


CITATION = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")


def citations(answer: str, hits: list[Hit]) -> list[dict[str, Any]]:
    """На какие куски сослался ответ: номера [n] → файл и раздел. Чужие номера отбрасываются."""
    by_rank = {hit.rank: hit for hit in hits}
    seen: list[int] = []
    for group in CITATION.findall(answer):
        for number in re.split(r"[,;]", group):
            rank = int(number)
            if rank in by_rank and rank not in seen:
                seen.append(rank)
    return [{"n": rank, "source": by_rank[rank].source, "section": by_rank[rank].section} for rank in sorted(seen)]


# ── сверка с ожиданием ───────────────────────────────────────────────────


def grade(probe: Probe, answer: str, cited: list[dict[str, Any]] | None) -> dict[str, Any]:
    """
    Факты — есть ли в ответе то, что обязано прозвучать. Источник — сослался
    ли ответ хоть на один нужный файл; у ответа без RAG ссылок нет — None.
    """
    facts = [{"label": fact.label, "found": fact.found_in(answer)} for fact in probe.facts]
    found = sum(fact["found"] for fact in facts)
    need = max(1, math.ceil(len(facts) * PASS_SHARE)) if facts else 0
    sources = None if cited is None else any(item["source"] in probe.sources for item in cited)
    return {"facts": facts, "found": found, "total": len(facts), "need": need, "passed": found >= need, "sources_ok": sources}


def find_probe(question: str) -> Probe | None:
    """Вопрос из поля ввода совпал с контрольным — тогда ответы можно сверить."""
    normalized = question.strip().casefold()
    return next((probe for probe in PROBES if probe.question.casefold() == normalized), None)


# ── один вопрос: оба режима сразу ────────────────────────────────────────


def ask(
    question: str,
    *,
    index: RagIndex,
    embedder: Embedder,
    transport: Transport,
    emit: Emit,
    k: int = TOP_K,
    stream: bool = True,
    probe: Probe | None = None,
) -> dict[str, Any]:
    """
    События: retrieved → (delta…)×2 вперемешку → answer × 2. Оба режима идут
    параллельно — ждать второй ответ после первого незачем. emit должен
    быть потокобезопасным (очередь подходит).
    """
    hits = retrieve(index, embedder, question, k=k)
    request = {"plain": messages(question, None), "rag": messages(question, hits)}
    emit(
        {
            "type": "retrieved",
            "question": question,
            "strategy": STRATEGY,
            "k": k,
            "hits": [hit.as_dict() for hit in hits],
            "found_expected": None if probe is None else any(hit.source in probe.sources for hit in hits),
            "prompt": {mode: request[mode][1]["content"] for mode in MODES},
            "system": SYSTEM_PROMPT,
        }
    )

    results: dict[str, dict[str, Any]] = {}
    lock = threading.Lock()

    def run(mode: str) -> None:
        on_delta = (lambda text: emit({"type": "delta", "mode": mode, "text": text})) if stream else None
        try:
            result = generate(transport, request[mode], on_delta=on_delta)
        except TransportError as error:
            result = {"error": str(error)}
        if "answer" in result:
            cited = citations(result["answer"], hits) if mode == "rag" else None
            result["cited"] = cited
            if probe is not None:
                result["grade"] = grade(probe, result["answer"], cited)
        with lock:
            results[mode] = result
        emit({"type": "answer", "mode": mode, **result})

    with ThreadPoolExecutor(max_workers=len(MODES)) as pool:
        list(pool.map(run, MODES))
    return {"hits": hits, "results": results}


# ── десять контрольных вопросов ──────────────────────────────────────────


def iter_compare(
    *,
    index: RagIndex,
    embedder: Embedder,
    transport: Transport,
    probes: tuple[Probe, ...] = PROBES,
    k: int = TOP_K,
) -> Iterator[dict[str, Any]]:
    """
    compare_start → (compare_question → retrieved → answer × 2)* → compare_summary.
    Ответы целиком, без потока по словам: в таблице важен итог.
    """
    totals = {mode: {"passed": 0, "found": 0, "total": 0, "sources": 0, "ms": 0, "tokens": 0, "errors": 0} for mode in MODES}
    retrieved_ok = 0
    yield {"type": "compare_start", "total": len(probes), "k": k, "strategy": STRATEGY}
    for number, probe in enumerate(probes, start=1):
        yield {"type": "compare_question", "index": number, **probe.as_dict()}
        events: list[dict[str, Any]] = []
        lock = threading.Lock()

        def collect(event: dict[str, Any]) -> None:
            with lock:
                events.append(event)

        outcome = ask(probe.question, index=index, embedder=embedder, transport=transport, emit=collect, k=k, stream=False, probe=probe)
        for event in events:
            yield {**event, "index": number}

        retrieved_ok += any(hit.source in probe.sources for hit in outcome["hits"])
        for mode, result in outcome["results"].items():
            total = totals[mode]
            if "grade" not in result:
                total["errors"] += 1
                continue
            total["passed"] += result["grade"]["passed"]
            total["found"] += result["grade"]["found"]
            total["total"] += result["grade"]["total"]
            total["sources"] += bool(result["grade"]["sources_ok"])
            total["ms"] += result["ms"]
            total["tokens"] += (result.get("prompt_tokens") or 0) + (result.get("completion_tokens") or 0)

    count = len(probes) or 1
    summary = {
        mode: {
            "passed": values["passed"],
            "facts": round(values["found"] / values["total"], 2) if values["total"] else 0.0,
            "sources": values["sources"] if mode == "rag" else None,
            "avg_ms": round(values["ms"] / max(1, count - values["errors"])),
            "tokens": values["tokens"],
            "errors": values["errors"],
        }
        for mode, values in totals.items()
    }
    yield {"type": "compare_summary", "total": len(probes), "retrieved_ok": retrieved_ok, "summary": summary}

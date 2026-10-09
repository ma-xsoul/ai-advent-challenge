"""
Ответ с доказательствами — день 24.

Ссылка [n] в ответе дня 22 — ещё не доказательство: модель может
сослаться на кусок и написать то, чего в нём нет. Здесь ответ обязан
прийти вместе с цитатами, и каждую проверяют:

1. поиск — воронка дня 23: top-20 → порог похожести → реранкер → top-5;
2. «не знаю» — если после порога и реранкера не осталось ни одного куска,
   релевантность ниже порога: модель не отвечает, а ассистент говорит
   «не знаю» и просит уточнить вопрос, подсказывая близкие темы проекта;
3. ответ — JSON: status, answer, quotes [{n, text}], clarify. Модель
   называет только номер куска; файл, раздел и chunk_id подставляет код,
   поэтому выдумать путь модель не может;
4. цитаты — код ищет каждую в её куске: дословно, с точностью до
   кавычек и пробелов, частично или нигде. Выдуманная цитата видна сразу;
5. смысл — отдельный запрос-судья: подтверждают ли цитаты ответ (да /
   частично / нет). Это смысловая проверка, кодом её честно не сделать.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from typing import Any

from ..agent.transport import Transport, TransportError
from ..navigation import DAYS
from .answer import CITATION, MODEL
from .embeddings import Embedder
from .index import Hit, RagIndex
from .probes import Probe
from .rerank import ALL_PROBES, CONCURRENCY, Settings, funnel


UNKNOWN = "Не знаю: в базе не нашлось фрагментов, которые отвечают на этот вопрос."

# Цитаты в JSON идут раньше ответа: модель пишет по порядку, поэтому сначала
# выписывает доказательства, а потом отвечает по ним. Когда ответ шёл первым,
# судья в 7 случаях из 10 находил утверждения, которых в цитатах нет.
ANSWER_PROMPT = (
    "Ты отвечаешь на вопросы о проекте ai-advent-challenge строго по фрагментам из его кода и документации. "
    "Верни только JSON такого вида:\n"
    '{"status": "answer", "quotes": [{"n": 1, "text": "..."}], "answer": "...", "clarify": ""}\n'
    "Порядок работы:\n"
    "1. quotes — сначала выпиши 3–5 цитат, которые отвечают на вопрос; n — номер фрагмента; text — до двух строк "
    "и не длиннее 250 символов, скопированных из этого фрагмента символ в символ, без пересказа, сокращений и "
    "многоточий;\n"
    "2. answer — потом ответ по-русски, 2–4 предложения, только по выписанным цитатам: каждое утверждение должно "
    "прямо следовать из какой-то цитаты, после него — ссылка на фрагмент [n]. Чего нет в цитатах — не пиши, "
    "даже если это есть во фрагментах;\n"
    '3. если во фрагментах ответа нет — status "unknown", quotes пустой, answer "Не знаю", а в clarify — '
    "просьба уточнить вопрос."
)

JUDGE_PROMPT = (
    "Ты — строгий проверяющий. Тебе дают ответ ассистента и цитаты из документов, на которые он опирается. "
    "Оцени только по цитатам, своих знаний не используй: подтверждают ли цитаты смысл ответа?\n"
    '"yes" — все ключевые утверждения ответа есть в цитатах или прямо из них следуют;\n'
    '"partial" — часть утверждений опирается на цитаты, часть — нет;\n'
    '"no" — цитаты о другом или противоречат ответу.\n'
    'Верни только JSON: {"verdict": "yes", "unsupported": ["утверждение ответа без опоры в цитатах"], '
    '"comment": "одно короткое предложение"}'
)

CLARIFY_PROMPT = (
    "В базе знаний проекта ai-advent-challenge не нашлось фрагментов по вопросу пользователя. На вопрос не "
    "отвечай. Напиши по-русски одну короткую просьбу уточнить вопрос (1–2 предложения) и предложи 2–3 близкие "
    "темы, по которым в базе есть материалы, — только из этого списка:\n{topics}"
)

VERDICTS = ("yes", "partial", "no")
QUOTE_OK = ("exact", "near", "elsewhere")
QUOTE_TITLES = {
    "exact": "дословно",
    "near": "с точностью до кавычек и пробелов",
    "elsewhere": "есть, но в другом куске",
    "partial": "совпала только часть",
    "missing": "в найденных кусках нет",
}

Emit = Callable[[dict[str, Any]], None]


# ── запросы к модели ─────────────────────────────────────────────────────


def _ask_json(transport: Transport, system: str, user: str, *, max_tokens: int, json_mode: bool = True) -> tuple[str, dict[str, int]]:
    payload: dict[str, Any] = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.1,
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
    tokens = (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
    return text, {"ms": round((time.perf_counter() - started) * 1000), "tokens": tokens}


def _json(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def generate(question: str, context: list[Hit], transport: Transport) -> dict[str, Any]:
    """Ответ модели в JSON. Не разобрался — весь текст считается ответом без цитат: проверки это покажут."""
    blocks = [
        f"[{hit.rank}] {hit.source} · {hit.section} · строки {hit.start_line}–{hit.end_line}\n{hit.text}"
        for hit in context
    ]
    user = "Фрагменты:\n\n" + "\n\n---\n\n".join(blocks) + f"\n\nВопрос: {question}"
    text, cost = _ask_json(transport, ANSWER_PROMPT, user, max_tokens=2000)
    data = _json(text)
    if data is None:
        return {"status": "answer", "answer": text.strip(), "quotes": [], "clarify": "", "parsed": False, **cost}
    status = "unknown" if str(data.get("status", "")).strip().lower() == "unknown" else "answer"
    quotes = [
        {"n": int(item["n"]), "text": str(item.get("text") or "").strip()}
        for item in data.get("quotes") or []
        if isinstance(item, dict) and str(item.get("n", "")).strip().isdigit() and str(item.get("text") or "").strip()
    ]
    return {
        "status": status,
        "answer": str(data.get("answer") or "").strip() or ("Не знаю" if status == "unknown" else ""),
        "quotes": quotes,
        "clarify": str(data.get("clarify") or "").strip(),
        "parsed": True,
        **cost,
    }


def judge(answer: str, quotes: list[dict[str, Any]], transport: Transport) -> dict[str, Any]:
    """Подтверждают ли цитаты смысл ответа. Судья видит только цитаты — не куски целиком и не свои знания."""
    listed = "\n".join(f"{number}. «{quote['text']}»" for number, quote in enumerate(quotes, start=1))
    user = f"Ответ ассистента:\n{answer}\n\nЦитаты:\n{listed}"
    try:
        text, cost = _ask_json(transport, JUDGE_PROMPT, user, max_tokens=300)
    except TransportError as error:
        return {"verdict": None, "unsupported": [], "comment": f"судья недоступен: {error}", "ms": 0, "tokens": 0}
    data = _json(text) or {}
    verdict = str(data.get("verdict", "")).strip().lower()
    return {
        "verdict": verdict if verdict in VERDICTS else None,
        "unsupported": [str(item) for item in data.get("unsupported") or [] if str(item).strip()][:3],
        "comment": str(data.get("comment") or "").strip() or ("судья ответил не JSON" if not data else ""),
        **cost,
    }


def clarify(question: str, transport: Transport) -> dict[str, Any]:
    """Просьба уточнить и 2–3 близкие темы проекта из списка дней — без выдумки, только из него."""
    topics = "\n".join(f"- день {day.number}: {day.title} — {day.description}" for day in DAYS)
    try:
        text, cost = _ask_json(transport, CLARIFY_PROMPT.format(topics=topics), question, max_tokens=200, json_mode=False)
    except TransportError:
        text, cost = "", {"ms": 0, "tokens": 0}
    return {"text": text.strip() or "Уточните, пожалуйста, о какой части проекта вопрос.", **cost}


# ── проверка цитат ───────────────────────────────────────────────────────


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _norm(text: str) -> str:
    """Мелочи, которые модель меняет при копировании: регистр, ё, кавычки, тире, обратные кавычки, пробелы."""
    text = text.casefold().replace("ё", "е")
    text = re.sub(r"[`*]", "", text)
    text = re.sub(r"[«»“”„\"']", '"', text)
    text = re.sub(r"[—–−]", "-", text)
    return _squash(text)


def _parts(quote: str) -> list[str]:
    # Многоточие запрещено, но если модель им склеила две строки — ищем обе части.
    return [part for part in re.split(r"\.\.\.|…", quote) if part.strip()]


def _inside(quote: str, text: str) -> bool:
    haystack = _norm(text)
    return all(_norm(part) in haystack for part in _parts(quote))


def verify_quote(quote: dict[str, Any], context: list[Hit]) -> dict[str, Any]:
    """
    exact     — есть в своём куске символ в символ (с точностью до переносов строк);
    near      — есть после нормализации кавычек, тире, регистра, пробелов;
    elsewhere — есть, но в другом куске: номер исправлен;
    partial   — ≥ 80% цитаты совпало подряд: модель подправила текст;
    missing   — в найденных кусках нет — выдумана.
    """
    by_rank = {hit.rank: hit for hit in context}
    own = by_rank.get(quote["n"])
    result = {"n": quote["n"], "text": quote["text"]}
    if own is not None and _squash(quote["text"]) in _squash(own.text):
        return {**result, "status": "exact", "hit": own}
    if own is not None and _inside(quote["text"], own.text):
        return {**result, "status": "near", "hit": own}
    other = next((hit for hit in context if hit is not own and _inside(quote["text"], hit.text)), None)
    if other is not None:
        return {**result, "status": "elsewhere", "n": other.rank, "claimed": quote["n"], "hit": other}
    if own is not None:
        needle, haystack = _norm(quote["text"]), _norm(own.text)
        match = SequenceMatcher(None, haystack, needle, autojunk=False).find_longest_match(0, len(haystack), 0, len(needle))
        share = match.size / len(needle) if needle else 0.0
        if share >= 0.8:
            return {**result, "status": "partial", "share": round(share, 2), "hit": own}
    return {**result, "status": "missing", "hit": own}


def sources_of(answer: str, quotes: list[dict[str, Any]], context: list[Hit]) -> list[dict[str, Any]]:
    """Источники — куски, на которые ссылается ответ [n] или подтверждённая цитата. Пути подставляет код."""
    by_rank = {hit.rank: hit for hit in context}
    quoted = {quote["n"] for quote in quotes if quote["status"] in QUOTE_OK}
    cited = {int(number) for group in CITATION.findall(answer) for number in re.split(r"[,;]", group) if int(number) in by_rank}
    return [
        {
            "n": rank,
            "source": by_rank[rank].source,
            "section": by_rank[rank].section,
            "chunk_id": by_rank[rank].chunk_id,
            "lines": [by_rank[rank].start_line, by_rank[rank].end_line],
            "quoted": rank in quoted,
            "cited": rank in cited,
        }
        for rank in sorted(quoted | cited)
    ]


# ── вопрос целиком ───────────────────────────────────────────────────────


def ask(
    question: str,
    *,
    index: RagIndex,
    embedder: Embedder,
    transport: Transport,
    settings: Settings,
    emit: Emit,
    probe: Probe | None = None,
) -> dict[str, Any]:
    """События: retrieved → answer → judge? → checks. Возвращает итог для таблицы."""
    started = time.perf_counter()
    stage = funnel(question, "filter", index=index, embedder=embedder, transport=transport, settings=settings)
    context: list[Hit] = stage["context"]
    scores = [row["rerank"] for row in stage["candidates"] if row["rerank"] is not None]
    relevance = max(scores) if scores else None
    tokens = (stage["cost"]["rerank"] or {}).get("tokens", 0)
    emit(
        {
            "type": "retrieved",
            "candidates": len(stage["candidates"]),
            "passed": sum(row["stage"] != "threshold" for row in stage["candidates"]),
            "relevance": relevance,
            "settings": settings.as_dict(),
            "context": [{**hit.as_dict(), "rerank": _rerank_of(stage, hit)} for hit in context],
        }
    )

    if not context:
        asked = clarify(question, transport)
        tokens += asked["tokens"]
        reply = {"status": "unknown", "reason": "relevance", "answer": UNKNOWN, "clarify": asked["text"], "quotes": [], "sources": [], "parsed": True}
    else:
        generated = generate(question, context, transport)
        tokens += generated["tokens"]
        quotes = [verify_quote(quote, context) for quote in generated["quotes"]]
        reply = {
            "status": generated["status"],
            "reason": "model" if generated["status"] == "unknown" else None,
            "answer": generated["answer"],
            "clarify": generated["clarify"],
            "quotes": quotes,
            "sources": sources_of(generated["answer"], quotes, context),
            "parsed": generated["parsed"],
        }
    emit({"type": "answer", **_public(reply)})

    verdict = None
    if reply["status"] == "answer" and reply["quotes"]:
        verdict = judge(reply["answer"], reply["quotes"], transport)
        tokens += verdict["tokens"]
        emit({"type": "judge", **verdict})

    checks = check(reply, verdict, probe)
    result = {
        **_public(reply),
        "relevance": relevance,
        "judge": verdict,
        "checks": checks,
        "tokens": tokens,
        "ms": round((time.perf_counter() - started) * 1000),
    }
    emit({"type": "checks", **checks, "tokens": tokens, "ms": result["ms"]})
    return result


def _rerank_of(stage: dict[str, Any], hit: Hit) -> int | None:
    return next((row["rerank"] for row in stage["candidates"] if row["chunk_id"] == hit.chunk_id), None)


def _public(reply: dict[str, Any]) -> dict[str, Any]:
    """Без объектов Hit — для JSON; у цитаты остаются файл и раздел её куска и сам кусок для подсветки."""
    quotes = []
    for quote in reply["quotes"]:
        hit = quote.get("hit")
        quotes.append(
            {
                **{key: value for key, value in quote.items() if key != "hit"},
                "source": hit.source if hit else None,
                "section": hit.section if hit else None,
                "chunk_id": hit.chunk_id if hit else None,
                "chunk": hit.text if hit else None,
            }
        )
    return {**reply, "quotes": quotes}


def check(reply: dict[str, Any], verdict: dict[str, Any] | None, probe: Probe | None) -> dict[str, Any]:
    """
    Три проверки задания и «не знаю». На вопросе по базе засчитано, если есть
    источники и цитаты, все цитаты подтвердились и судья сказал «да». На
    вопросе вне базы — если ассистент сказал «не знаю».
    """
    quotes = reply["quotes"]
    verified = sum(quote["status"] in QUOTE_OK for quote in quotes)
    unknown = reply["status"] == "unknown"
    result = {
        "unknown": unknown,
        "has_sources": bool(reply["sources"]),
        "has_quotes": bool(quotes),
        "quotes_verified": verified,
        "quotes_total": len(quotes),
        "meaning": verdict["verdict"] if verdict else None,
        "negative": probe is not None and not probe.sources,
    }
    if result["negative"]:
        passed = unknown
    else:
        passed = not unknown and result["has_sources"] and result["has_quotes"] and verified == len(quotes) and result["meaning"] == "yes"
    result["passed"] = passed if probe is not None else None
    return result


# ── контрольные вопросы ──────────────────────────────────────────────────


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
        try:
            result = ask(probe.question, index=index, embedder=embedder, transport=transport, settings=settings, emit=lambda event: None, probe=probe)
        except TransportError as error:
            result = {"error": str(error)}
        return number, probe, result

    rows: list[tuple[Probe, dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = [pool.submit(one, number, probe) for number, probe in enumerate(probes, start=1)]
        for future in as_completed(futures):
            number, probe, result = future.result()
            rows.append((probe, result))
            yield {"type": "compare_row", "index": number, "negative": not probe.sources, **_row(result)}
    yield {"type": "compare_summary", **summarize(rows)}


def _row(result: dict[str, Any]) -> dict[str, Any]:
    if "error" in result:
        return {"error": result["error"]}
    return {
        "status": result["status"],
        "parsed": result["parsed"],
        "answer": result["answer"],
        "clarify": result["clarify"],
        "relevance": result["relevance"],
        "sources": [{key: source[key] for key in ("n", "source", "section", "chunk_id")} for source in result["sources"]],
        "quotes": [{key: quote[key] for key in ("n", "text", "status", "source")} for quote in result["quotes"]],
        "judge": result["judge"],
        "checks": result["checks"],
        "tokens": result["tokens"],
        "ms": result["ms"],
    }


def summarize(rows: list[tuple[Probe, dict[str, Any]]]) -> dict[str, Any]:
    on_topic = [result for probe, result in rows if probe.sources and "error" not in result]
    off_topic = [result for probe, result in rows if not probe.sources and "error" not in result]
    checks = [result["checks"] for result in on_topic]
    answered = [item for item in checks if not item["unknown"]]
    verdicts = {verdict: sum(item["meaning"] == verdict for item in answered) for verdict in VERDICTS}
    total_quotes = sum(item["quotes_total"] for item in answered)
    return {
        "on_topic": sum(1 for probe, _ in rows if probe.sources),
        "off_topic": sum(1 for probe, _ in rows if not probe.sources),
        "answered": len(answered),
        "with_sources": sum(item["has_sources"] for item in answered),
        "with_quotes": sum(item["has_quotes"] for item in answered),
        "quotes_verified": sum(item["quotes_verified"] for item in answered),
        "quotes_total": total_quotes,
        "meaning": verdicts,
        "passed": sum(bool(item["passed"]) for item in checks),
        "false_unknown": sum(item["unknown"] for item in checks),
        "unknown_off_topic": sum(result["checks"]["unknown"] for result in off_topic),
        "tokens": sum(result["tokens"] for _, result in rows if "error" not in result),
        "errors": sum("error" in result for _, result in rows),
    }

"""
Проверка флоу дня 20: правильно ли агент выбирал серверы и в каком порядке.

Маршрут модели не виден в её ответе — он виден в журнале вызовов. Здесь
код сверяет журнал одного хода:

1. каждый вызов нашёл свой сервер — модель не выдумала инструмент и не
   перепутала префикс;
2. выбраны нужные серверы — не меньше и не больше, чем требует просьба.
   Что нужно, видно по словам просьбы: у каждого сервера своя тема —
   набор основ слов. Проверка по словам, а не по смыслу: грубая, зато
   прозрачная — видно, почему сервер сочтён нужным или лишним;
3. если в ходе был GitMCP — его спросили про репозиторий, который до
   этого вернул GitHub: данные перешли с сервера на сервер, а не взялись
   из памяти модели;
4. если отчёт сохраняли после GitMCP — сохранили после ответа, то есть
   в отчёт было что класть.

Проверки 3 и 4 не про любой ход, а про длинный флоу GitHub → GitMCP →
отчёт; где их не к чему применить, их нет в списке.
"""

from __future__ import annotations

import json
import re
from typing import Any


GITHUB, DOCS = "github", "gitmcp"
SAVE = "save_report"

# По каким словам в просьбе видно, что сервер нужен. Основы слов — чтобы
# «курс», «курса» и «курсом» считались одним.
TOPICS: dict[str, tuple[str, ...]] = {
    GITHUB: ("github", "гитхаб", "репозитор", "звёзд", "звезд", "отчёт", "отчет", "сохрани", "сравни"),
    DOCS: ("gitmcp", "архитектур", "устроен", "документац", "readme"),
    "rates": ("курс", "доллар", "рубл", "валют", "usd"),
}


def expected_servers(question: str, available: list[str]) -> list[str]:
    """Какие из подключённых серверов нужны просьбе — по словам темы."""
    text = question.lower()
    return [key for key in available if any(word in text for word in TOPICS.get(key, ()))]


def check_flow(calls: list[dict[str, Any]], servers: list[dict[str, Any]], question: str = "") -> dict[str, Any]:
    """calls — вызовы одного хода по порядку; servers — карточки подключённых серверов; question — просьба пользователя."""
    titles = {card["key"]: card["title"] for card in servers}
    available = [card["key"] for card in servers if card.get("ok")]
    used = [call["server"] for call in calls if call.get("server")]
    expected = expected_servers(question, available)

    checks = [_check_routes(calls), _check_choice(used, expected, titles)]
    if any(key == DOCS for key in used):
        checks.append(_check_handoff(calls))
        if any(call.get("tool") == SAVE for call in calls):
            checks.append(_check_report(calls))

    return {
        "steps": [_step(call) for call in calls],
        "checks": checks,
        "ok": all(check["ok"] for check in checks),
        "path": [titles.get(key, key) for key in _collapse(used)],
        "expected": [titles.get(key, key) for key in expected],
        "idle": [titles[key] for key in available if key not in used],
        "failed": [{"title": card["title"], "error": card.get("error")} for card in servers if not card.get("ok")],
    }


def _step(call: dict[str, Any]) -> dict[str, Any]:
    return {
        "server": call.get("server"),
        "tool": call.get("tool") or call.get("name"),
        "name": call.get("name"),
        "ok": bool(call.get("ok")),
        "round": call.get("round"),
        "arguments": call.get("arguments"),
        "chars": call.get("chars"),
        "ms": call.get("ms"),
    }


def _collapse(keys: list[str]) -> list[str]:
    """github, github, gitmcp, github → github, gitmcp, github: путь без повторов подряд."""
    return [key for index, key in enumerate(keys) if index == 0 or keys[index - 1] != key]


def _check_routes(calls: list[dict[str, Any]]) -> dict[str, Any]:
    title = "Каждый вызов нашёл свой сервер"
    if not calls:
        return {"title": title, "ok": True, "detail": "вызовов не было — модель ответила сама"}
    lost = [call["name"] for call in calls if not call.get("server")]
    if lost:
        return {"title": title, "ok": False, "detail": f"нет маршрута: {', '.join(lost)}"}
    counts: dict[str, int] = {}
    for call in calls:
        counts[call["server"]] = counts.get(call["server"], 0) + 1
    return {"title": title, "ok": True, "detail": ", ".join(f"{key} ×{count}" for key, count in counts.items())}


def _check_choice(used: list[str], expected: list[str], titles: dict[str, str]) -> dict[str, Any]:
    title = "Выбраны нужные серверы"
    touched = list(dict.fromkeys(used))
    if not expected:
        detail = "по словам просьбы тема не определилась — судить не по чему"
        return {"title": title, "ok": True, "detail": detail + (f"; вызовы: {', '.join(titles.get(k, k) for k in touched)}" if touched else "")}

    extra = [titles.get(key, key) for key in touched if key not in expected]
    missing = [titles.get(key, key) for key in expected if key not in touched]
    problems = []
    if extra:
        problems.append(f"лишние: {', '.join(extra)}")
    if missing:
        problems.append(f"не спросили: {', '.join(missing)}")
    if problems:
        return {"title": title, "ok": False, "detail": "; ".join(problems)}
    return {"title": title, "ok": True, "detail": f"по просьбе нужны {', '.join(titles.get(k, k) for k in expected)} — они и выбраны"}


def _check_handoff(calls: list[dict[str, Any]]) -> dict[str, Any]:
    title = "GitMCP спросили про репозиторий из GitHub"
    asks = [call for call in calls if call.get("server") == DOCS and call.get("ok")]
    if not asks:
        return {"title": title, "ok": False, "detail": "удачного вызова GitMCP не было"}

    problems, sources = [], []
    for ask in asks:
        known = _github_names(calls[: calls.index(ask)])
        for name in _repo_names(ask.get("arguments")):
            origin = known.get(name.lower())
            if origin:
                sources.append(f"{name} — из {origin}")
            else:
                problems.append(name)
    if problems:
        return {"title": title, "ok": False, "detail": f"не из результатов GitHub: {', '.join(problems)}"}
    if not sources:
        return {"title": title, "ok": False, "detail": "в вызовах GitMCP нет имени репозитория"}
    return {"title": title, "ok": True, "detail": "; ".join(dict.fromkeys(sources))}


def _check_report(calls: list[dict[str, Any]]) -> dict[str, Any]:
    title = "Отчёт сохранён после ответа GitMCP"
    saves = [call for call in calls if call.get("tool") == SAVE and call.get("ok")]
    asks = [call for call in calls if call.get("server") == DOCS and call.get("ok")]
    if not saves:
        return {"title": title, "ok": False, "detail": "отчёт не сохранился"}
    if not asks:
        return {"title": title, "ok": False, "detail": "удачного ответа GitMCP не было — класть в отчёт нечего"}
    save, last_ask = saves[-1], asks[-1]
    if not save["round"] > last_ask["round"]:
        return {"title": title, "ok": False, "detail": "отчёт сохранён раньше, чем пришёл ответ GitMCP"}
    file = (_json(save) or {}).get("file", "?")
    return {"title": title, "ok": True, "detail": f"круг {last_ask['round']} → круг {save['round']}, файл {file}"}


def _github_names(calls: list[dict[str, Any]]) -> dict[str, str]:
    """Все репозитории, которые GitHub уже вернул в этом ходу: имя → каким инструментом и в каком круге."""
    names: dict[str, str] = {}
    for call in calls:
        if call.get("server") != GITHUB or not call.get("ok"):
            continue
        data = _json(call)
        if not isinstance(data, dict):
            continue
        rows = [data] + list(data.get("items") or []) + list(data.get("repositories") or [])
        for row in rows:
            if isinstance(row, dict) and row.get("full_name"):
                names.setdefault(str(row["full_name"]).lower(), f"{call['name']} (круг {call.get('round')})")
    return names


def _repo_names(arguments: Any) -> list[str]:
    """Какой репозиторий спросили: owner + repo, repoName или ссылка на github.com в url."""
    if not isinstance(arguments, dict):
        return []
    if arguments.get("owner") and arguments.get("repo"):
        return [f"{arguments['owner']}/{arguments['repo']}"]
    value = arguments.get("repoName")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    match = _GITHUB_URL.search(str(arguments.get("url") or ""))
    return [f"{match.group(1)}/{match.group(2)}"] if match else []


_GITHUB_URL = re.compile(r"(?:github\.com|githubusercontent\.com)/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")


def _json(call: dict[str, Any]) -> Any:
    try:
        return json.loads(call.get("for_model") or call.get("preview") or "")
    except ValueError:
        return None

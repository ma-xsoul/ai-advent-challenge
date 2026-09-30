"""
Проверка цепочки дня 19: дошли ли данные от инструмента к инструменту.

Цепочку строит модель — значит, она же может где-то ошибиться: сравнить
не те репозитории, пересказать таблицу своими словами, сохранить не то.
Здесь код сверяет журнал вызовов одного хода:

1. цепочка выполнена сама: поиск → сравнение → сохранение, в этом порядке;
2. в сравнение ушли ровно те имена, что вернул поиск, — ни одного лишнего;
3. в отчёт попала таблица из сравнения — дословно, а не пересказ;
4. файл на диске совпадает с тем, что модель передала в save_report.

Сверка идёт по тому, что реально было в вызовах и результатах, а не по
словам модели в ответе.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SEARCH, COMPARE, SAVE = "search_repositories", "compare_repositories", "save_report"


def check_chain(calls: list[dict[str, Any]], reports_dir: str | Path) -> dict[str, Any]:
    """calls — вызовы одного хода в порядке выполнения (записи ToolDialog.calls)."""
    steps = [call for call in calls if call.get("name") in (SEARCH, COMPARE, SAVE)]
    compare = _first(steps, COMPARE)
    # Модель может уточнить поиск и искать дважды — цепочка продолжается
    # от последнего удачного поиска перед сравнением, с ним и сверяем.
    search = _last(steps, SEARCH, before=compare)
    save = _first(steps, SAVE, after=compare)

    checks = [
        _check_order(search, compare, save),
        _check_names(search, compare),
        _check_table(compare, save),
        _check_file(save, Path(reports_dir)),
    ]
    return {
        "steps": [_step(call) for call in steps],
        "checks": checks,
        "ok": all(check["ok"] for check in checks),
        "complete": bool(search and compare and save),
    }


def _first(calls: list[dict[str, Any]], name: str, after: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Первый удачный вызов name — после вызова after, если он задан."""
    start = calls.index(after) + 1 if after in calls else 0
    return next((call for call in calls[start:] if call.get("name") == name and call.get("ok")), None)


def _last(calls: list[dict[str, Any]], name: str, before: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Последний удачный вызов name — до вызова before, если он задан."""
    stop = calls.index(before) if before in calls else len(calls)
    return next((call for call in reversed(calls[:stop]) if call.get("name") == name and call.get("ok")), None)


def _result(call: dict[str, Any] | None) -> dict[str, Any] | None:
    if not call:
        return None
    try:
        data = json.loads(call.get("for_model") or call.get("preview") or "")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _step(call: dict[str, Any]) -> dict[str, Any]:
    data = _result(call) or {}
    if not call.get("ok"):
        note = "ошибка"
    elif call["name"] == SEARCH:
        note = f"найдено {len(data.get('items') or [])} из {data.get('total', '?')}"
    elif call["name"] == COMPARE:
        note = f"сравнено {data.get('compared', '?')}, лидер по звёздам — {(data.get('leaders') or {}).get('stars') or '?'}"
    else:
        note = f"файл {data.get('file', '?')}, {data.get('bytes', '?')} байт"
    return {"name": call["name"], "ok": bool(call.get("ok")), "round": call.get("round"), "arguments": call.get("arguments"), "note": note}


def _check_order(search: Any, compare: Any, save: Any) -> dict[str, Any]:
    missing = [name for name, call in ((SEARCH, search), (COMPARE, compare), (SAVE, save)) if call is None]
    if missing:
        return {"title": "Цепочка выполнена сама", "ok": False, "detail": f"не было удачного вызова: {', '.join(missing)}"}
    if not search["round"] < compare["round"] < save["round"]:
        return {"title": "Цепочка выполнена сама", "ok": False, "detail": "шаги шли не по очереди: результат одного не мог стать входом другого"}
    rounds = [search["round"], compare["round"], save["round"]]
    return {
        "title": "Цепочка выполнена сама",
        "ok": True,
        "detail": f"search → compare → save за один ход, круги модели {rounds[0]} → {rounds[1]} → {rounds[2]}",
    }


def _check_names(search: Any, compare: Any) -> dict[str, Any]:
    title = "В сравнение ушли имена из поиска"
    found = _result(search)
    if found is None or compare is None:
        return {"title": title, "ok": False, "detail": "нечего сверять: нет поиска или сравнения"}
    from_search = [item.get("full_name") for item in found.get("items") or []]
    arguments = compare.get("arguments") if isinstance(compare.get("arguments"), dict) else {}
    passed = list(arguments.get("full_names") or [])
    extra = [name for name in passed if name not in from_search]
    if extra:
        return {"title": title, "ok": False, "detail": f"не из поиска: {', '.join(extra)}"}
    return {"title": title, "ok": True, "detail": f"{len(passed)} из {len(from_search)} найденных, лишних нет"}


def _check_table(compare: Any, save: Any) -> dict[str, Any]:
    title = "В отчёт попала таблица из сравнения"
    compared = _result(compare)
    if compared is None or save is None:
        return {"title": title, "ok": False, "detail": "нечего сверять: нет сравнения или сохранения"}
    table = _normalize(compared.get("table") or "")
    arguments = save.get("arguments") if isinstance(save.get("arguments"), dict) else {}
    report = _normalize(arguments.get("markdown") or "")
    if table and table in report:
        return {"title": title, "ok": True, "detail": f"все {table.count(chr(10)) + 1} строк таблицы — дословно"}
    lost = [line for line in table.splitlines() if line not in report]
    return {"title": title, "ok": False, "detail": f"не совпало строк: {len(lost)} — модель пересказала или обрезала таблицу"}


def _check_file(save: Any, reports_dir: Path) -> dict[str, Any]:
    title = "Файл на диске совпадает с отчётом"
    saved = _result(save)
    if saved is None:
        return {"title": title, "ok": False, "detail": "отчёт не сохранялся"}
    path = reports_dir / str(saved.get("file") or "")
    if not saved.get("file") or path.parent != reports_dir or not path.is_file():
        return {"title": title, "ok": False, "detail": f"файла нет: {saved.get('file')}"}
    arguments = save.get("arguments") if isinstance(save.get("arguments"), dict) else {}
    if path.read_text(encoding="utf-8") != arguments.get("markdown"):
        return {"title": title, "ok": False, "detail": f"{path.name} отличается от переданного текста"}
    return {"title": title, "ok": True, "detail": f"{path.name} · {path.stat().st_size} байт — байт в байт"}


def _normalize(text: str) -> str:
    return "\n".join(line.strip() for line in text.strip().splitlines() if line.strip())

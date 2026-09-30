"""
MCP-сервер вокруг публичного GitHub API — дни 17 и 19.

День 17 — три инструмента на чтение: поиск репозиториев, карточка
репозитория и репозитории пользователя. Сервер отдаёт не сырой ответ
GitHub — там десятки полей и ссылок, — а короткую выжимку: её потом
читает модель, и каждый лишний ключ стоит токенов.

День 19 — ещё два, чтобы из инструментов сервера складывалась цепочка
«получить → обработать → сохранить»: compare_repositories берёт имена
(обычно — из результата поиска), догружает карточки и считает сравнение
с готовой таблицей; save_report кладёт отчёт в файл. Порядок вызовов
сервер не навязывает — его выбирает модель.

Регистрация — декоратор @server.tool(). Имя инструмента берётся из имени
функции, описание — из docstring, схема параметров — из аннотаций:
Annotated[..., Field(description=...)] превращается в описание параметра
в inputSchema, ge/le/pattern — в ограничения, которые SDK проверит сам
ещё до вызова функции.

Запуск: python -m advent.mcp.servers.github [--reports-dir ПАПКА] — сервер
говорит по stdio, агент поднимает его процессом. Ключ не нужен: без GITHUB_TOKEN
GitHub даёт 60 запросов в час, с ним — 5000.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

import requests
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field


API_URL = "https://api.github.com"
TIMEOUT_SECONDS = 15
MAX_TOPICS = 6

# Имя владельца и репозитория приходит от модели — в путь URL пускаем
# только то, что GitHub сам разрешает в именах. «../» сюда не пролезет.
NAME_PATTERN = r"^[A-Za-z0-9_.-]{1,100}$"

Owner = Annotated[str, Field(pattern=NAME_PATTERN, description="Владелец: логин пользователя или организации, например «modelcontextprotocol»")]
Repo = Annotated[str, Field(pattern=NAME_PATTERN, description="Имя репозитория без владельца, например «python-sdk»")]
Limit = Annotated[int, Field(ge=1, le=10, description="Сколько репозиториев вернуть, от 1 до 10")]

FULL_NAME_PATTERN = r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$"
FullNames = Annotated[
    list[Annotated[str, Field(pattern=FULL_NAME_PATTERN)]],
    Field(
        min_length=2,
        max_length=8,
        description="Полные имена репозиториев «владелец/имя», от 2 до 8 — например, full_name из результата search_repositories",
    ),
]

DEFAULT_REPORTS = Path(__file__).resolve().parents[3] / "data" / "day19" / "reports"
MAX_REPORT_CHARS = 20_000


class GitHubError(ToolError):
    """
    GitHub не ответил или ответил отказом. Наследник ToolError — так SDK
    понимает, что ошибка ожидаемая, и отдаёт её текст модели (isError),
    а не прячет за «Error executing tool» как падение сервера.
    """


class GitHubApi:
    """Тонкий HTTP-слой к api.github.com. В тестах подменяется заглушкой."""

    def __init__(self, token: str | None = None, *, timeout: int = TIMEOUT_SECONDS) -> None:
        self._token = token
        self._timeout = timeout

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ai-advent-mcp",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        try:
            response = requests.get(f"{API_URL}{path}", params=params, headers=headers, timeout=self._timeout)
        except requests.RequestException as error:
            raise GitHubError(f"Нет связи с GitHub: {error}") from error

        if response.ok:
            return response.json()
        raise GitHubError(_refusal(response, path))


def _refusal(response: requests.Response, path: str) -> str:
    try:
        message = response.json().get("message", "")
    except ValueError:
        message = ""

    if response.status_code == 404:
        return f"На GitHub нет такого: {path}"
    if response.status_code in (403, 429) and response.headers.get("X-RateLimit-Remaining") == "0":
        reset = response.headers.get("X-RateLimit-Reset")
        when = datetime.fromtimestamp(int(reset)).strftime("%H:%M") if reset and reset.isdigit() else "позже"
        return f"Лимит GitHub API исчерпан, сброс в {when}. С GITHUB_TOKEN лимит 5000 запросов в час."
    return f"GitHub ответил {response.status_code}: {message or 'без пояснения'}"


def repo_card(raw: dict[str, Any]) -> dict[str, Any]:
    """Выжимка репозитория: то, о чём спрашивают, без служебных ссылок."""
    license_info = raw.get("license") or {}
    pushed = raw.get("pushed_at") or ""
    created = raw.get("created_at") or ""
    return {
        "full_name": raw.get("full_name"),
        "description": raw.get("description"),
        "url": raw.get("html_url"),
        "stars": raw.get("stargazers_count", 0),
        "forks": raw.get("forks_count", 0),
        "open_issues": raw.get("open_issues_count", 0),
        "language": raw.get("language"),
        "topics": (raw.get("topics") or [])[:MAX_TOPICS],
        "license": license_info.get("spdx_id"),
        "archived": bool(raw.get("archived")),
        "pushed": pushed[:10] or None,
        "created": created[:10] or None,
    }


def compare(cards: list[dict[str, Any]], today: datetime) -> dict[str, Any]:
    """Сравнение карточек: место по звёздам, свежесть пуша, лидеры и таблица. Чистая функция."""
    ranked = sorted(cards, key=lambda card: card["stars"], reverse=True)
    rows = []
    for rank, card in enumerate(ranked, start=1):
        pushed = card.get("pushed")
        days = (today.date() - datetime.fromisoformat(pushed).date()).days if pushed else None
        rows.append({"rank": rank, **card, "days_since_push": days})

    fresh = min((row for row in rows if row["days_since_push"] is not None), key=lambda row: row["days_since_push"], default=None)
    header = "| # | Репозиторий | ★ | Форки | Issues | Язык | Лицензия | Последний пуш |"
    divider = "|---|---|---|---|---|---|---|---|"
    lines = [
        f"| {row['rank']} | [{row['full_name']}]({row['url']}) | {row['stars']} | {row['forks']} | {row['open_issues']} "
        f"| {row['language'] or '—'} | {row['license'] or '—'} | {row['pushed'] or '—'} |"
        for row in rows
    ]
    return {
        "compared": len(rows),
        "repositories": [
            {key: row[key] for key in ("rank", "full_name", "stars", "forks", "open_issues", "language", "license", "pushed", "days_since_push", "archived")}
            for row in rows
        ],
        "leaders": {
            "stars": rows[0]["full_name"] if rows else None,
            "freshest_push": fresh["full_name"] if fresh else None,
            "most_open_issues": max(rows, key=lambda row: row["open_issues"])["full_name"] if rows else None,
        },
        "table": "\n".join([header, divider, *lines]),
    }


def report_filename(title: str, now: datetime) -> str:
    """Имя файла отчёта из заголовка: дата и слаг. Никаких «/» и «..» — только буквы, цифры и дефисы."""
    slug = re.sub(r"[^0-9a-zA-Zа-яА-ЯёЁ]+", "-", title.lower()).strip("-")[:60].strip("-") or "report"
    return f"{now.strftime('%Y-%m-%d-%H%M%S')}-{slug}.md"


def build_server(
    api: GitHubApi,
    *,
    reports_dir: str | Path | None = None,
    now: Any = None,
) -> MCPServer:
    reports = Path(reports_dir or os.getenv("ADVENT_REPORTS_DIR") or DEFAULT_REPORTS)
    clock = now or (lambda: datetime.now(timezone.utc).astimezone())
    server = MCPServer(
        "advent-github",
        version="19",
        instructions=(
            "Инструменты читают публичный GitHub. Цифры о репозиториях бери только из результата "
            "инструмента, не по памяти. Нужен один репозиторий — get_repository, несколько по теме — "
            "search_repositories, всё у одного автора — list_user_repositories. Сравнить несколько — "
            "compare_repositories (он сам догружает карточки — get_repository перед ним не нужен), "
            "сохранить итог в файл — save_report. Данные между инструментами передавай дословно: "
            "имена — как в full_name, таблицу — как вернул compare_repositories."
        ),
    )

    @server.tool(title="Поиск репозиториев")
    def search_repositories(
        query: Annotated[
            str,
            Field(
                min_length=1,
                max_length=256,
                description="Поисковый запрос GitHub: слова и квалификаторы, например «mcp language:python» или «topic:llm stars:>1000»",
            ),
        ],
        limit: Limit = 5,
        sort: Annotated[
            Literal["stars", "updated", "best"],
            Field(description="Порядок: stars — по звёздам, updated — по свежести, best — по релевантности"),
        ] = "stars",
    ) -> dict[str, Any]:
        """Найти публичные репозитории GitHub по запросу. Возвращает общее число совпадений и карточки первых найденных."""
        params: dict[str, Any] = {"q": query, "per_page": limit}
        if sort != "best":
            params["sort"] = sort
        data = api.get("/search/repositories", params)
        items = data.get("items") or []
        return {"query": query, "total": data.get("total_count", 0), "items": [repo_card(item) for item in items[:limit]]}

    @server.tool(title="Карточка репозитория")
    def get_repository(owner: Owner, repo: Repo) -> dict[str, Any]:
        """Подробно об одном репозитории: описание, звёзды, форки, открытые issues, язык, темы, лицензия, дата последнего пуша."""
        return repo_card(api.get(f"/repos/{owner}/{repo}"))

    @server.tool(title="Репозитории пользователя")
    def list_user_repositories(
        user: Owner,
        limit: Limit = 5,
        sort: Annotated[
            Literal["pushed", "updated", "created", "full_name"],
            Field(description="Порядок: pushed — по последнему пушу, created — по дате создания, full_name — по имени"),
        ] = "pushed",
    ) -> dict[str, Any]:
        """Публичные репозитории пользователя или организации GitHub — карточки в выбранном порядке."""
        data = api.get(f"/users/{user}/repos", {"per_page": limit, "sort": sort, "type": "owner"})
        return {"user": user, "items": [repo_card(item) for item in (data or [])[:limit]]}

    @server.tool(title="Сравнение репозиториев")
    def compare_repositories(full_names: FullNames) -> dict[str, Any]:
        """Сравнить несколько репозиториев: догрузить их карточки, расставить по звёздам, найти лидеров (звёзды, свежесть пуша, открытые issues) и собрать markdown-таблицу для отчёта."""
        unique = list(dict.fromkeys(full_names))
        cards = [repo_card(api.get(f"/repos/{name}")) for name in unique]
        return compare(cards, clock())

    @server.tool(title="Сохранить отчёт")
    def save_report(
        title: Annotated[str, Field(min_length=1, max_length=120, description="Заголовок отчёта — из него же имя файла")],
        markdown: Annotated[
            str,
            Field(min_length=1, max_length=MAX_REPORT_CHARS, description="Текст отчёта в markdown — сохраняется в файл как есть"),
        ],
    ) -> dict[str, Any]:
        """Сохранить готовый отчёт в markdown-файл на диске. Возвращает имя файла и размер — по ним отчёт можно найти и проверить."""
        moment = clock()
        reports.mkdir(parents=True, exist_ok=True)
        path = reports / report_filename(title, moment)
        path.write_text(markdown, encoding="utf-8")
        return {
            "file": path.name,
            "folder": str(reports),
            "bytes": path.stat().st_size,
            "lines": markdown.count("\n") + 1,
            "saved_at": moment.strftime("%H:%M:%S"),
        }

    return server


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m advent.mcp.servers.github")
    # День 20 держит отчёты отдельно от дня 19 — папку передаёт флагом.
    parser.add_argument("--reports-dir", default=None)
    args = parser.parse_args(argv)
    build_server(GitHubApi(os.getenv("GITHUB_TOKEN") or None), reports_dir=args.reports_dir).run()


if __name__ == "__main__":
    main()

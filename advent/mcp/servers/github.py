"""
MCP-сервер вокруг публичного GitHub API — день 17.

Три инструмента, все на чтение: поиск репозиториев, карточка репозитория
и репозитории пользователя. Сервер отдаёт не сырой ответ GitHub — там
десятки полей и ссылок, — а короткую выжимку: её потом читает модель,
и каждый лишний ключ стоит токенов.

Регистрация — декоратор @server.tool(). Имя инструмента берётся из имени
функции, описание — из docstring, схема параметров — из аннотаций:
Annotated[..., Field(description=...)] превращается в описание параметра
в inputSchema, ge/le/pattern — в ограничения, которые SDK проверит сам
ещё до вызова функции.

Запуск: python -m advent.mcp.servers.github — сервер говорит по stdio,
агент дня 17 поднимает его процессом. Ключ не нужен: без GITHUB_TOKEN
GitHub даёт 60 запросов в час, с ним — 5000.
"""

from __future__ import annotations

import os
from datetime import datetime
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


def build_server(api: GitHubApi) -> MCPServer:
    server = MCPServer(
        "advent-github",
        version="17",
        instructions=(
            "Инструменты читают публичный GitHub. Цифры о репозиториях бери только из результата "
            "инструмента, не по памяти. Нужен один репозиторий — get_repository, несколько по теме — "
            "search_repositories, всё у одного автора — list_user_repositories."
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

    return server


def main() -> None:
    build_server(GitHubApi(os.getenv("GITHUB_TOKEN") or None)).run()


if __name__ == "__main__":
    main()

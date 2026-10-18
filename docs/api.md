# API и форматы данных

## Общие правила

- Запросы и ответы — JSON. Ошибка — HTTP 4xx/5xx и тело `{"error": "текст"}`.
- Долгие ответы идут потоком `application/x-ndjson`: каждая строка — JSON
  с полем `type`. Последнее событие — `done` или `error`.
- У каждого дня свой префикс: `/days/<N>/api/...`. Страница дня —
  `GET /days/<N>/`.

## Приложение: главные адреса по дням

| Дни | Адреса | Что делают |
|---|---|---|
| 1–5 | `POST /days/1/api/chat`, `/days/2/api/compare/stream`, `/days/3/api/solve/stream`, `/days/4/api/answer/stream`, `/days/5/api/compare/stream` | первые запросы к модели, форматы, способы рассуждения, температура, модели |
| 6–15 | `POST /days/<N>/api/chat/stream`, `GET /days/<N>/api/state`, `POST /days/<N>/api/reset` | чат с агентом: история на диске, токены, сжатие, ветки, память, профиль, задача, инварианты |
| 16 | `POST /days/16/api/inspect` | подключиться к MCP-серверу и получить его инструменты |
| 17–20 | `POST /days/<N>/api/chat/stream`, `GET /days/<N>/api/tools` | агент с инструментами MCP |
| 18 | `POST /days/18/api/collect`, `POST /days/18/api/summary` | снимок курса и сводка по расписанию |
| 21 | `POST /days/21/api/build`, `GET /days/21/api/search?q=&k=` | сборка индекса потоком, поиск |
| 22–24 | `POST /days/<N>/api/ask`, `POST /days/<N>/api/compare` | ответ по базе и прогон контрольных вопросов |
| 25 | `POST /days/25/api/chat` | мини-чат с RAG и памятью задачи |
| 26–29 | `POST /days/<N>/api/ask`, `POST /days/<N>/api/compare`, `GET /days/<N>/api/runs` | локальная модель: запросы, RAG, оптимизация, прогоны |
| 30 | `GET /days/30/api/status`, `POST /days/30/api/keys`, `DELETE /days/30/api/keys/<имя>`, `POST /days/30/api/load` | пульт приватного сервиса |
| 31 | `POST /days/31/api/help`, `POST /days/31/api/command`, `POST /days/31/api/index` | ассистент разработчика |
| 34 | `POST /days/34/api/run`, `GET /days/34/api/changes`, `POST /days/34/api/changes/apply`, `POST /days/34/api/changes/discard`, `GET /days/34/api/runs`, `GET /days/34/api/runs/<run_id>` | ассистент по файлам проекта: цель, черновик правок, прогоны |

### День 31: ассистент разработчика

`POST /days/31/api/help` — `{"question": "Где хранится память агента?"}`.
Поток событий:

- `retrieved` — куски документации из индекса: `n`, `source`, `section`, `lines`, `score`;
- `connected` — агент подключился к MCP-серверу git, список инструментов;
- `tool_call` / `tool_result` — вызовы инструментов git и их результат;
- `answer` — ответ модели в markdown со ссылками `[n]` на куски;
- `done` — токены и время.

`POST /days/31/api/command` — `{"command": "/branch"}`, `/files`, `/diff`
или `/log`: ответ прямо из MCP-сервера git, без модели.

### День 34: файлы проекта

`POST /days/34/api/run` — `{"goal": "Обнови docs/api.md под код дня 34"}`.
Цель не пустая и не длиннее 1500 символов (`MAX_GOAL`), иначе 400
`{"error": "Напишите цель"}` / `{"error": "Цель длиннее 1500 символов"}`.
Ответ — поток `application/x-ndjson`; событие `run` отдаётся кратко
(`id`, `started_at`, `goal`, `changes`, `stats`, `files_read`) и
сохраняется в `data/day34/runs.json`.

`GET /days/34/api/changes` — `{"files": [...]}`: что лежит в черновике
(`data/day34/draft`), с полями `path`, `new`, `added`, `removed`, `diff`.

`POST /days/34/api/changes/apply` — `{"paths": ["docs/api.md"]}` или пустое
тело (тогда применяется весь черновик). `paths` — список строк, иначе 400
`{"error": "paths — список путей"}`; ошибка пути — 400 с текстом ошибки.
Ответ: `{"applied": [...], "files": [...]}`.

`POST /days/34/api/changes/discard` — `{"discarded": <сколько файлов>, "files": []}`.

`GET /days/34/api/runs` — список прогонов, новые первыми, в кратком виде.
`GET /days/34/api/runs/<run_id>` — прогон целиком; если такого нет — 404
`{"error": "Такого прогона нет"}`.

## Приватный сервис (день 30)

Отдельный процесс: `.venv/bin/python service.py`, порт 8030. Наружу
выходит только он; Ollama остаётся на localhost.

| Адрес | Ключ | Что |
|---|---|---|
| `GET /` | нет | чат для людей |
| `GET /api/health` | нет | `{"ok", "model", "busy", "waiting"}` |
| `GET /api/me` | да | `{"name", "used", "limits"}` |
| `POST /api/chat` | да | ответ модели |
| `GET /admin/status?after=<seq>` | ключ администратора в `X-Admin-Token` | очередь, клиенты, журнал |

Ключ — заголовок `Authorization: Bearer sk-…`. Тело `POST /api/chat`:
`{"message": "текст"}` или `{"messages": [{"role": "user", "content": "…"}]}`;
с `"stream": false` ответ приходит целиком:
`{"answer", "queue_position", "waited_s", "output_tokens", "output_tps", "wall_s"}`.
Иначе — поток: `queued` (место в очереди, повторяется, пока ждём) →
`start` → `delta` × N → `done`.

| Код | Когда |
|---|---|
| 400 | тело не JSON-объект, пустое сообщение, последняя реплика не от user |
| 401 | нет ключа или ключ неверный |
| 413 | сообщение длиннее 2000 символов, диалог длиннее 12000, больше 30 сообщений |
| 429 | больше 5 запросов в минуту на ключ; заголовок `Retry-After` |
| 503 | в очереди уже ждут 8 запросов |

## MCP-серверы

| Сервер | Инструменты |
|---|---|
| `github` | `search_repositories(query, …)`, `get_repository(owner, repo)`, `list_user_repositories(user, …)`, `compare_repositories(full_names)`, `save_report(…)` |
| `rates` | `collect_rate()`, `rate_summary(minutes)`, `rate_history(minutes, limit)` |
| `git` | `git_branch()`, `git_status()`, `git_diff(scope=working\|branch, path)`, `git_log(limit, path)`, `list_files(path, pattern, limit)` |
| `crm` | `list_tickets(status)`, `get_ticket(ticket_id)`, `get_client(client)`, `client_events(client, limit)`, `service_status()`, `reply_to_ticket(ticket_id, text, status)` |
| `files` | `list_files(path, pattern, limit)`, `search(query, path, glob, regex, limit)`, `read_file(path, start, end)`, `write_file(path, content)`, `edit_file(path, old, new)`, `draft_diff()` |

`files` (`advent/mcp/servers/files.py`, имя сервера `advent-files`, версия 34,
запуск `python -m advent.mcp.servers.files`, stdio) видит только текстовые
файлы; `.git`, `data/`, `.venv`, `.env` и служебные папки скрыты. Правки
`write_file` и `edit_file` идут в черновик `data/day34/draft`, а не в проект;
писать можно только в `docs/*.md` и `.md` в корне. Корень — `ADVENT_FILES_ROOT`,
черновик — `ADVENT_FILES_DRAFT`.

## Форматы файлов в `data/`

### Индекс RAG — `data/day21/index.sqlite3`, `data/day31/index.sqlite3`

- `meta(key, value)` — ключ `index`: JSON с моделью векторов, размерностью,
  временем сборки и статистикой корпуса и нарезок;
- `chunks(id, strategy, chunk_id, source, title, section, kind, start_line, end_line, text, chars)` —
  куски: `strategy` — `fixed` или `structure`, `kind` — `doc`, `code` или `page`;
- `vectors(chunk, vector)` — вектор куска, float32 подряд;
- `cache(hash, vector)` — векторы по хэшу «модель + текст»: пересборка
  не считает заново то, что не изменилось.

### Диалоги — `data/agents/<сессия>-<день>.json`

`{"session_id", "saved_at", "dialog": {"messages": [...], "calls": [...], "turns": [...]}}` —
история в формате chat completions, журнал вызовов инструментов и
статистика ходов. Идентификатор сессии — `[A-Za-z0-9_-]{1,128}`.

### Прогоны — `data/day28/runs.json`, `data/day29/runs.json`

Список последних 8 прогонов, новые первыми:
`{"id", "started_at", "summary": {…по режимам…}, "rows": [{"index", "question", "negative", "modes": {…}}]}`.

### Ключи сервиса — `data/day30/keys.json`

`{"admin": "adm-…", "keys": [{"name", "key": "sk-…", "created_at", "test"}]}`.
Ключи с `test: true` создаёт проверка нагрузки и сразу удаляет.

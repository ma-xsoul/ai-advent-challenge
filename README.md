# AI Advent — домашние задания

Локальная коллекция заданий AI Advent. У каждого дня свой маршрут, шаблон, стили и при необходимости собственный API.

## Запуск

1. Создайте виртуальное окружение и установите зависимости:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Добавьте ключи в `.env`:

   ```dotenv
   DEEPSEEK_API_KEY=ваш_ключ
   DEEPSEEK_MODEL=deepseek-v4-flash
   OPENROUTER_API_KEY=ключ_openrouter
   ```

   Ключ OpenRouter нужен только пятому дню, получить его можно на <https://openrouter.ai/keys>.

3. Запустите приложение:

   ```bash
   python app.py
   ```

4. Откройте <http://127.0.0.1:5001>.

## Структура

```text
advent/
├── navigation.py            # единый список дней для меню
├── agent/                   # агент как отдельная сущность (день 6)
│   ├── config.py            #   неизменяемый конфиг: модель, промпт, температура, политика сжатия
│   ├── transport.py         #   HTTP-слой, подменяется в тестах
│   ├── agent.py             #   стек сообщений, вызов модели, учёт токенов, конспект истории
│   ├── registry.py          #   один процесс — много экземпляров агента
│   ├── store.py             #   JSON-файл на сессию: агент переживает перезапуск
│   ├── tokens.py            #   оценка токенов по символам, до запроса
│   └── scenarios.py         #   заготовленные диалоги для сравнений
├── days/
│   ├── day01/routes.py      # страница и API первого дня
│   ├── day02/routes.py      # страница и API второго дня
│   ├── day03/routes.py      # страница и API третьего дня
│   ├── day04/routes.py      # страница и API четвёртого дня
│   ├── day05/routes.py      # страница и API пятого дня
│   ├── day06/routes.py      # чат с агентом (день 6)
│   ├── day07/routes.py      # чат с агентом, который переживает перезапуск (день 7)
│   ├── day08/routes.py      # токены по ходам, лимит контекста, сценарий (день 8)
│   ├── day09/routes.py      # две дорожки: без сжатия и со сжатием истории (день 9)
│   └── agent_chat.py        # общие маршруты чата с агентом для дней 7+
├── services/
│   ├── deepseek.py          # интеграция с DeepSeek
│   └── openrouter.py        # интеграция с OpenRouter (день 5)
├── templates/
│   ├── base.html            # общая оболочка и меню
│   └── days/                # отдельный шаблон каждого дня
└── static/
    ├── css/                 # общие и дневные стили
    └── js/                  # скрипты конкретных дней
```

- `/` перенаправляет на `/days/1/`.
- Day 1 использует `POST /days/1/api/chat`.
- Старый `POST /api/chat` оставлен как совместимый адрес.
- Day 2 использует `POST /days/2/api/compare` и потоковый `POST /days/2/api/compare/stream`.
- Day 3 использует `POST /days/3/api/solve`, `POST /days/3/api/solve/stream` и `POST /days/3/api/task`.
- Day 4 использует `POST /days/4/api/answer`, потоковый `POST /days/4/api/answer/stream` и `POST /days/4/api/prompt`.
- Day 5 использует `POST /days/5/api/prompt` и потоковый `POST /days/5/api/compare/stream`.
- Day 5 обращается к OpenRouter: нужен `OPENROUTER_API_KEY` в `.env`.
- Day 6 использует `POST /days/6/api/chat/stream`, `POST /days/6/api/reset` и `GET /days/6/api/state`;
  агент живёт в памяти процесса, сессия — cookie `agent_session`.
- Day 7 использует те же маршруты под `/days/7/api/` плюс `POST /days/7/api/restart`;
  стек пишется в `data/agents/<сессия>.json` после каждого ответа и читается при старте.
- Day 8 добавляет `POST /days/8/api/limit` (лимит контекста агента) и `GET /days/8/api/scenario`;
  каждый ход записывает три числа — вопрос (оценка), стек и ответ (из API).
- Day 9 держит на одну сессию двух агентов (`<сессия>-plain` и `<сессия>-summary`) и использует
  `POST /days/9/api/chat/stream` (с полем `lane`), `POST /days/9/api/policy` (N, порог, лимит),
  `POST /days/9/api/compress`, `POST /days/9/api/reset`, `GET /days/9/api/state` и `GET /days/9/api/scenario?key=spec|spec-verbose`;
  у агента со сжатием старая часть стека сворачивается в конспект отдельным запросом к модели,
  конспект хранится отдельно и уходит в запрос вместо старых сообщений, а расход на него считается.

## Как добавить новый день

1. Создайте Blueprint в `advent/days/dayXX/routes.py`.
2. Добавьте страницу в `advent/templates/days/` и дневные ассеты в `advent/static/`.
3. Зарегистрируйте Blueprint в `advent/__init__.py`.
4. Добавьте метаданные дня в `advent/navigation.py` — пункт автоматически появится в меню.

## Проверка

```bash
python -m unittest discover -v
```

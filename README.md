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
│   ├── config.py            #   неизменяемый конфиг: модель, промпт, температура, стратегия контекста
│   ├── transport.py         #   HTTP-слой, подменяется в тестах
│   ├── agent.py             #   стек сообщений, вызов модели, учёт токенов, конспект, окно, факты, ветвление
│   ├── registry.py          #   один процесс — много экземпляров агента
│   ├── store.py             #   JSON-файл на сессию: агент переживает перезапуск
│   ├── tokens.py            #   оценка токенов по символам, до запроса
│   ├── scenarios.py         #   заготовленные диалоги для сравнений и факты для проверки
│   ├── branches.py          #   дерево веток диалога и его индекс на диске (день 10)
│   ├── memory.py            #   три слоя памяти, нарезатель и файлы слоёв (день 11)
│   ├── profile.py           #   профиль пользователя: стиль, формат, запреты, пайплайн (день 12)
│   ├── task.py              #   задача как конечный автомат: этап, шаг, ожидание, наблюдатель (день 13), таблица переходов и guard'ы (день 15)
│   └── invariants.py        #   инварианты в два рубежа: регулярка и ревизор (день 14)
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
│   ├── day10/routes.py      # стратегии контекста, ветки от checkpoint, таблица прогонов (день 10)
│   ├── day11/routes.py      # три слоя памяти, раскладка и их сроки жизни (день 11)
│   ├── day12/routes.py      # две дорожки с разными профилями, редактор профиля (день 12)
│   ├── day13/routes.py      # автомат задачи: переходы кнопками, пауза, продолжение (день 13)
│   ├── day14/routes.py      # инварианты: отказ модели, проверка кодом, журнал (день 14)
│   ├── day15/routes.py      # контролируемые переходы: одни ворота для кнопки, наблюдателя и модели (день 15)
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

`docs/day11-memory-model.md` — описание модели памяти агента (текстовая часть задания дня 11).

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
- Day 10 держит один диалог с ветками (`<сессия>-main`, `<сессия>-b1`, …) и дерево веток в `<сессия>-tree.json`;
  `POST /days/10/api/strategy` переключает стратегию контекста (`full` / `window` / `facts`, N) у активной ветки,
  `POST /days/10/api/checkpoint`, `POST /days/10/api/fork`, `POST /days/10/api/switch` — ветки от checkpoint,
  `POST|DELETE /days/10/api/runs` — таблица прогонов, плюс `chat/stream`, `state`, `reset`, `scenario?key=spec|spec-branches`.
- Day 11 держит три слоя памяти в отдельных файлах: `<сессия>-mem-chat.json`, `-task.json`, `-user.json`
  (плюс `-settings.json` с режимом раскладки); `POST /days/11/api/mode` выбирает, кто раскладывает
  (`agent` — отдельный запрос к модели, `heuristic` — слова-маркеры, `manual` — только руками),
  `POST /days/11/api/layers` — какие слои уходят в промпт, `POST|DELETE /days/11/api/memory`,
  `POST /days/11/api/memory/move` и `/memory/clear` — правка руками, `POST /days/11/api/task/new`
  убивает рабочий слой, `POST /days/11/api/reset` — диалог и краткосрочный, долговременный переживает оба.
- Day 12 держит две дорожки (`<сессия>-a`, `<сессия>-b`) с разными профилями и свои профили в `<сессия>-profiles.json`;
  профиль (обращение, тон, формат, запреты, шаги) уходит блоком в системный промпт каждого запроса — перед слоями памяти дня 11;
  `POST /days/12/api/lane` привязывает профиль к дорожке, `POST|DELETE /days/12/api/profile` — свой профиль
  (встроенные `maksim` / `customer` / `reviewer` не переписываются), плюс `chat/stream` (с полем `lane`), `state`, `reset`, `scenario`.
- Day 13 держит задачу в `<сессия>-task.json` (этап `planning → execution → validation → done`, шаги, ожидание, журнал);
  она рождается с первой реплики и уходит блоком в системный промпт; после ответа наблюдатель (JSON-запрос) докладывает,
  что произошло, а переход делает код. `POST /days/13/api/task/approve|step|verdict|pause|resume|new` — переходы кнопками,
  `POST /days/13/api/reset` стирает стек, но не задачу, плюс `chat/stream`, `state`, `scenario`.
- Day 14 держит инварианты в `<сессия>-invariants.json` (встроенные `stack` / `storage` / `passwords` / `paid` плюс свои);
  они уходят в системный промпт первыми, с процедурой «сверь и откажи»; после ответа регулярка ищет улики, ревизор
  (JSON-запрос) выносит вердикт — он главнее регулярки. `POST|DELETE /days/14/api/invariant`, `POST /days/14/api/invariant/toggle`,
  `DELETE /days/14/api/checks` — журнал, плюс `chat/stream`, `state`, `reset`, `scenario`.
- Day 15 — тот же автомат в строгом режиме: таблица переходов `planning → execution → validation → done`
  (+ `validation → execution`) с guard'ами (план утверждает и результат принимает только пользователь,
  на проверку — только с закрытыми шагами). `POST /days/15/api/task/transition` запрашивает любой переход:
  нелегальный — 409 с причиной и записью в журнал; модель просит переход строкой `[переход: …]` и проходит те же
  ворота. Задача и диалог отдельно от дня 13 (`<сессия>-task15.json`, агент `<сессия>-d15`), плюс `pause`, `resume`, `new`, `reset`, `scenario`.

## Как добавить новый день

1. Создайте Blueprint в `advent/days/dayXX/routes.py`.
2. Добавьте страницу в `advent/templates/days/` и дневные ассеты в `advent/static/`.
3. Зарегистрируйте Blueprint в `advent/__init__.py`.
4. Добавьте метаданные дня в `advent/navigation.py` — пункт автоматически появится в меню.

## Проверка

```bash
python -m unittest discover -v
```

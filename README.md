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
├── days/
│   ├── day01/routes.py      # страница и API первого дня
│   ├── day02/routes.py      # страница и API второго дня
│   ├── day03/routes.py      # страница и API третьего дня
│   ├── day04/routes.py      # страница и API четвёртого дня
│   └── day05/routes.py      # страница и API пятого дня
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

## Как добавить новый день

1. Создайте Blueprint в `advent/days/dayXX/routes.py`.
2. Добавьте страницу в `advent/templates/days/` и дневные ассеты в `advent/static/`.
3. Зарегистрируйте Blueprint в `advent/__init__.py`.
4. Добавьте метаданные дня в `advent/navigation.py` — пункт автоматически появится в меню.

## Проверка

```bash
python -m unittest discover -v
```

# AI Advent — домашние задания

Локальная коллекция заданий AI Advent. У каждого дня свой маршрут, шаблон, стили и при необходимости собственный API.

## Запуск

1. Создайте виртуальное окружение и установите зависимости:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Для Day 1 добавьте ключ в `.env`:

   ```dotenv
   DEEPSEEK_API_KEY=ваш_ключ
   DEEPSEEK_MODEL=deepseek-v4-flash
   ```

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
│   └── day02/routes.py      # страница второго дня
├── services/deepseek.py     # интеграция с DeepSeek
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
- Day 2 — самостоятельная заготовка под следующее задание.

## Как добавить новый день

1. Создайте Blueprint в `advent/days/dayXX/routes.py`.
2. Добавьте страницу в `advent/templates/days/` и дневные ассеты в `advent/static/`.
3. Зарегистрируйте Blueprint в `advent/__init__.py`.
4. Добавьте метаданные дня в `advent/navigation.py` — пункт автоматически появится в меню.

## Проверка

```bash
python -m unittest discover -v
```

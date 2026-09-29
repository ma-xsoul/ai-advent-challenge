import json
import unittest
from unittest.mock import patch

from advent import create_app


class AdventAppTestCase(unittest.TestCase):
    def setUp(self):
        app = create_app({"TESTING": True})
        self.client = app.test_client()

    def test_root_redirects_to_day_one(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/days/1/"))

    def test_day_one_renders_active_navigation_and_its_script(self):
        response = self.client.get("/days/1/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("День 1.", html)
        self.assertIn("Первый запрос к LLM", html)
        self.assertIn("Домашнее задание", html)
        self.assertIn("Ожидаемый результат", html)
        self.assertIn("css/homework.css", html)
        self.assertIn('aria-current="page"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('class="homework-divider"', html)
        self.assertIn("js/day01.js", html)
        self.assertIn('href="/days/2/"', html)

    def test_day_two_uses_the_same_homework_layout_as_day_one(self):
        response = self.client.get("/days/2/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("День 2.", html)
        self.assertIn("Формат ответа", html)
        self.assertIn("Домашнее задание", html)
        self.assertIn("Ожидаемый результат", html)
        self.assertIn("stop sequence", html)
        self.assertIn("Сравнить два ответа", html)
        self.assertIn("Отправить запрос и сравнить ответы", html)
        self.assertIn("С параметрами", html)
        self.assertIn("Показать отправленные запросы", html)
        self.assertIn("### END ###", html)
        self.assertIn('data-custom-select="format"', html)
        self.assertIn('data-custom-select="model"', html)
        self.assertIn("deepseek-v4-flash", html)
        self.assertIn("deepseek-v4-pro", html)
        self.assertNotIn("<select", html)
        self.assertIn("css/day01.css", html)
        self.assertIn("css/homework.css", html)
        self.assertIn("css/day02.css", html)
        self.assertNotIn("js/day01.js", html)
        self.assertIn("js/day02.js", html)
        self.assertIn('href="/days/2/"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('class="homework-divider"', html)

    def test_day_three_renders_homework_and_reasoning_playground(self):
        response = self.client.get("/days/3/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("День 3.", html)
        self.assertIn("Способы рассуждения", html)
        self.assertIn("Домашнее задание", html)
        self.assertIn("решай пошагово", html)
        self.assertIn("группу экспертов", html)
        self.assertIn("Несколько решений одной задачи и их сравнение", html)
        self.assertIn("css/day01.css", html)
        self.assertIn("css/homework.css", html)
        self.assertIn("css/day03.css", html)
        self.assertIn("Решить и сравнить", html)
        self.assertIn("Все четыре способа", html)
        self.assertIn("Промпт от модели", html)
        self.assertIn("Мета-анализ", html)
        self.assertIn("Сгенерировать задачу", html)
        self.assertIn('data-solve-url="/days/3/api/solve"', html)
        self.assertIn('data-task-url="/days/3/api/task"', html)
        self.assertNotIn("js/day01.js", html)
        self.assertNotIn("js/day02.js", html)
        self.assertIn("js/day03.js", html)
        self.assertIn('href="/days/3/"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('class="homework-divider"', html)

    def test_day_four_renders_the_temperature_homework(self):
        response = self.client.get("/days/4/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("День 4.", html)
        self.assertIn("Температура", html)
        self.assertIn("Домашнее задание", html)
        self.assertIn("temperature = 0", html)
        self.assertIn("temperature = 0.7", html)
        self.assertIn("temperature = 1.2", html)
        self.assertIn("точности, креативности и разнообразию", html)
        self.assertIn(
            "Примеры ответов с разной температурой и выводы по их использованию",
            html,
        )
        self.assertIn("css/day01.css", html)
        self.assertIn("css/homework.css", html)
        self.assertIn("css/day04.css", html)
        self.assertIn('href="/days/4/"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('class="homework-divider"', html)

    def test_day_four_theory_block_is_collapsed_by_default(self):
        response = self.client.get("/days/4/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('<details class="theory-brief">', html)
        self.assertNotIn('<details class="theory-brief" open>', html)
        self.assertIn('class="theory-brief__title"', html)
        self.assertIn("Теория", html)
        self.assertIn("softmax(z / T)", html)
        self.assertIn("Как это работает", html)
        self.assertIn("Где какая настройка уместна", html)
        self.assertIn("Что важно помнить", html)

    @patch("advent.days.day03.routes.request_day_three_solutions")
    def test_day_three_forwards_selected_reasoning_approach(self, solve_request):
        solve_request.return_value = (
            {
                "task": "Найди закономерность",
                "model": "deepseek-v4-pro",
                "approach": "all",
                "results": {},
                "analysis": None,
            },
            200,
        )

        response = self.client.post(
            "/days/3/api/solve",
            json={
                "task": "  Найди закономерность  ",
                "model": "deepseek-v4-pro",
                "approach": "all",
            },
        )

        self.assertEqual(response.status_code, 200)
        solve_request.assert_called_once_with(
            "Найди закономерность",
            model="deepseek-v4-pro",
            approach="all",
        )

    @patch("advent.days.day03.routes.stream_day_three_solutions")
    def test_day_three_proxies_reasoning_events_as_ndjson(self, stream_solutions):
        stream_solutions.return_value = iter(
            [
                {"type": "approach_start", "approach": "direct"},
                {
                    "type": "delta",
                    "approach": "direct",
                    "target": "answer",
                    "content": "Ответ",
                },
                {"type": "complete", "results_count": 1},
            ]
        )

        response = self.client.post(
            "/days/3/api/solve/stream",
            json={
                "task": "  Простая задача  ",
                "model": "deepseek-v4-flash",
                "approach": "direct",
            },
        )
        events = [json.loads(line) for line in response.get_data(as_text=True).splitlines()]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/x-ndjson")
        self.assertEqual(
            [event["type"] for event in events],
            ["approach_start", "delta", "complete"],
        )
        stream_solutions.assert_called_once_with(
            "Простая задача",
            model="deepseek-v4-flash",
            approach="direct",
        )

    @patch("advent.days.day03.routes.request_day_three_task")
    def test_day_three_generates_a_task_for_the_selected_model(self, task_request):
        task_request.return_value = (
            {"content": "Спланируйте приготовление ужина."},
            200,
        )

        response = self.client.post(
            "/days/3/api/task",
            json={"model": "deepseek-v4-pro"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["content"],
            "Спланируйте приготовление ужина.",
        )
        task_request.assert_called_once_with(model="deepseek-v4-pro")

    def test_day_three_rejects_an_unknown_reasoning_approach(self):
        response = self.client.post(
            "/days/3/api/solve",
            json={
                "task": "Тест",
                "model": "deepseek-v4-flash",
                "approach": "unknown",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "Выберите способ решения"})

    def test_day_six_renders_the_agent_homework(self):
        response = self.client.get("/days/6/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("День 6.", html)
        self.assertIn("Первый агент", html)
        self.assertIn("Домашнее задание", html)
        self.assertIn("отдельной сущностью", html)
        self.assertIn("Инкапсулируйте логику запроса и ответа", html)
        self.assertIn("Агент принимает запрос и корректно вызывает LLM через API", html)
        self.assertIn("css/day01.css", html)
        self.assertIn("css/homework.css", html)
        self.assertIn("css/lab.css", html)
        self.assertIn("css/day06.css", html)
        self.assertIn("css/agent-chat.css", html)
        self.assertIn("js/agent-chat.js", html)
        self.assertIn('id="agent-form"', html)
        self.assertIn("agent.snapshot()", html)
        self.assertIn("Новый диалог", html)
        self.assertIn('data-chat-url="/days/6/api/chat/stream"', html)
        self.assertIn("agent_session", response.headers.get("Set-Cookie", ""))
        self.assertIn('href="/days/6/"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('class="homework-divider"', html)

    def test_day_seven_renders_the_persistence_homework(self):
        response = self.client.get("/days/7/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("День 7.", html)
        self.assertIn("Сохранение контекста", html)
        self.assertIn("Домашнее задание", html)
        self.assertIn("JSON или SQLite", html)
        self.assertIn("как будто агент не выключался", html)
        self.assertIn("сохраняет и восстанавливает контекст между запусками", html)
        self.assertIn("css/day01.css", html)
        self.assertIn("css/homework.css", html)
        self.assertIn("css/agent-chat.css", html)
        self.assertIn("css/day07.css", html)
        self.assertIn("js/agent-chat.js", html)
        self.assertIn('id="agent-restart"', html)
        self.assertIn("JsonStore", html)
        self.assertIn('data-restart-url="/days/7/api/restart"', html)
        self.assertIn('href="/days/7/"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('class="homework-divider"', html)

    def test_day_eight_renders_the_tokens_homework(self):
        response = self.client.get("/days/8/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("День 8.", html)
        self.assertIn("Работа с токенами", html)
        self.assertIn("Домашнее задание", html)
        self.assertIn("для текущего запроса, для всей истории диалога, для ответа модели", html)
        self.assertIn("превышает лимит модели", html)
        self.assertIn("что ломается при переполнении", html)
        self.assertIn("считает токены и показывает, как они влияют на поведение агента", html)
        self.assertIn("css/day01.css", html)
        self.assertIn("css/homework.css", html)
        self.assertIn("css/agent-chat.css", html)
        self.assertIn("css/day08.css", html)
        self.assertIn("js/agent-chat.js", html)
        self.assertIn('id="agent-limit-form"', html)
        self.assertIn('id="agent-scenario"', html)
        self.assertIn('data-limit-url="/days/8/api/limit"', html)
        self.assertIn('data-scenario-url="/days/8/api/scenario"', html)
        self.assertIn("Собираем ТЗ", html)
        self.assertIn('data-turns', html)
        self.assertIn('href="/days/8/"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('class="homework-divider"', html)

    def test_day_nine_renders_the_compression_homework(self):
        response = self.client.get("/days/9/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("День 9.", html)
        self.assertIn("Сжатие истории", html)
        self.assertIn("Домашнее задание", html)
        self.assertIn("последние N сообщений", html)
        self.assertIn("каждые 10 сообщений", html)
        self.assertIn("вместо полной истории", html)
        self.assertIn("расход токенов до и после", html)
        self.assertIn("работает с компрессией истории и экономит токены", html)
        # Практика: две дорожки, политика и сравнение.
        self.assertIn('data-lane="plain"', html)
        self.assertIn('data-lane="summary"', html)
        self.assertIn("Без сжатия", html)
        self.assertIn("Со сжатием", html)
        self.assertIn('id="day9-policy"', html)
        self.assertIn("Сжать сейчас", html)
        self.assertIn('id="agent-initial"', html)
        self.assertIn("css/day01.css", html)
        self.assertIn("css/homework.css", html)
        self.assertIn("css/day09.css", html)
        self.assertIn('href="/days/9/"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('class="homework-divider"', html)

    def test_day_ten_renders_the_strategies_homework(self):
        response = self.client.get("/days/10/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("День 10.", html)
        self.assertIn("Стратегии контекста", html)
        self.assertIn("Домашнее задание", html)
        self.assertIn("Sliding Window", html)
        self.assertIn("Sticky Facts", html)
        self.assertIn("Branching", html)
        self.assertIn("последние N сообщений", html)
        self.assertIn("checkpoint", html)
        self.assertIn("качество ответа", html)
        self.assertIn("расход токенов", html)
        self.assertIn("Агент с тремя стратегиями", html)
        # Практика: переключатель, лента, факты, ветки, таблица прогонов.
        self.assertIn('id="day10-strategy"', html)
        self.assertIn('value="window"', html)
        self.assertIn('value="facts"', html)
        self.assertIn('id="day10-checkpoint"', html)
        self.assertIn("data-facts", html)
        self.assertIn("data-tree", html)
        self.assertIn("data-runs", html)
        self.assertIn('id="agent-initial"', html)
        self.assertIn("css/day01.css", html)
        self.assertIn("css/homework.css", html)
        self.assertIn("css/day10.css", html)
        self.assertIn('href="/days/10/"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('class="homework-divider"', html)

    def test_every_day_collapses_its_homework_block_by_default(self):
        for number in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17):
            with self.subTest(day=number):
                response = self.client.get(f"/days/{number}/")
                html = response.get_data(as_text=True)

                self.assertEqual(response.status_code, 200)
                self.assertIn('<details class="homework-brief" aria-label=', html)
                self.assertNotIn('<details class="homework-brief" open', html)
                self.assertIn('<summary class="homework-brief__title">', html)
                self.assertNotIn('<section class="homework-brief"', html)
                self.assertIn("<h2>Домашнее задание</h2>", html)

    def test_day_four_renders_the_temperature_lab(self):
        response = self.client.get("/days/4/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="temperature-slider"', html)
        self.assertIn('type="range"', html)
        self.assertIn('min="0.0"', html)
        self.assertIn('max="2.0"', html)
        self.assertIn("Сгенерировать запрос", html)
        self.assertIn("Отправить запрос", html)
        self.assertIn("Сравнить все четыре температуры", html)
        self.assertIn('data-answer-url="/days/4/api/answer"', html)
        self.assertIn('data-prompt-url="/days/4/api/prompt"', html)
        self.assertIn('id="temperature-card-template"', html)
        self.assertIn("Мета-анализ", html)
        self.assertIn("css/lab.css", html)
        self.assertIn("css/day04.css", html)
        self.assertIn("js/day04.js", html)
        self.assertNotIn("js/day03.js", html)
        for label in ("0", "0.7", "1.4", "2"):
            self.assertIn(f'data-temperature="{label}"', html)
        self.assertIn("Сравнить все четыре температуры", html)

    @patch("advent.days.day04.routes.request_day_four_answers")
    def test_day_four_forwards_a_single_temperature(self, answer_request):
        answer_request.return_value = ({"results": {}}, 200)

        response = self.client.post(
            "/days/4/api/answer",
            json={
                "prompt": "  Придумай слоган  ",
                "model": "deepseek-v4-pro",
                "temperature": 1.2,
            },
        )

        self.assertEqual(response.status_code, 200)
        answer_request.assert_called_once_with(
            "Придумай слоган",
            model="deepseek-v4-pro",
            temperature=1.2,
        )

    @patch("advent.days.day04.routes.request_day_four_answers")
    def test_day_four_forwards_the_all_temperatures_run(self, answer_request):
        answer_request.return_value = ({"results": {}}, 200)

        response = self.client.post(
            "/days/4/api/answer",
            json={
                "prompt": "Придумай слоган",
                "model": "deepseek-v4-flash",
                "temperature": "all",
            },
        )

        self.assertEqual(response.status_code, 200)
        answer_request.assert_called_once_with(
            "Придумай слоган",
            model="deepseek-v4-flash",
            temperature="all",
        )

    @patch("advent.days.day04.routes.stream_day_four_answers")
    def test_day_four_proxies_answer_events_as_ndjson(self, stream_answers):
        stream_answers.return_value = iter(
            [
                {"type": "answer_start", "label": "0", "temperature": 0.0},
                {"type": "delta", "label": "0", "target": "answer", "content": "Ответ"},
                {"type": "answer_done", "label": "0", "answer": {"content": "Ответ"}},
                {"type": "complete", "results_count": 1},
            ]
        )

        response = self.client.post(
            "/days/4/api/answer/stream",
            json={
                "prompt": "  Придумай слоган  ",
                "model": "deepseek-v4-flash",
                "temperature": 0,
            },
        )
        events = [json.loads(line) for line in response.get_data(as_text=True).splitlines()]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/x-ndjson")
        self.assertEqual(
            [event["type"] for event in events],
            ["answer_start", "delta", "answer_done", "complete"],
        )
        stream_answers.assert_called_once_with(
            "Придумай слоган",
            model="deepseek-v4-flash",
            temperature=0.0,
        )

    @patch("advent.days.day04.routes.request_day_four_prompt")
    def test_day_four_generates_a_prompt_for_the_selected_model(self, prompt_request):
        prompt_request.return_value = ({"content": "Придумай метафору для дождя."}, 200)

        response = self.client.post("/days/4/api/prompt", json={"model": "deepseek-v4-pro"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["content"],
            "Придумай метафору для дождя.",
        )
        prompt_request.assert_called_once_with(model="deepseek-v4-pro")

    def test_day_four_rejects_a_temperature_outside_the_api_range(self):
        for value in (-0.1, 2.1, 5):
            with self.subTest(temperature=value):
                response = self.client.post(
                    "/days/4/api/answer",
                    json={
                        "prompt": "Тест",
                        "model": "deepseek-v4-flash",
                        "temperature": value,
                    },
                )

                self.assertEqual(response.status_code, 400)
                self.assertIn("от 0 до 2", response.get_json()["error"])

    def test_day_four_rejects_a_non_numeric_temperature(self):
        for value in ("hot", None, True, [1]):
            with self.subTest(temperature=value):
                response = self.client.post(
                    "/days/4/api/answer",
                    json={
                        "prompt": "Тест",
                        "model": "deepseek-v4-flash",
                        "temperature": value,
                    },
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.get_json(),
                    {"error": "Укажите температуру числом"},
                )

    def test_day_five_renders_the_model_versions_homework(self):
        response = self.client.get("/days/5/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("День 5.", html)
        self.assertIn("Версии моделей", html)
        self.assertIn("Домашнее задание", html)
        self.assertIn("слабой модели", html)
        self.assertIn("средней модели", html)
        self.assertIn("сильной модели", html)
        self.assertIn("HuggingFace", html)
        self.assertIn("время ответа, количество токенов и стоимость", html)
        self.assertIn("качество ответов, скорость и ресурсоёмкость", html)
        self.assertIn(
            "Короткий вывод о различиях между моделями и ссылки на них",
            html,
        )
        self.assertIn("css/day01.css", html)
        self.assertIn("css/homework.css", html)
        self.assertIn("css/day05.css", html)
        self.assertIn("css/lab.css", html)
        self.assertIn("js/day05.js", html)
        self.assertIn("Сгенерировать запрос", html)
        self.assertIn("Запросить три модели", html)
        self.assertIn('id="models-card-template"', html)
        self.assertIn('data-prompt-url="/days/5/api/prompt"', html)
        self.assertIn('data-compare-url="/days/5/api/compare/stream"', html)
        self.assertIn("qwen/qwen3.7-flash", html)
        self.assertIn("qwen/qwen3-30b-a3b-instruct-2507", html)
        self.assertIn("moonshotai/kimi-k3", html)
        self.assertIn("deepseek-v4-pro", html)
        self.assertIn('href="/days/5/"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('class="homework-divider"', html)

    @patch("advent.days.day05.routes.request_day_five_prompt")
    def test_day_five_generates_a_prompt(self, prompt_request):
        prompt_request.return_value = ({"content": "Объясни, почему небо голубое."}, 200)

        response = self.client.post("/days/5/api/prompt", json={"model": "deepseek-v4-flash"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["content"],
            "Объясни, почему небо голубое.",
        )
        prompt_request.assert_called_once_with(model="deepseek-v4-flash")

    @patch("advent.days.day05.routes.stream_day_five_analysis")
    @patch("advent.days.day05.routes.stream_day_five_answers")
    def test_day_five_chains_openrouter_models_and_the_deepseek_analysis(
        self, stream_answers, stream_analysis
    ):
        answer = {"content": "Ответ", "seconds": 1.2, "output_tokens": 40, "cost": 0.00001}
        stream_answers.return_value = iter(
            [
                {"type": "model_start", "tier": "weak", "label": "Слабая", "slug": "a"},
                {"type": "delta", "tier": "weak", "target": "answer", "content": "От"},
                {"type": "model_done", "tier": "weak", "answer": answer},
                {"type": "models_complete", "results": {"weak": answer}},
            ]
        )

        def fake_analysis(prompt, *, model, results):
            yield {"type": "delta", "target": "analysis", "content": "Итог"}
            return {"content": "Итог"}, None

        stream_analysis.side_effect = fake_analysis

        response = self.client.post(
            "/days/5/api/compare/stream",
            json={"prompt": "  Почему небо голубое?  "},
        )
        events = [json.loads(line) for line in response.get_data(as_text=True).splitlines()]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/x-ndjson")
        self.assertEqual(
            [event["type"] for event in events],
            [
                "model_start",
                "delta",
                "model_done",
                "analysis_start",
                "delta",
                "analysis_done",
                "complete",
            ],
        )
        # models_complete — внутреннее событие, наружу уходить не должно.
        self.assertNotIn("models_complete", [event["type"] for event in events])
        stream_answers.assert_called_once_with("Почему небо голубое?")
        self.assertEqual(stream_analysis.call_args.kwargs["model"], "deepseek-v4-pro")

    def test_day_five_rejects_an_empty_prompt(self):
        response = self.client.post("/days/5/api/compare/stream", json={"prompt": "   "})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "Введите запрос"})

    def _day_six_client(self):
        from tests.test_agent import FakeTransport

        transport = FakeTransport(replies=["Из-за рассеяния света.", "Рассеяние."])
        app = create_app({"TESTING": True, "AGENT_TRANSPORT": transport})
        return app.test_client(), transport

    def test_day_six_streams_the_agent_reply_and_keeps_the_dialogue(self):
        client, transport = self._day_six_client()

        first = client.post("/days/6/api/chat/stream", json={"message": " Почему небо голубое? "})
        events = [json.loads(line) for line in first.get_data(as_text=True).splitlines()]

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.mimetype, "application/x-ndjson")
        self.assertEqual(events[0]["type"], "delta")
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["reply"]["content"], "Из-за рассеяния света.")
        self.assertEqual(events[-1]["snapshot"]["usage"]["requests"], 1)
        self.assertEqual(len(events[-1]["snapshot"]["messages"]), 2)

        # Второй запрос той же сессии уходит вместе с первым обменом.
        client.post("/days/6/api/chat/stream", json={"message": "А теперь короче."})
        roles = [m["role"] for m in transport.payloads[1]["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])

    def test_day_six_keeps_separate_agents_for_separate_sessions(self):
        client, transport = self._day_six_client()
        client.post("/days/6/api/chat/stream", json={"message": "Первая сессия"})

        client.delete_cookie("agent_session")
        state = client.get("/days/6/api/state").get_json()

        # Незнакомая сессия видит пустого агента, но в реестр не попадает:
        # просмотр страницы не плодит записи.
        self.assertEqual(state["messages"], [])
        self.assertEqual(state["agents_in_registry"], 1)

    def test_day_six_reset_starts_a_fresh_agent(self):
        client, _ = self._day_six_client()
        client.post("/days/6/api/chat/stream", json={"message": "Вопрос"})

        state = client.post("/days/6/api/reset").get_json()

        self.assertEqual(state["messages"], [])
        self.assertEqual(state["usage"]["requests"], 0)

    def test_day_six_reports_a_transport_error_without_breaking_the_stack(self):
        from tests.test_agent import FakeTransport
        from advent.agent import TransportError

        transport = FakeTransport(error=TransportError("сеть упала"))
        client = create_app({"TESTING": True, "AGENT_TRANSPORT": transport}).test_client()

        response = client.post("/days/6/api/chat/stream", json={"message": "Вопрос"})
        events = [json.loads(line) for line in response.get_data(as_text=True).splitlines()]

        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("сеть упала", events[-1]["error"])
        self.assertEqual(events[-1]["snapshot"]["messages"], [])

    def test_day_six_rejects_an_empty_message(self):
        client, _ = self._day_six_client()

        response = client.post("/days/6/api/chat/stream", json={"message": "  "})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "Введите сообщение"})

    def _day_seven_app(self, store_dir, replies=None):
        from tests.test_agent import FakeTransport

        transport = FakeTransport(replies=list(replies or ["Запомнил.", "Тебя зовут Максим."]))
        return create_app({"TESTING": True, "AGENT_STORE_DIR": store_dir, "AGENT_TRANSPORT": transport})

    @staticmethod
    def _chat(client, url, message):
        """Потоковый ответ ленивый: сохранение на диск происходит при чтении тела."""
        response = client.post(url, json={"message": message})
        response.get_data()
        return response

    def test_day_seven_writes_the_stack_to_disk_after_each_reply(self):
        import json as json_module
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as store_dir:
            client = self._day_seven_app(store_dir).test_client()
            client.set_cookie("agent_session", "s7")

            response = client.post("/days/7/api/chat/stream", json={"message": "Меня зовут Максим"})
            events = [json_module.loads(line) for line in response.get_data(as_text=True).splitlines()]

            self.assertEqual(events[-1]["type"], "done")
            self.assertIsNone(events[-1]["save_error"])
            self.assertEqual(events[-1]["snapshot"]["storage"]["messages"], 2)

            saved = json_module.loads(Path(store_dir, "s7.json").read_text(encoding="utf-8"))
            self.assertEqual([m["content"] for m in saved["messages"]], ["Меня зовут Максим", "Запомнил."])

    def test_day_seven_survives_a_real_process_restart(self):
        """Новый экземпляр приложения — новая память процесса, файл тот же."""
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            first_run = self._day_seven_app(store_dir).test_client()
            first_run.set_cookie("agent_session", "s7")
            self._chat(first_run, "/days/7/api/chat/stream", "Меня зовут Максим")

            second_app = self._day_seven_app(store_dir, replies=["Тебя зовут Максим."])
            second_run = second_app.test_client()
            second_run.set_cookie("agent_session", "s7")

            page = second_run.get("/days/7/").get_data(as_text=True)
            self.assertIn('data-debug="stack">2<', page)
            self.assertIn('data-storage="messages">2<', page)
            state = second_run.get("/days/7/api/state").get_json()
            self.assertEqual(state["messages"][0]["content"], "Меня зовут Максим")

            self._chat(second_run, "/days/7/api/chat/stream", "Как меня зовут?")
            transport = second_app.config["AGENT_TRANSPORT"]
            self.assertEqual(
                [m["role"] for m in transport.payloads[0]["messages"]],
                ["system", "user", "assistant", "user"],
            )
            self.assertEqual(transport.payloads[0]["messages"][1]["content"], "Меня зовут Максим")

    def test_day_seven_restart_endpoint_reloads_the_agent_from_disk(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            app = self._day_seven_app(store_dir)
            client = app.test_client()
            client.set_cookie("agent_session", "s7")
            self._chat(client, "/days/7/api/chat/stream", "Привет")
            before = app.extensions["persistent_agent_registry"].get("s7")

            state = client.post("/days/7/api/restart").get_json()

            after = app.extensions["persistent_agent_registry"].get("s7")
            self.assertIsNot(before, after)
            self.assertEqual([m["content"] for m in state["messages"]], ["Привет", "Запомнил."])
            self.assertEqual(state["usage"]["requests"], 1)

    def test_day_seven_reset_removes_the_file(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as store_dir:
            client = self._day_seven_app(store_dir).test_client()
            client.set_cookie("agent_session", "s7")
            self._chat(client, "/days/7/api/chat/stream", "Привет")
            self.assertTrue(Path(store_dir, "s7.json").exists())

            state = client.post("/days/7/api/reset").get_json()

            self.assertFalse(Path(store_dir, "s7.json").exists())
            self.assertIsNone(state["storage"])
            self.assertEqual(state["messages"], [])

    def test_day_seven_reports_a_corrupt_session_file(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as store_dir:
            Path(store_dir, "s7.json").write_text("{битый", encoding="utf-8")
            client = self._day_seven_app(store_dir).test_client()
            client.set_cookie("agent_session", "s7")

            response = client.get("/days/7/api/state")

            self.assertEqual(response.status_code, 500)
            self.assertIn("s7.json", response.get_json()["error"])

    def test_day_six_stays_in_memory_only(self):
        """Реестр дня 6 без диска: тот же cookie, но своя, непостоянная память."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as store_dir:
            client = self._day_seven_app(store_dir).test_client()
            client.set_cookie("agent_session", "s6")
            self._chat(client, "/days/6/api/chat/stream", "Привет")

            self.assertFalse(Path(store_dir, "s6.json").exists())

    def _day_eight_client(self, store_dir, replies=None):
        from tests.test_agent import FakeTransport

        transport = FakeTransport(replies=list(replies or []))
        app = create_app({"TESTING": True, "AGENT_STORE_DIR": store_dir, "AGENT_TRANSPORT": transport})
        client = app.test_client()
        client.set_cookie("agent_session", "s8")
        return client, transport

    def test_day_eight_reports_the_three_numbers_for_each_turn(self):
        import json as json_module
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_eight_client(store_dir, replies=["Понял."])

            response = self._chat(client, "/days/8/api/chat/stream", "Платформа — только Android.")
            done = [json_module.loads(l) for l in response.get_data(as_text=True).splitlines()][-1]

            self.assertEqual(done["type"], "done")
            turn = done["turn"]
            self.assertEqual(turn["index"], 1)
            self.assertGreater(turn["question_tokens"], 0)
            self.assertEqual(turn["context_tokens"], 10)
            self.assertEqual(turn["answer_tokens"], 5)
            self.assertEqual(done["snapshot"]["turns"][0]["total_tokens"], 15)
            self.assertEqual(done["snapshot"]["context"]["estimated"], 15)

    def test_day_eight_sets_and_clears_the_context_limit_without_losing_the_dialogue(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_eight_client(store_dir)
            self._chat(client, "/days/8/api/chat/stream", "Привет")

            limited = client.post("/days/8/api/limit", json={"limit": 300}).get_json()
            self.assertEqual(limited["context"]["limit"], 300)
            self.assertEqual(len(limited["messages"]), 2)

            cleared = client.post("/days/8/api/limit", json={"limit": None}).get_json()
            self.assertIsNone(cleared["context"]["limit"])
            self.assertEqual(len(cleared["messages"]), 2)

    def test_day_eight_rejects_a_bad_limit(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_eight_client(store_dir)

            for bad in ("много", 5, 999_999, True):
                with self.subTest(limit=bad):
                    response = client.post("/days/8/api/limit", json={"limit": bad})
                    self.assertEqual(response.status_code, 400)

    def test_day_eight_streams_an_overflow_event_and_keeps_the_stack_intact(self):
        import json as json_module
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_eight_client(store_dir)
            self._chat(client, "/days/8/api/chat/stream", "Раз")
            client.post("/days/8/api/limit", json={"limit": 100})

            long_message = "Очень длинная реплика про требования к приложению, " * 8
            response = self._chat(client, "/days/8/api/chat/stream", long_message)
            events = [json_module.loads(l) for l in response.get_data(as_text=True).splitlines()]

            self.assertEqual([e["type"] for e in events], ["error"])
            self.assertEqual(events[0]["overflow"]["limit"], 100)
            self.assertGreater(events[0]["overflow"]["estimated"], 100)
            self.assertIn("не отправлен", events[0]["error"])
            # Стек не тронут, запрос к модели не уходил.
            self.assertEqual(len(events[0]["snapshot"]["messages"]), 2)
            self.assertEqual(len(transport.payloads), 1)

    def test_day_eight_serves_the_spec_scenario(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_eight_client(store_dir)

            scenario = client.get("/days/8/api/scenario").get_json()

            self.assertEqual(scenario["key"], "spec")
            self.assertEqual(len(scenario["messages"]), 12)

    def test_day_eight_persists_turns_across_restart(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_eight_client(store_dir)
            self._chat(client, "/days/8/api/chat/stream", "Раз")
            self._chat(client, "/days/8/api/chat/stream", "Два")

            state = client.post("/days/8/api/restart").get_json()

            self.assertEqual([t["index"] for t in state["turns"]], [1, 2])
            self.assertEqual(state["turns"][1]["total_tokens"], 30)

    def _day_nine_client(self, store_dir, replies=None, measuring=False):
        from tests.test_agent import FakeTransport, MeasuringTransport

        transport_cls = MeasuringTransport if measuring else FakeTransport
        transport = transport_cls(replies=list(replies or []))
        app = create_app({"TESTING": True, "AGENT_STORE_DIR": store_dir, "AGENT_TRANSPORT": transport})
        client = app.test_client()
        client.set_cookie("agent_session", "s9")
        return client, transport

    @staticmethod
    def _events(response):
        import json as json_module

        return [json_module.loads(line) for line in response.get_data(as_text=True).splitlines()]

    def test_day_nine_keeps_two_agents_per_session_with_different_configs(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_nine_client(store_dir)

            state = client.get("/days/9/api/state").get_json()

            self.assertIsNone(state["lanes"]["plain"]["config"]["compression"])
            self.assertEqual(state["lanes"]["summary"]["config"]["compression"]["every"], 10)
            self.assertEqual(state["policy"], {"keep_last": 4, "every": 10, "summary_max_tokens": 800, "limit": None})
            self.assertEqual(state["compare"]["lanes"]["summary"]["facts_total"], 13)
            # Просмотр страницы не плодит агентов в реестре.
            self.assertEqual(state["lanes"]["plain"]["agents_in_registry"], 0)

    def test_day_nine_streams_to_one_lane_and_stores_it_under_its_own_file(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_nine_client(store_dir, replies=["Понял."])

            response = self._chat_lane(client, "summary", "Платформа — Android.")
            events = self._events(response)

            self.assertEqual(events[-1]["type"], "done")
            self.assertEqual(events[-1]["snapshot"]["storage"]["file"], "s9-summary.json")
            self.assertTrue(Path(store_dir, "s9-summary.json").exists())
            self.assertFalse(Path(store_dir, "s9-plain.json").exists())
            # Cookie сессии — базовый id, а не id дорожки.
            self.assertIn("agent_session=s9", response.headers.get("Set-Cookie", "agent_session=s9"))
            self.assertEqual(len(transport.payloads), 1)

    def test_day_nine_rejects_an_unknown_lane(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_nine_client(store_dir)

            response = client.post("/days/9/api/chat/stream", json={"message": "Привет", "lane": "left"})

            self.assertEqual(response.status_code, 400)
            self.assertIn("дорожку", response.get_json()["error"])

    def test_day_nine_compresses_the_summary_lane_and_reports_it_in_the_stream(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_nine_client(store_dir)
            client.post("/days/9/api/policy", json={"keep_last": 2, "every": 6, "limit": None})
            # Заглушка отдаёт ответы по порядку: три хода правой дорожки, потом конспект.
            transport._replies = ["а1", "а2", "а3", "КОНСПЕКТ"]

            for text in ("в1", "в2"):
                self._chat_lane(client, "summary", text)
            events = self._events(self._chat_lane(client, "summary", "в3"))

            types = [event["type"] for event in events]
            self.assertEqual(types[-3:], ["compressing", "compressed", "done"])
            self.assertEqual(events[-2]["compression"]["reason"], "every")
            self.assertEqual(events[-2]["compression"]["compressed"], 4)
            done = events[-1]
            self.assertEqual(done["snapshot"]["summary"]["text"], "КОНСПЕКТ")
            self.assertEqual(len(done["snapshot"]["messages"]), 6)
            self.assertEqual(done["snapshot"]["context"]["messages"], 2)

            # Левая дорожка ничего не сжимает, даже на том же пороге.
            for text in ("в1", "в2"):
                self._chat_lane(client, "plain", text)
            events = self._events(self._chat_lane(client, "plain", "в3"))
            self.assertEqual([event["type"] for event in events if event["type"] != "delta"], ["done"])
            self.assertIsNone(events[-1]["snapshot"]["summary"])
            self.assertEqual(len(events[-1]["snapshot"]["messages"]), 6)

            state = client.get("/days/9/api/state").get_json()
            self.assertEqual(state["compare"]["lanes"]["summary"]["summary_requests"], 1)
            self.assertEqual(state["compare"]["lanes"]["plain"]["summary_requests"], 0)

    def test_day_nine_policy_validation(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_nine_client(store_dir)

            bad_payloads = [
                {"keep_last": 10, "every": 10, "limit": None},
                {"keep_last": -1, "every": 10, "limit": None},
                {"keep_last": 2, "every": 1, "limit": None},
                {"keep_last": 2, "every": 10, "limit": 10},
                {"keep_last": "два", "every": 10, "limit": None},
                {"keep_last": True, "every": 10, "limit": None},
            ]
            for payload in bad_payloads:
                with self.subTest(payload=payload):
                    self.assertEqual(client.post("/days/9/api/policy", json=payload).status_code, 400)

            ok = client.post("/days/9/api/policy", json={"keep_last": 2, "every": 8, "limit": 700}).get_json()
            self.assertEqual(ok["policy"], {"keep_last": 2, "every": 8, "summary_max_tokens": 800, "limit": 700})
            self.assertEqual(ok["lanes"]["plain"]["context"]["limit"], 700)
            self.assertIsNone(ok["lanes"]["plain"]["config"]["compression"])

    def test_day_nine_manual_compress(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_nine_client(store_dir)
            client.post("/days/9/api/policy", json={"keep_last": 0, "every": 20, "limit": None})

            nothing = client.post("/days/9/api/compress").get_json()
            self.assertIsNone(nothing["compression"])

            transport._replies = ["а1", "КОНСПЕКТ"]
            self._chat_lane(client, "summary", "в1")
            data = client.post("/days/9/api/compress").get_json()

            self.assertEqual(data["compression"]["reason"], "manual")
            self.assertEqual(data["state"]["lanes"]["summary"]["summary"]["text"], "КОНСПЕКТ")
            self.assertEqual(data["state"]["lanes"]["summary"]["context"]["messages"], 0)

    def test_day_nine_compares_facts_and_tokens_between_the_lanes(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_nine_client(store_dir, measuring=True)
            full = "ТЗ: Android 10, рубли, доллары и евро, свои категории, офлайн, Google, 800 тыс., три месяца, тёмная тема, CSV, уведомления, 299 руб."
            lossy = "ТЗ: Android, рубли, доллары и евро, категории, офлайн, Google, 800 тыс., три месяца, тёмная тема, CSV, уведомления."
            transport._replies = [full, lossy]

            self._chat_lane(client, "plain", "Собери ТЗ")
            self._chat_lane(client, "summary", "Собери ТЗ")
            compare = client.get("/days/9/api/state").get_json()["compare"]

            self.assertEqual(compare["lanes"]["plain"]["facts_found"], 13)
            self.assertEqual(compare["lanes"]["summary"]["facts_found"], 11)
            self.assertEqual(compare["lanes"]["summary"]["missing"], ["минимальная версия 10", "цена 299 рублей"])
            lost = [f["label"] for f in compare["facts"] if f["plain"] and not f["summary"]]
            self.assertEqual(lost, ["минимальная версия 10", "цена 299 рублей"])
            # Короткий ответ дешевле длинного — экономия положительная, в токенах и процентах.
            self.assertGreater(compare["saved_tokens"], 0)
            self.assertIsInstance(compare["saved_percent"], int)
            self.assertTrue(compare["same_turns"])

    def test_day_nine_reset_clears_both_lanes_but_keeps_the_policy(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_nine_client(store_dir)
            client.post("/days/9/api/policy", json={"keep_last": 2, "every": 8, "limit": 500})
            self._chat_lane(client, "plain", "в1")
            self._chat_lane(client, "summary", "в1")

            state = client.post("/days/9/api/reset").get_json()

            self.assertEqual(state["lanes"]["plain"]["messages"], [])
            self.assertEqual(state["lanes"]["summary"]["messages"], [])
            self.assertEqual(state["policy"], {"keep_last": 2, "every": 8, "summary_max_tokens": 800, "limit": 500})
            self.assertEqual(state["lanes"]["plain"]["context"]["limit"], 500)
            self.assertEqual(state["lanes"]["summary"]["storage"]["messages"], 0)
            self.assertTrue(Path(store_dir, "s9-summary.json").exists())

    def test_day_nine_summary_survives_a_restart(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_nine_client(store_dir)
            client.post("/days/9/api/policy", json={"keep_last": 0, "every": 20, "limit": None})
            transport._replies = ["а1", "КОНСПЕКТ"]
            self._chat_lane(client, "summary", "в1")
            client.post("/days/9/api/compress")

            from tests.test_agent import FakeTransport

            second = create_app({"TESTING": True, "AGENT_STORE_DIR": store_dir, "AGENT_TRANSPORT": FakeTransport(replies=["а2"])})
            second_client = second.test_client()
            second_client.set_cookie("agent_session", "s9")
            # tojson экранирует кириллицу в разметке — проверяем по JSON состояния.
            self.assertEqual(second_client.get("/days/9/").status_code, 200)
            state = second_client.get("/days/9/api/state").get_json()
            self.assertEqual(state["lanes"]["summary"]["summary"]["text"], "КОНСПЕКТ")
            self.assertEqual(state["lanes"]["summary"]["compressions"][0]["reason"], "manual")

            self._chat_lane(second_client, "summary", "в2")
            sent = second.config["AGENT_TRANSPORT"].payloads[0]["messages"]
            self.assertEqual([m["role"] for m in sent], ["system", "user"])
            self.assertIn("КОНСПЕКТ", sent[0]["content"])

    def test_day_nine_serves_the_scenario_with_its_facts(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_nine_client(store_dir)

            scenario = client.get("/days/9/api/scenario").get_json()

            self.assertEqual(len(scenario["messages"]), 12)
            self.assertEqual(len(scenario["facts"]), 13)
            self.assertEqual(scenario["facts"][-1]["label"], "цена 299 рублей")

            verbose = client.get("/days/9/api/scenario?key=spec-verbose").get_json()
            self.assertEqual(verbose["messages"][1:], scenario["messages"][1:])
            self.assertIn("развёрнуто", verbose["messages"][0])
            self.assertEqual(verbose["facts"], scenario["facts"])
            self.assertEqual(client.get("/days/9/api/scenario?key=nope").status_code, 404)

    @staticmethod
    def _chat_lane(client, lane, message):
        response = client.post("/days/9/api/chat/stream", json={"message": message, "lane": lane})
        response.get_data()
        return response

    def _day_ten_client(self, store_dir, transport=None):
        from tests.test_agent import FakeTransport

        transport = transport or FakeTransport()
        app = create_app({"TESTING": True, "AGENT_STORE_DIR": store_dir, "AGENT_TRANSPORT": transport})
        client = app.test_client()
        client.set_cookie("agent_session", "s10")
        return client, transport

    @staticmethod
    def _chat10(client, message):
        response = client.post("/days/10/api/chat/stream", json={"message": message})
        response.get_data()
        return response

    def test_day_ten_switches_the_strategy_on_a_running_dialogue(self):
        import json as json_module
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_ten_client(store_dir)
            transport._replies = ["а1", "а2", "а3"]
            self._chat10(client, "в1")
            self._chat10(client, "в2")

            state = client.post("/days/10/api/strategy", json={"kind": "window", "n": 2}).get_json()
            self.assertEqual(state["strategy"], {"kind": "window", "title": "Sliding Window", "n": 2})
            self.assertEqual(len(state["branch"]["messages"]), 4)
            self.assertEqual(state["branch"]["context"]["messages"], 2)
            self.assertEqual(state["branch"]["context"]["dropped"], 2)

            done = [json_module.loads(l) for l in self._chat10(client, "в3").get_data(as_text=True).splitlines()][-1]
            sent = transport.payloads[-1]["messages"]
            self.assertEqual([m["content"] for m in sent if m["role"] != "system"], ["в2", "а2", "в3"])
            self.assertEqual(done["snapshot"]["strategy"], "window")

            back = client.post("/days/10/api/strategy", json={"kind": "full", "n": 2}).get_json()
            self.assertEqual(back["strategy"]["kind"], "full")
            self.assertEqual(back["branch"]["context"]["dropped"], 0)

    def test_day_ten_rejects_a_bad_strategy(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_ten_client(store_dir)

            for payload in ({"kind": "summary", "n": 4}, {"kind": "window", "n": 0}, {"kind": "facts", "n": "четыре"}, ["window"]):
                with self.subTest(payload=payload):
                    self.assertEqual(client.post("/days/10/api/strategy", json=payload).status_code, 400)

    def test_day_ten_streams_facts_events_and_keeps_the_block_in_the_state(self):
        import json as json_module
        import tempfile

        from tests.test_agent import FactsJsonTransport

        with tempfile.TemporaryDirectory() as store_dir:
            transport = FactsJsonTransport(replies=["а1"], facts=['{"facts": {"платформа": "Android 10"}}'])
            client, _ = self._day_ten_client(store_dir, transport)
            client.post("/days/10/api/strategy", json={"kind": "facts", "n": 2})

            events = [json_module.loads(l) for l in self._chat10(client, "Платформа — Android 10").get_data(as_text=True).splitlines()]

            types = [e["type"] for e in events if e["type"] != "delta"]
            self.assertEqual(types, ["facts_updating", "facts_updated", "done"])
            self.assertEqual(events[-2]["update"]["added"], ["платформа"])
            self.assertEqual(events[-1]["snapshot"]["facts"], {"платформа": "Android 10"})
            state = client.get("/days/10/api/state").get_json()
            self.assertEqual(state["branch"]["facts"], {"платформа": "Android 10"})
            self.assertEqual(state["branch"]["usage"]["requests"], 2)

    def test_day_ten_checkpoint_fork_and_switch(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_ten_client(store_dir)
            transport._replies = ["а1", "а2", "ветка-A", "ветка-B"]
            self._chat10(client, "в1")
            self._chat10(client, "в2")

            point = client.post("/days/10/api/checkpoint", json={"title": "после двух"}).get_json()["checkpoint"]
            self.assertEqual((point["id"], point["message"], point["title"]), ("c1", 4, "после двух"))

            forked = client.post("/days/10/api/fork", json={"checkpoint": "c1", "title": "A"}).get_json()
            self.assertEqual(forked["branch"]["id"], "b1")
            self.assertEqual(forked["state"]["tree"]["active"], "b1")
            self.assertEqual(len(forked["state"]["branch"]["messages"]), 4)
            self.assertTrue(Path(store_dir, "s10-b1.json").exists())
            self._chat10(client, "в-A")

            forked_b = client.post("/days/10/api/fork", json={"checkpoint": "c1", "title": "B"}).get_json()
            self.assertEqual(forked_b["state"]["tree"]["active"], "b2")
            self._chat10(client, "в-B")

            state = client.get("/days/10/api/state").get_json()
            self.assertEqual([m["content"] for m in state["branch"]["messages"]][-2:], ["в-B", "ветка-B"])
            sizes = {b["id"]: b["messages"] for b in state["tree"]["branches"]}
            self.assertEqual(sizes, {"main": 4, "b1": 6, "b2": 6})

            switched = client.post("/days/10/api/switch", json={"branch": "b1"}).get_json()
            self.assertEqual(switched["tree"]["active"], "b1")
            self.assertEqual(switched["branch"]["messages"][-1]["content"], "ветка-A")
            self.assertEqual(client.post("/days/10/api/switch", json={"branch": "b9"}).status_code, 400)
            self.assertEqual(client.post("/days/10/api/fork", json={"checkpoint": "c9"}).status_code, 400)

    def test_day_ten_records_runs_and_reset_keeps_them(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_ten_client(store_dir)
            self.assertEqual(client.post("/days/10/api/runs", json={}).status_code, 400)
            transport._replies = ["ТЗ: Android 10, рубли, доллары и евро, категории, офлайн, Google, 800 тыс., три месяца, тёмная тема, CSV, уведомления, 299 руб."]
            client.post("/days/10/api/strategy", json={"kind": "window", "n": 3})
            self._chat10(client, "Собери ТЗ")
            client.post("/days/10/api/checkpoint", json={})

            run = client.post("/days/10/api/runs", json={"scenario": "spec"}).get_json()["run"]
            self.assertEqual((run["strategy"], run["n"], run["turns"], run["facts_found"], run["facts_total"]), ("window", 3, 1, 13, 13))
            self.assertEqual(run["missing"], [])

            state = client.post("/days/10/api/reset").get_json()
            self.assertEqual(state["branch"]["messages"], [])
            self.assertEqual(state["strategy"]["kind"], "window")
            self.assertEqual(len(state["tree"]["runs"]), 1)
            self.assertEqual(state["tree"]["checkpoints"], [])
            self.assertTrue(Path(store_dir, "s10-tree.json").exists())

            cleared = client.delete("/days/10/api/runs").get_json()
            self.assertEqual(cleared["tree"]["runs"], [])

    def test_day_ten_serves_both_scenarios(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_ten_client(store_dir)

            spec = client.get("/days/10/api/scenario?key=spec").get_json()
            branches = client.get("/days/10/api/scenario?key=spec-branches").get_json()

            self.assertEqual(len(spec["messages"]), 12)
            self.assertEqual(len(branches["trunk"]), 10)
            self.assertEqual([b["title"][:1] for b in branches["branches"]], ["A", "B"])
            self.assertIn("299", branches["branches"][0]["messages"][0])
            self.assertEqual(client.get("/days/10/api/scenario?key=nope").status_code, 404)

    def test_day_ten_state_survives_a_restart(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_ten_client(store_dir)
            transport._replies = ["а1", "а2"]
            client.post("/days/10/api/strategy", json={"kind": "window", "n": 2})
            self._chat10(client, "в1")
            client.post("/days/10/api/checkpoint", json={})
            client.post("/days/10/api/fork", json={"checkpoint": "c1", "title": "A"})
            self._chat10(client, "в2")

            from tests.test_agent import FakeTransport

            second = create_app({"TESTING": True, "AGENT_STORE_DIR": store_dir, "AGENT_TRANSPORT": FakeTransport()}).test_client()
            second.set_cookie("agent_session", "s10")
            state = second.get("/days/10/api/state").get_json()

            self.assertEqual(state["tree"]["active"], "b1")
            self.assertEqual(state["strategy"]["kind"], "window")
            self.assertEqual(len(state["branch"]["messages"]), 4)
            self.assertEqual([b["messages"] for b in state["tree"]["branches"]], [2, 4])

    def test_day_eleven_renders_the_memory_model_homework(self):
        response = self.client.get("/days/11/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("День 11.", html)
        self.assertIn("Модель памяти агента", html)
        self.assertIn("Домашнее задание", html)
        self.assertIn("Опишите и реализуйте модель памяти для агента", html)
        self.assertIn("краткосрочная", html)
        self.assertIn("рабочая", html)
        self.assertIn("долговременная", html)
        self.assertIn("хранились отдельно", html)
        self.assertIn("явно выбирали, что и куда сохраняется", html)
        self.assertIn("какие данные попадают в каждый слой", html)
        self.assertIn("как это влияет на ответы агента", html)
        self.assertIn("Агент с явной моделью памяти (memory layers)", html)
        self.assertIn("chat scope", html)
        self.assertIn("task scope", html)
        self.assertIn("user scope", html)
        self.assertIn("css/day01.css", html)
        self.assertIn("css/homework.css", html)
        self.assertIn("css/day11.css", html)
        self.assertIn('href="/days/11/"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('class="homework-divider"', html)

    # ── День 11: три слоя памяти ────────────────────────────────────────

    def _day_eleven_client(self, store_dir, routed=None, replies=None):
        from tests.test_agent import FakeTransport

        class RoutingTransport(FakeTransport):
            """На запрос нарезателя отдаёт JSON, на ходы — обычный текст."""

            def __init__(self, replies=None, routed=None):
                super().__init__(replies=replies)
                self._routed = list(routed or [])

            def send(self, payload):
                if payload.get("response_format") == {"type": "json_object"}:
                    self.payloads.append(payload)
                    content = self._routed.pop(0) if self._routed else '{"chat": {}, "task": {}, "user": {}}'
                    return {
                        "model": "deepseek-v4-flash",
                        "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                        "usage": {"prompt_tokens": 20, "completion_tokens": 8},
                    }
                return super().send(payload)

        transport = RoutingTransport(replies=replies, routed=routed)
        app = create_app({"TESTING": True, "AGENT_STORE_DIR": store_dir, "AGENT_TRANSPORT": transport})
        client = app.test_client()
        client.set_cookie("agent_session", "s11")
        return client, transport

    @staticmethod
    def _chat11(client, message):
        response = client.post("/days/11/api/chat/stream", json={"message": message})
        response.get_data()
        return response

    @staticmethod
    def _layer(state, scope):
        return next(layer for layer in state["memory"]["layers"] if layer["scope"] == scope)

    def test_day_eleven_router_splits_a_reply_into_three_layers(self):
        import json as json_module
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            routed = ['{"chat": {"now": "выбираем тему"}, "task": {"budget": "800 тысяч"}, "user": {"name": "Максим"}}']
            client, transport = self._day_eleven_client(store_dir, routed=routed, replies=["ответ"])

            events = [
                json_module.loads(line)
                for line in self._chat11(client, "Меня зовут Максим, бюджет 800 тысяч").get_data(as_text=True).splitlines()
            ]
            kinds = [event["type"] for event in events]
            self.assertIn("memory_routing", kinds)
            self.assertIn("memory_routed", kinds)

            state = client.get("/days/11/api/state").get_json()
            self.assertEqual([item["key"] for item in self._layer(state, "user")["items"]], ["name"])
            self.assertEqual([item["key"] for item in self._layer(state, "task")["items"]], ["budget"])
            self.assertEqual([item["key"] for item in self._layer(state, "chat")["items"]], ["now"])

            # Три слоя — три файла: это и есть «хранятся отдельно».
            files = sorted(name for name in os.listdir(store_dir) if "-mem-" in name)
            self.assertEqual(
                files,
                ["s11-mem-chat.json", "s11-mem-settings.json", "s11-mem-task.json", "s11-mem-user.json"],
            )
            self.assertEqual(state["side"]["requests"], 1)
            self.assertEqual(state["side"]["input_tokens"], 20)

    def test_day_eleven_puts_enabled_layers_into_the_system_prompt(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            routed = ['{"chat": {}, "task": {"budget": "800 тысяч"}, "user": {"name": "Максим"}}']
            client, transport = self._day_eleven_client(store_dir, routed=routed, replies=["а1", "а2"])
            self._chat11(client, "в1")
            self._chat11(client, "в2")

            turns = [item for item in transport.payloads if "response_format" not in item]
            system = next(m for m in turns[-1]["messages"] if m["role"] == "system")["content"]
            self.assertIn("три раздельные памяти", system)
            self.assertIn("Рабочая память (текущая задача):", system)
            self.assertIn("budget: 800 тысяч", system)
            self.assertIn("Долговременная память", system)
            self.assertIn("name: Максим", system)

    def test_day_eleven_disabled_layer_leaves_the_prompt_but_stays_on_disk(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            routed = ['{"chat": {}, "task": {"budget": "800 тысяч"}, "user": {"name": "Максим"}}']
            client, transport = self._day_eleven_client(store_dir, routed=routed, replies=["а1", "а2"])
            self._chat11(client, "в1")

            state = client.post("/days/11/api/layers", json={"enabled": ["chat", "user"]}).get_json()
            self.assertEqual(state["enabled"], ["chat", "user"])
            self.assertFalse(self._layer(state, "task")["enabled"])
            # Выключили — но не стёрли: запись на месте.
            self.assertEqual([item["key"] for item in self._layer(state, "task")["items"]], ["budget"])

            self._chat11(client, "в2")
            turns = [item for item in transport.payloads if "response_format" not in item]
            system = next(m for m in turns[-1]["messages"] if m["role"] == "system")["content"]
            self.assertNotIn("Рабочая память", system)
            self.assertIn("Долговременная память", system)

    def test_day_eleven_new_dialogue_kills_only_the_chat_layer(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            routed = ['{"chat": {"now": "думаем"}, "task": {"budget": "800"}, "user": {"name": "Максим"}}']
            client, _ = self._day_eleven_client(store_dir, routed=routed, replies=["а1"])
            self._chat11(client, "в1")

            state = client.post("/days/11/api/reset", json={}).get_json()
            self.assertEqual(state["messages"], [])
            self.assertEqual(self._layer(state, "chat")["items"], [])
            self.assertEqual([item["key"] for item in self._layer(state, "task")["items"]], ["budget"])
            self.assertEqual([item["key"] for item in self._layer(state, "user")["items"]], ["name"])

    def test_day_eleven_new_task_kills_only_the_task_layer(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            routed = ['{"chat": {"now": "думаем"}, "task": {"budget": "800"}, "user": {"name": "Максим"}}']
            client, _ = self._day_eleven_client(store_dir, routed=routed, replies=["а1"])
            self._chat11(client, "в1")

            state = client.post("/days/11/api/task/new", json={}).get_json()
            self.assertEqual(self._layer(state, "task")["items"], [])
            self.assertEqual([item["key"] for item in self._layer(state, "chat")["items"]], ["now"])
            self.assertEqual([item["key"] for item in self._layer(state, "user")["items"]], ["name"])
            self.assertEqual(len(state["messages"]), 2)

    def test_day_eleven_manual_mode_puts_moves_and_forgets_by_hand(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_eleven_client(store_dir, replies=["а1"])
            client.post("/days/11/api/mode", json={"mode": "manual"})

            state = client.post("/days/11/api/memory", json={"scope": "task", "key": "stack", "value": "Flask"}).get_json()
            self.assertEqual(self._layer(state, "task")["items"][0]["source"], "manual")

            state = client.post("/days/11/api/memory/move", json={"from": "task", "to": "user", "key": "stack"}).get_json()
            self.assertEqual(self._layer(state, "task")["items"], [])
            self.assertEqual(self._layer(state, "user")["items"][0]["value"], "Flask")

            # В ручном режиме ход не тратит запрос на раскладку.
            self._chat11(client, "в1")
            self.assertEqual(client.get("/days/11/api/state").get_json()["side"]["requests"], 0)

            state = client.delete("/days/11/api/memory", json={"scope": "user", "key": "stack"}).get_json()
            self.assertEqual(self._layer(state, "user")["items"], [])

    def test_day_eleven_heuristic_mode_sorts_without_a_request(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_eleven_client(store_dir, replies=["а1", "а2"])
            client.post("/days/11/api/mode", json={"mode": "heuristic"})

            self._chat11(client, "Меня зовут Максим")
            self._chat11(client, "Бюджет 800 тысяч рублей")

            state = client.get("/days/11/api/state").get_json()
            self.assertEqual(len(self._layer(state, "user")["items"]), 1)
            self.assertEqual(len(self._layer(state, "task")["items"]), 1)
            # Ни одного побочного запроса: эвристика бесплатна.
            self.assertEqual(state["side"]["requests"], 0)
            self.assertEqual(state["side"]["input_tokens"], 0)

    def test_day_eleven_keeps_the_answer_when_the_router_fails(self):
        import json as json_module
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_eleven_client(store_dir, routed=["не json"], replies=["ответ дошёл"])

            events = [
                json_module.loads(line)
                for line in self._chat11(client, "в1").get_data(as_text=True).splitlines()
            ]
            failed = next(event for event in events if event["type"] == "memory_failed")
            self.assertIn("не JSON", failed["error"])
            done = events[-1]
            self.assertEqual(done["type"], "done")
            self.assertEqual(done["reply"]["content"], "ответ дошёл")
            self.assertEqual(len(done["snapshot"]["messages"]), 2)

    def test_day_eleven_serves_the_scenario_with_both_checks(self):
        response = self.client.get("/days/11/api/scenario")
        scenario = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(scenario["key"], "layers")
        self.assertEqual(len(scenario["messages"]), 7)
        self.assertEqual(len(scenario["after_reset"]), 1)
        self.assertTrue(any("Максим" in message for message in scenario["messages"]))
        self.assertEqual(len(scenario["user_facts"]), 4)
        self.assertEqual(len(scenario["task_facts"]), 5)

    def test_day_eleven_picks_the_scope_with_its_own_listbox(self):
        """Нативный <option> не стилизуется — список свой, значение в скрытом поле."""
        html = self.client.get("/days/11/").get_data(as_text=True)

        self.assertNotIn("<select", html)
        self.assertIn('role="listbox"', html)
        self.assertEqual(html.count('role="option"'), 3)
        self.assertIn('<input type="hidden" name="scope" value="chat">', html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn('data-value="user"', html)

    def test_day_eleven_validates_mode_layers_and_scope(self):
        for url, payload, message in (
            ("/days/11/api/mode", {"mode": "магия"}, "Режим раскладки"),
            ("/days/11/api/layers", {"enabled": ["chat", "нет"]}, "Ожидался список слоёв"),
            ("/days/11/api/memory", {"scope": "нет", "key": "k", "value": "v"}, "Укажите слой"),
            ("/days/11/api/memory", {"scope": "task", "key": " ", "value": "v"}, "Укажите ключ"),
            ("/days/11/api/memory/clear", {"scope": "нет"}, "Укажите слой"),
            ("/days/11/api/memory/move", {"from": "task", "to": "task", "key": "k"}, "разные слои"),
        ):
            with self.subTest(url=url):
                response = self.client.post(url, json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.get_json()["error"])

    def test_day_twelve_renders_the_personalization_homework(self):
        response = self.client.get("/days/12/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("День 12.", html)
        self.assertIn("Персонализация ассистента", html)
        self.assertIn("Домашнее задание", html)
        self.assertIn("Добавьте персонализацию поверх модели памяти", html)
        self.assertIn("Создайте профиль пользователя", html)
        self.assertIn("стиль, формат, ограничения", html)
        self.assertIn("Подключите профиль к каждому запросу", html)
        self.assertIn("ответы для разных профилей", html)
        self.assertIn("учитывает автоматически", html)
        self.assertIn("Персонализированный агент, адаптированный под пользователя", html)
        self.assertIn("Пайплайн", html)
        self.assertEqual(html.count('class="day12-lane"'), 2)
        self.assertIn('role="listbox"', html)
        self.assertIn("css/day01.css", html)
        self.assertIn("css/homework.css", html)
        self.assertIn("css/day12.css", html)
        self.assertIn('href="/days/12/"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('class="homework-divider"', html)

    # ── День 12: профиль поверх памяти ──────────────────────────────────

    def _day_twelve_client(self, store_dir, replies=None):
        from tests.test_agent import FakeTransport

        transport = FakeTransport(replies=replies)
        app = create_app({"TESTING": True, "AGENT_STORE_DIR": store_dir, "AGENT_TRANSPORT": transport})
        client = app.test_client()
        client.set_cookie("agent_session", "s12")
        return client, transport

    @staticmethod
    def _chat12(client, message, lane):
        response = client.post("/days/12/api/chat/stream", json={"message": message, "lane": lane})
        response.get_data()
        return response

    @staticmethod
    def _system_of(transport):
        turns = [item for item in transport.payloads if "response_format" not in item]
        return next(m for m in turns[-1]["messages"] if m["role"] == "system")["content"]

    def test_day_twelve_starts_with_two_different_presets_on_the_lanes(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_twelve_client(store_dir)
            state = client.get("/days/12/api/state").get_json()

            self.assertEqual([p["id"] for p in state["profiles"]], ["maksim", "customer", "reviewer"])
            self.assertEqual(state["lanes"]["a"]["profile"], "maksim")
            self.assertEqual(state["lanes"]["b"]["profile"], "customer")
            self.assertIn("Профиль пользователя «Максим · коротко»", state["lanes"]["a"]["system_text"])
            self.assertIn("Профиль пользователя «Заказчик · на вы»", state["lanes"]["b"]["system_text"])

    def test_day_twelve_puts_the_profile_into_every_request(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_twelve_client(store_dir, replies=["а1", "а2"])
            self._chat12(client, "в1", "a")
            self.assertIn("на ты, по имени — Максим", self._system_of(transport))
            self.assertIn("Порядок работы над задачей", self._system_of(transport))

            self._chat12(client, "в1", "b")
            self.assertIn("на вы, без имени", self._system_of(transport))
            self.assertNotIn("Максим", self._system_of(transport))

    def test_day_twelve_profile_sits_on_top_of_day_eleven_memory(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            from advent.agent import MemoryStore

            memory_store = MemoryStore(store_dir)
            memory = memory_store.load("s12")
            memory["user"].put("stack", "Flask")
            memory_store.save("s12", memory)

            client, transport = self._day_twelve_client(store_dir, replies=["а1"])
            self._chat12(client, "в1", "a")
            system = self._system_of(transport)

            # Сначала профиль (как работать), потом память (что знаем).
            self.assertLess(system.index("Профиль пользователя"), system.index("Долговременная память"))
            self.assertIn("stack: Flask", system)
            state = client.get("/days/12/api/state").get_json()
            self.assertEqual(state["memory"]["counts"]["user"], 1)

    def test_day_twelve_rebinds_a_lane_without_losing_its_dialogue(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_twelve_client(store_dir, replies=["а1", "а2"])
            self._chat12(client, "в1", "a")

            state = client.post("/days/12/api/lane", json={"lane": "a", "profile": "reviewer"}).get_json()
            self.assertEqual(state["lanes"]["a"]["profile"], "reviewer")
            self.assertEqual(len(state["lanes"]["a"]["messages"]), 2)

            self._chat12(client, "в2", "a")
            self.assertIn("Ревьюер · строго", self._system_of(transport))

            missing = client.post("/days/12/api/lane", json={"lane": "a", "profile": "нет"})
            self.assertEqual(missing.status_code, 404)

    def test_day_twelve_saves_a_custom_profile_and_protects_presets(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_twelve_client(store_dir, replies=["а1"])
            payload = {
                "id": "mine",
                "title": "Мой",
                "address": "на ты, по имени — Макс",
                "tone": "сухо",
                "format": "таблицей",
                "limits": ["шутить"],
                "pipeline": ["план", "код"],
            }
            state = client.post("/days/12/api/profile", json=payload).get_json()
            mine = next(p for p in state["profiles"] if p["id"] == "mine")
            self.assertFalse(mine["preset"])
            self.assertEqual(mine["pipeline"], ["план", "код"])

            client.post("/days/12/api/lane", json={"lane": "b", "profile": "mine"})
            self._chat12(client, "в1", "b")
            self.assertIn("Профиль пользователя «Мой»", self._system_of(transport))
            self.assertIn("1) план; 2) код", self._system_of(transport))

            clash = client.post("/days/12/api/profile", json={**payload, "id": "maksim"})
            self.assertEqual(clash.status_code, 400)
            empty = client.post("/days/12/api/profile", json={**payload, "tone": " "})
            self.assertEqual(empty.status_code, 400)

            protected = client.delete("/days/12/api/profile", json={"id": "maksim"})
            self.assertEqual(protected.status_code, 400)
            state = client.delete("/days/12/api/profile", json={"id": "mine"}).get_json()
            self.assertNotIn("mine", [p["id"] for p in state["profiles"]])
            # Дорожка со снесённым профилем откатилась к встроенному.
            self.assertEqual(state["lanes"]["b"]["profile"], "customer")

    def test_day_twelve_reports_what_the_answer_shows(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_twelve_client(
                store_dir,
                replies=["Максим, тебе нужно:\n- раз\n- два\n```py\nx = 1\n```", "Вам стоит подумать.\n\nВо-первых…"],
            )
            self._chat12(client, "в1", "a")
            self._chat12(client, "в1", "b")
            state = client.get("/days/12/api/state").get_json()

            a = state["lanes"]["a"]["check"]
            self.assertEqual((a["address"], a["list_items"], a["has_code"], a["named"]), ("ты", 2, True, True))
            b = state["lanes"]["b"]["check"]
            self.assertEqual((b["address"], b["list_items"], b["has_code"], b["paragraphs"]), ("вы", 0, False, 2))

    def test_day_twelve_reset_clears_both_lanes_but_keeps_profiles(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_twelve_client(store_dir, replies=["а1", "а2"])
            client.post("/days/12/api/lane", json={"lane": "a", "profile": "reviewer"})
            self._chat12(client, "в1", "a")
            self._chat12(client, "в1", "b")

            state = client.post("/days/12/api/reset", json={}).get_json()
            self.assertEqual(state["lanes"]["a"]["messages"], [])
            self.assertEqual(state["lanes"]["b"]["messages"], [])
            self.assertEqual(state["lanes"]["a"]["profile"], "reviewer")

    def test_day_twelve_serves_the_scenario_and_validates_the_lane(self):
        response = self.client.get("/days/12/api/scenario")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["messages"]), 3)

        bad = self.client.post("/days/12/api/chat/stream", json={"message": "в", "lane": "c"})
        self.assertEqual(bad.status_code, 400)

    def test_day_thirteen_renders_the_task_state_homework(self):
        response = self.client.get("/days/13/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("День 13.", html)
        self.assertIn("Состояние задачи", html)
        self.assertIn("Домашнее задание", html)
        self.assertIn("конечный автомат", html)
        self.assertIn("planning → execution → validation → done", html)
        self.assertIn("паузу на любом этапе", html)
        self.assertIn("продолжение без повторных объяснений", html)
        self.assertIn("Агент с формализованным состоянием задачи", html)
        self.assertEqual(html.count('class="day13-stage"'), 4)
        self.assertIn('data-stepper', html)
        self.assertIn('data-action="approve"', html)
        self.assertIn("css/day01.css", html)
        self.assertIn("css/homework.css", html)
        self.assertIn("css/day13.css", html)
        self.assertIn('href="/days/13/"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('class="homework-divider"', html)

    # ── День 13: автомат задачи ─────────────────────────────────────────

    def _day_thirteen_client(self, store_dir, reports=None, replies=None):
        from tests.test_agent import FakeTransport

        class ObservingTransport(FakeTransport):
            """На запрос наблюдателя отдаёт JSON, на ходы — обычный текст."""

            def __init__(self, replies=None, reports=None):
                super().__init__(replies=replies)
                self._reports = list(reports or [])

            def send(self, payload):
                if payload.get("response_format") == {"type": "json_object"}:
                    self.payloads.append(payload)
                    content = self._reports.pop(0) if self._reports else '{"plan": [], "plan_ready": false}'
                    return {
                        "model": "deepseek-v4-flash",
                        "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                        "usage": {"prompt_tokens": 30, "completion_tokens": 9},
                    }
                return super().send(payload)

        transport = ObservingTransport(replies=replies, reports=reports)
        app = create_app({"TESTING": True, "AGENT_STORE_DIR": store_dir, "AGENT_TRANSPORT": transport})
        client = app.test_client()
        client.set_cookie("agent_session", "s13")
        return client, transport

    @staticmethod
    def _chat13(client, message):
        import json as json_module

        response = client.post("/days/13/api/chat/stream", json={"message": message})
        return [json_module.loads(line) for line in response.get_data(as_text=True).splitlines()]

    def test_day_thirteen_first_message_creates_the_task_and_the_observer_extracts_a_plan(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_thirteen_client(
                store_dir, reports=['{"plan": ["прочитать CSV", "сгруппировать"], "plan_ready": true}'], replies=["план такой"]
            )
            events = self._chat13(client, "Нужна утилита для расходов. Составь план.")
            kinds = [e["type"] for e in events]

            self.assertIn("task_created", kinds)
            self.assertIn("task_updated", kinds)
            done = events[-1]
            task = done["snapshot"]["task"]
            self.assertEqual(task["stage"], "planning")
            self.assertEqual([s["text"] for s in task["steps"]], ["прочитать CSV", "сгруппировать"])
            self.assertEqual(task["expected"], "утвердить план")
            self.assertIn("s13-task.json", os.listdir(store_dir))
            # Задача уходит в системный промпт уже на первом ходу.
            turns = [item for item in transport.payloads if "response_format" not in item]
            system = next(m for m in turns[-1]["messages"] if m["role"] == "system")["content"]
            self.assertIn("Текущая задача:", system)
            self.assertIn("Этап: planning", system)

    def test_day_thirteen_buttons_move_the_machine_and_illegal_moves_are_refused(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_thirteen_client(store_dir, reports=['{"plan": ["a", "b"], "plan_ready": true}'], replies=["ок"])
            self.assertEqual(client.post("/days/13/api/task/approve", json={}).status_code, 404)  # задачи нет

            self._chat13(client, "цель")
            self.assertEqual(client.post("/days/13/api/task/step", json={}).status_code, 409)  # ещё planning

            state = client.post("/days/13/api/task/approve", json={}).get_json()
            self.assertEqual(state["task"]["stage"], "execution")
            state = client.post("/days/13/api/task/step", json={}).get_json()
            self.assertEqual(state["task"]["current"], 1)
            state = client.post("/days/13/api/task/step", json={}).get_json()
            self.assertEqual(state["task"]["stage"], "validation")
            state = client.post("/days/13/api/task/verdict", json={"ok": False, "step": 1}).get_json()
            self.assertEqual((state["task"]["stage"], state["task"]["current"]), ("execution", 0))
            self.assertEqual(client.post("/days/13/api/task/verdict", json={"ok": "да"}).status_code, 400)

    def test_day_thirteen_pause_survives_reset_and_the_agent_resumes_from_the_step(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_thirteen_client(
                store_dir,
                reports=['{"plan": ["a", "b", "c"], "plan_ready": true}', '{"step_done": true, "note": "готово"}', '{"step_done": true, "note": "ок"}'],
                replies=["план", "шаг 1 готов", "шаг 2 готов"],
            )
            self._chat13(client, "цель")
            client.post("/days/13/api/task/approve", json={})
            self._chat13(client, "делай")
            state = client.post("/days/13/api/task/pause", json={}).get_json()
            self.assertTrue(state["task"]["paused"])
            self.assertEqual(state["task"]["current"], 1)

            state = client.post("/days/13/api/reset", json={}).get_json()
            self.assertEqual(state["messages"], [])
            self.assertEqual((state["task"]["stage"], state["task"]["current"], state["task"]["paused"]), ("execution", 1, True))

            # На паузе наблюдатель не зовётся — ход есть, автомат стоит.
            events = self._chat13(client, "что там?")
            self.assertNotIn("task_observing", [e["type"] for e in events])

            client.post("/days/13/api/task/resume", json={})
            events = self._chat13(client, "Продолжаем.")
            turns = [item for item in transport.payloads if "response_format" not in item]
            system = next(m for m in turns[-1]["messages"] if m["role"] == "system")["content"]
            self.assertIn("Текущий шаг: 2 из 3 — b", system)
            self.assertIn("Не пересказывай задачу", system)
            self.assertEqual(events[-1]["snapshot"]["task"]["current"], 2)
            self.assertFalse(events[-1]["snapshot"]["retelling"]["retold"])

    def test_day_thirteen_observer_failure_keeps_the_answer_and_the_state(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_thirteen_client(store_dir, reports=["не json"], replies=["ответ дошёл"])
            events = self._chat13(client, "цель")

            self.assertIn("task_failed", [e["type"] for e in events])
            done = events[-1]
            self.assertEqual(done["reply"]["content"], "ответ дошёл")
            self.assertEqual(done["snapshot"]["task"]["stage"], "planning")
            self.assertEqual(done["snapshot"]["side"]["requests"], 0)

    def test_day_thirteen_new_task_drops_the_file_and_the_next_message_starts_over(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_thirteen_client(store_dir, reports=['{"plan": ["a"], "plan_ready": true}'], replies=["п", "п2"])
            self._chat13(client, "первая")
            state = client.post("/days/13/api/task/new", json={}).get_json()
            self.assertIsNone(state["task"])
            self.assertFalse(state["task_file"]["exists"])
            events = self._chat13(client, "вторая задача")
            self.assertEqual(events[-1]["snapshot"]["task"]["goal"], "вторая задача")

    def test_day_thirteen_serves_the_scenario_with_actions(self):
        response = self.client.get("/days/13/api/scenario")
        scenario = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual([s["kind"] for s in scenario["steps"]], ["say", "approve", "say", "say", "pause", "reset", "resume", "say"])

    def test_reset_all_wipes_every_session_file_and_the_registries(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_eleven_client(
                store_dir, routed=['{"chat": {}, "task": {}, "user": {"name": "Максим"}}'], replies=["а1", "а2"]
            )
            self._chat11(client, "в1")
            client.post("/days/12/api/lane", json={"lane": "a", "profile": "reviewer"})
            self.assertGreater(len(os.listdir(store_dir)), 3)

            response = client.post("/api/reset-all")
            data = response.get_json()

            self.assertEqual(response.status_code, 200)
            self.assertGreaterEqual(data["files"], 4)
            self.assertEqual(os.listdir(store_dir), [])
            state = client.get("/days/11/api/state").get_json()
            self.assertEqual(state["messages"], [])
            self.assertEqual([layer["items"] for layer in state["memory"]["layers"]], [[], [], []])
            self.assertEqual(client.get("/days/12/api/state").get_json()["lanes"]["a"]["profile"], "maksim")

    def test_every_page_has_the_reset_all_button(self):
        html = self.client.get("/days/13/").get_data(as_text=True)
        self.assertIn('id="reset-all"', html)
        self.assertIn('data-reset-all-url="/api/reset-all"', html)
        self.assertIn("js/shell.js", html)

    def test_day_fourteen_renders_the_invariants_homework(self):
        response = self.client.get("/days/14/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("День 14.", html)
        self.assertIn("Инварианты и ограничения", html)
        self.assertIn("Домашнее задание", html)
        self.assertIn("не имеет права нарушать", html)
        self.assertIn("хранятся отдельно от диалога", html)
        self.assertIn("явно учитывает их в рассуждениях", html)
        self.assertIn("отказывается предлагать решения", html)
        self.assertIn("конфликте запроса и инварианта", html)
        self.assertIn("как ассистент объясняет отказ", html)
        self.assertIn("Ассистент, который работает в рамках заданных инвариантов", html)
        self.assertEqual(html.count('class="day14-kind"'), 4)
        self.assertIn('data-invariants', html)
        self.assertIn('data-checks', html)
        self.assertIn("css/day01.css", html)
        self.assertIn("css/homework.css", html)
        self.assertIn("css/day14.css", html)
        self.assertIn('href="/days/14/"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('class="homework-divider"', html)

    # ── День 14: инварианты ─────────────────────────────────────────────

    def _day_fourteen_client(self, store_dir, reviews=None, replies=None):
        from tests.test_agent import FakeTransport

        class ReviewingTransport(FakeTransport):
            """На запрос ревизора отдаёт JSON, на ходы — обычный текст."""

            def __init__(self, replies=None, reviews=None):
                super().__init__(replies=replies)
                self._reviews = list(reviews or [])

            def send(self, payload):
                if payload.get("response_format") == {"type": "json_object"}:
                    self.payloads.append(payload)
                    content = self._reviews.pop(0) if self._reviews else '{"violations": [], "refused": false}'
                    return {
                        "model": "deepseek-v4-flash",
                        "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                        "usage": {"prompt_tokens": 40, "completion_tokens": 7},
                    }
                return super().send(payload)

        transport = ReviewingTransport(replies=replies, reviews=reviews)
        app = create_app({"TESTING": True, "AGENT_STORE_DIR": store_dir, "AGENT_TRANSPORT": transport})
        client = app.test_client()
        client.set_cookie("agent_session", "s14")
        return client, transport

    @staticmethod
    def _chat14(client, message):
        import json as json_module

        response = client.post("/days/14/api/chat/stream", json={"message": message})
        return [json_module.loads(line) for line in response.get_data(as_text=True).splitlines()]

    def test_day_fourteen_puts_the_rules_into_the_prompt_and_records_a_refusal(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_fourteen_client(
                store_dir, replies=["Не могу: нарушает инвариант 1 — только Flask. Вместо этого…"], reviews=['{"violations": [], "refused": true}']
            )
            events = self._chat14(client, "перепиши на Django")

            turns = [item for item in transport.payloads if "response_format" not in item]
            system = next(m for m in turns[-1]["messages"] if m["role"] == "system")["content"]
            self.assertIn("Инварианты проекта", system)
            self.assertIn("1. [стек]", system)
            self.assertLess(system.index("Инварианты проекта"), len(system))

            record = next(e for e in events if e["type"] == "invariants_checked")["record"]
            self.assertTrue(record["refused"])
            self.assertEqual(record["named"], ["stack"])
            self.assertEqual(record["verdict"], "held")
            state = events[-1]["snapshot"]
            self.assertEqual(state["summary"]["refused"], 1)
            self.assertEqual(state["side"]["requests"], 1)

    def test_day_fourteen_catches_a_yielded_answer_by_regex_and_reviewer(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_fourteen_client(
                store_dir,
                replies=["Ставим Django: pip install django", "Подключаем платный API, ключ в .env"],
                reviews=['{"violations": [{"id": "stack", "why": "предлагает Django"}], "refused": false}', '{"violations": [{"id": "paid", "why": "платный API"}], "refused": false}'],
            )
            first = next(e for e in self._chat14(client, "давай Django") if e["type"] == "invariants_checked")["record"]
            self.assertEqual([v["invariant"] for v in first["regex"]], ["stack"])
            self.assertEqual(first["verdict"], "caught")

            second = next(e for e in self._chat14(client, "платный API") if e["type"] == "invariants_checked")["record"]
            self.assertEqual(second["regex"], [])  # у paid нет регулярки — только ревизор
            self.assertEqual([v["invariant"] for v in second["reviewer"]], ["paid"])
            self.assertEqual(second["verdict"], "caught")

            summary = client.get("/days/14/api/state").get_json()["summary"]
            self.assertEqual(summary["caught"], 2)

    def test_day_fourteen_reviewer_clears_a_regex_false_alarm(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_fourteen_client(
                store_dir, replies=["Пароли только bcrypt. Никакого открытого текста, base64, md5."], reviews=['{"violations": [], "refused": false}']
            )
            record = next(e for e in self._chat14(client, "как хранить пароли?") if e["type"] == "invariants_checked")["record"]

            self.assertEqual([v["invariant"] for v in record["regex"]], ["passwords"])
            self.assertEqual(record["reviewer"], [])
            self.assertEqual(record["verdict"], "false_alarm")

    def test_day_fourteen_reviewer_failure_keeps_the_answer_and_lets_regex_decide(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_fourteen_client(store_dir, replies=["Ставим Django"], reviews=["не json"])
            events = self._chat14(client, "давай Django")
            record = next(e for e in events if e["type"] == "invariants_checked")["record"]

            self.assertIn("не JSON", record["reviewer_error"])
            self.assertIsNone(record["reviewer"])
            self.assertEqual(record["verdict"], "caught")
            self.assertEqual(events[-1]["reply"]["content"], "Ставим Django")

    def test_day_fourteen_manages_custom_rules_and_protects_presets(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_fourteen_client(store_dir, replies=["ок"])
            state = client.post("/days/14/api/invariant", json={"id": "lang", "rule": "Только русский язык в интерфейсе", "kind": "decision", "check": "\\benglish\\b"}).get_json()
            self.assertIn("lang", [i["id"] for i in state["invariants"]])

            state = client.post("/days/14/api/invariant/toggle", json={"id": "paid", "enabled": False}).get_json()
            paid = next(i for i in state["invariants"] if i["id"] == "paid")
            self.assertFalse(paid["enabled"])
            self._chat14(client, "в")
            turns = [item for item in transport.payloads if "response_format" not in item]
            system = next(m for m in turns[-1]["messages"] if m["role"] == "system")["content"]
            self.assertNotIn("платные внешние сервисы", system)
            self.assertIn("Только русский язык", system)

            self.assertEqual(client.post("/days/14/api/invariant", json={"id": "stack", "rule": "x", "kind": "stack"}).status_code, 400)
            self.assertEqual(client.post("/days/14/api/invariant", json={"id": "bad", "rule": "x", "kind": "stack", "check": "("}).status_code, 400)
            self.assertEqual(client.delete("/days/14/api/invariant", json={"id": "stack"}).status_code, 400)
            state = client.delete("/days/14/api/invariant", json={"id": "lang"}).get_json()
            self.assertNotIn("lang", [i["id"] for i in state["invariants"]])

    def test_day_fourteen_reset_keeps_rules_and_journal(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_fourteen_client(store_dir, replies=["ок"])
            self._chat14(client, "в")
            state = client.post("/days/14/api/reset", json={}).get_json()
            self.assertEqual(state["messages"], [])
            self.assertEqual(len(state["checks"]), 1)
            state = client.delete("/days/14/api/checks").get_json()
            self.assertEqual(state["checks"], [])

    def test_day_fourteen_serves_the_scenario(self):
        response = self.client.get("/days/14/api/scenario")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["messages"]), 5)

    def test_day_fifteen_renders_the_transitions_homework(self):
        response = self.client.get("/days/15/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("День 15.", html)
        self.assertIn("Контролируемые переходы", html)
        self.assertIn("Домашнее задание", html)
        self.assertIn("явные переходы между состояниями задачи", html)
        self.assertIn("допустимые состояния", html)
        self.assertIn("разрешённые переходы", html)
        self.assertIn("«перепрыгнуть» этап", html)
        self.assertIn("нельзя делать реализацию до утверждённого плана", html)
        self.assertIn("нельзя делать финал без валидации", html)
        self.assertIn("продолжения после паузы", html)
        self.assertIn("Ассистент с контролируемым жизненным циклом задачи", html)
        self.assertEqual(html.count('class="day15-rule"'), 4)
        self.assertIn('data-jump-to="done"', html)
        self.assertIn("planning → execution", html)
        self.assertIn("css/day01.css", html)
        self.assertIn("css/homework.css", html)
        self.assertIn("css/day15.css", html)
        self.assertIn('href="/days/15/"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('class="homework-divider"', html)

    # ── День 15: контролируемые переходы ───────────────────────────────

    def _day_fifteen_client(self, store_dir, reports=None, replies=None):
        from tests.test_agent import FakeTransport

        class ObservingTransport(FakeTransport):
            def __init__(self, replies=None, reports=None):
                super().__init__(replies=replies)
                self._reports = list(reports or [])

            def send(self, payload):
                if payload.get("response_format") == {"type": "json_object"}:
                    self.payloads.append(payload)
                    content = self._reports.pop(0) if self._reports else '{"plan": [], "plan_ready": false}'
                    return {
                        "model": "deepseek-v4-flash",
                        "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                        "usage": {"prompt_tokens": 30, "completion_tokens": 9},
                    }
                return super().send(payload)

        transport = ObservingTransport(replies=replies, reports=reports)
        app = create_app({"TESTING": True, "AGENT_STORE_DIR": store_dir, "AGENT_TRANSPORT": transport})
        client = app.test_client()
        client.set_cookie("agent_session", "s15")
        return client, transport

    @staticmethod
    def _chat15(client, message):
        import json as json_module

        response = client.post("/days/15/api/chat/stream", json={"message": message})
        return [json_module.loads(line) for line in response.get_data(as_text=True).splitlines()]

    def test_day_fifteen_button_cannot_jump_and_the_refusal_is_logged(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_fifteen_client(store_dir, reports=['{"plan": ["a", "b"], "plan_ready": true}'], replies=["план"])
            self._chat15(client, "цель")

            response = client.post("/days/15/api/task/transition", json={"to": "done"})
            data = response.get_json()

            self.assertEqual(response.status_code, 409)
            self.assertTrue(data["transition"]["refused"])
            self.assertIn("переход planning → done не разрешён", data["transition"]["reason"])
            self.assertEqual(data["task"]["stage"], "planning")
            self.assertEqual(data["summary"]["refused_user"], 1)
            self.assertEqual(data["task"]["allowed"], ["execution"])

            ok = client.post("/days/15/api/task/transition", json={"to": "execution"})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.get_json()["task"]["stage"], "execution")
            self.assertEqual(client.post("/days/15/api/task/transition", json={"to": "нет"}).status_code, 400)

    def test_day_fifteen_model_requests_go_through_the_gate(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, transport = self._day_fifteen_client(
                store_dir,
                reports=['{"plan": ["a"], "plan_ready": true}', '{"step_done": true, "note": "ок"}'],
                replies=["план\n[переход: execution]", "шаг сделан\n[переход: validation]"],
            )
            events = self._chat15(client, "цель")
            refused = next(e for e in events if e["type"] == "task_transition")["record"]
            self.assertTrue(refused["refused"])
            self.assertEqual(refused["actor"], "model")
            turns = [item for item in transport.payloads if "response_format" not in item]
            system = next(m for m in turns[-1]["messages"] if m["role"] == "system")["content"]
            self.assertIn("Разрешённые переходы", system)

            client.post("/days/15/api/task/transition", json={"to": "execution"})
            events = self._chat15(client, "делай")
            # Модель попросила validation, пока шаг открыт — отказ; потом наблюдатель
            # закрыл последний шаг, и автомат сам ушёл на проверку разрешённым переходом.
            transitions = [e["record"] for e in events if e["type"] == "task_transition"]
            self.assertTrue(transitions[0]["refused"])
            self.assertIn("не закрыты шаги 1", transitions[0]["reason"])
            self.assertEqual(events[-1]["snapshot"]["task"]["stage"], "validation")
            self.assertEqual(events[-1]["snapshot"]["summary"]["refused_model"], 2)

    def test_day_fifteen_observer_cannot_finish_the_task(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_fifteen_client(
                store_dir,
                reports=['{"plan": ["a"], "plan_ready": true}', '{"step_done": true, "note": "ок"}', '{"verdict": "ok", "step": null, "issues": []}'],
                replies=["план", "сделал", "всё сходится"],
            )
            self._chat15(client, "цель")
            client.post("/days/15/api/task/transition", json={"to": "execution"})
            self._chat15(client, "делай")
            events = self._chat15(client, "проверь")

            refused = [e["record"] for e in events if e["type"] == "task_transition" and e["record"]["refused"]]
            self.assertEqual(refused[-1]["actor"], "observer")
            self.assertIn("только пользователь", refused[-1]["reason"])
            self.assertEqual(events[-1]["snapshot"]["task"]["stage"], "validation")

            done = client.post("/days/15/api/task/transition", json={"to": "done"}).get_json()
            self.assertEqual(done["task"]["stage"], "done")

    def test_day_fifteen_refusing_answer_keeps_the_plan_and_pause_survives_reset(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_fifteen_client(
                store_dir,
                reports=['{"plan": ["a", "b"], "plan_ready": true}', '{"plan": [], "plan_ready": false}'],
                replies=["план", "Не могу: сначала утвердите план"],
            )
            self._chat15(client, "цель")
            events = self._chat15(client, "пиши код сейчас")
            check = next(e for e in events if e["type"] == "task_jump_check")
            self.assertTrue(check["refused"])
            self.assertEqual(len(events[-1]["snapshot"]["task"]["steps"]), 2)

            client.post("/days/15/api/task/pause", json={})
            state = client.post("/days/15/api/reset", json={}).get_json()
            self.assertEqual(state["messages"], [])
            self.assertTrue(state["task"]["paused"])
            self.assertEqual(state["task"]["allowed"], [])
            self.assertEqual(client.post("/days/15/api/task/transition", json={"to": "execution"}).status_code, 409)

    def test_day_fifteen_return_to_step_reopens_it_and_manual_close_gets_out(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_fifteen_client(
                store_dir, reports=['{"plan": ["a", "b"], "plan_ready": true}', '{"step_done": true, "note": "ок"}', '{"step_done": true, "note": "ок"}'], replies=["план", "1", "2"]
            )
            self._chat15(client, "цель")
            client.post("/days/15/api/task/transition", json={"to": "execution"})
            self._chat15(client, "делай")
            state = self._chat15(client, "дальше")[-1]["snapshot"]
            self.assertEqual(state["task"]["stage"], "validation")
            self.assertEqual(state["task"]["allowed"], ["done", "execution"])

            # «→ execution» с проверки — возврат: последний шаг открывается заново.
            back = client.post("/days/15/api/task/transition", json={"to": "execution"}).get_json()
            self.assertEqual(back["task"]["stage"], "execution")
            self.assertEqual([s["done"] for s in back["task"]["steps"]], [True, False])
            self.assertEqual(client.post("/days/15/api/task/transition", json={"to": "validation"}).status_code, 409)

            # Закрыть шаг руками — и проверка снова разрешена.
            closed = client.post("/days/15/api/task/step", json={}).get_json()
            self.assertEqual(closed["task"]["stage"], "validation")
            self.assertEqual(client.post("/days/15/api/task/step", json={}).status_code, 409)

    def test_day_fifteen_keeps_its_task_apart_from_day_thirteen(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client, _ = self._day_fifteen_client(store_dir, reports=['{"plan": ["a"], "plan_ready": true}'], replies=["план"])
            self._chat15(client, "цель")
            self.assertIn("s15-task15.json", os.listdir(store_dir))
            self.assertIn("s15-d15.json", os.listdir(store_dir))
            self.assertNotIn("s15-task.json", os.listdir(store_dir))

    def test_day_fifteen_serves_the_guarded_scenario(self):
        response = self.client.get("/days/15/api/scenario")
        scenario = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(scenario["key"], "task-guarded")
        self.assertEqual([s["kind"] for s in scenario["steps"]].count("jump"), 2)

    def test_day_sixteen_renders_the_mcp_connection_homework(self):
        response = self.client.get("/days/16/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("День 16.", html)
        self.assertIn("Подключение MCP", html)
        self.assertIn("Домашнее задание", html)
        self.assertIn("Установите MCP SDK / клиент", html)
        self.assertIn("устанавливает MCP-соединение", html)
        self.assertIn("список доступных инструментов", html)
        self.assertIn("соединение устанавливается", html)
        self.assertIn("список инструментов корректно возвращается", html)
        self.assertIn("Видео + код", html)
        self.assertIn("геттулз у любого общедоступного mcp", html)
        self.assertEqual(html.count('class="day16-rule"'), 4)
        self.assertIn("tools/list", html)
        self.assertIn('value="deepwiki" checked', html)
        self.assertIn('value="everything"', html)
        self.assertIn("https://mcp.deepwiki.com/mcp", html)
        self.assertIn("npx -y @modelcontextprotocol/server-everything", html)
        self.assertIn('data-inspect-url="/days/16/api/inspect"', html)
        self.assertIn("css/day01.css", html)
        self.assertIn("css/homework.css", html)
        self.assertIn("css/day16.css", html)
        self.assertIn("js/day16.js", html)
        self.assertIn('href="/days/16/"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('class="homework-divider"', html)

    def _day_sixteen_client(self, factory):
        return create_app({"TESTING": True, "MCP_TRANSPORT_FACTORY": factory}).test_client()

    def test_day_sixteen_connects_and_returns_tools_with_the_wire(self):
        from tests.test_mcp import legacy_transport

        client = self._day_sixteen_client(legacy_transport())
        response = client.post("/days/16/api/inspect", json={"server": "deepwiki"})
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["server"]["key"], "deepwiki")
        self.assertEqual(data["server"]["transport"], "http")
        self.assertEqual(data["protocol_version"], "2025-11-25")
        self.assertEqual([tool["name"] for tool in data["tools"]], ["search", "summarize", "save"])
        self.assertEqual(data["required_params"], 2)
        self.assertGreater(data["schema_tokens"], 0)
        methods = [message["method"] for message in data["wire"] if message["direction"] == "out"]
        self.assertEqual(methods[:3], ["server/discover", "initialize", "notifications/initialized"])
        self.assertIn("tools/list", methods)

    def test_day_sixteen_failed_connection_is_502_with_the_reason(self):
        def refused(server, errlog):
            raise ConnectionRefusedError("порт закрыт")

        response = self._day_sixteen_client(refused).post("/days/16/api/inspect", json={"server": "everything"})
        data = response.get_json()

        self.assertEqual(response.status_code, 502)
        self.assertFalse(data["ok"])
        self.assertIn("порт закрыт", data["error"])
        self.assertEqual(data["tools"], [])

    def test_day_sixteen_rejects_unknown_server(self):
        for payload in ({"server": "nope"}, {}, {"server": 1}, ["deepwiki"]):
            with self.subTest(payload=payload):
                response = self.client.post("/days/16/api/inspect", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json(), {"error": "Выберите сервер из списка"})

    def test_day_seventeen_renders_the_first_mcp_tool_homework(self):
        response = self.client.get("/days/17/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("День 17.", html)
        self.assertIn("Первый инструмент MCP", html)
        self.assertIn("Домашнее задание", html)
        self.assertIn("свой MCP-сервер вокруг любого API", html)
        self.assertIn("Яндекс.Трекер, Git, CRM, mock API", html)
        self.assertIn("регистрацию инструмента", html)
        self.assertIn("описание входных параметров", html)
        self.assertIn("возврат результата", html)
        self.assertIn("вызовите его из приложения", html)
        self.assertIn("Получите и используйте результат", html)
        self.assertIn("Агент делает вызов к MCP-инструменту и получает результат", html)
        self.assertIn("он дёргает какие нужно MCP-вызовы", html)
        self.assertEqual(html.count('class="day17-rule"'), 4)
        self.assertIn("GitHub · наш сервер", html)
        self.assertIn("python -m advent.mcp.servers.github", html)
        self.assertIn('data-chat-url="/days/17/api/chat/stream"', html)
        self.assertIn('data-tools-url="/days/17/api/tools"', html)
        self.assertIn("Сценарий «GitHub через MCP»", html)
        self.assertIn("css/day01.css", html)
        self.assertIn("css/homework.css", html)
        self.assertIn("css/agent-chat.css", html)
        self.assertIn("css/day17.css", html)
        self.assertIn("js/day17.js", html)
        self.assertIn('href="/days/17/"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('class="homework-divider"', html)

    def _day_seventeen_client(self, store_dir, model=None, api=None):
        from tests.test_mcp import ScriptedModel, github_transport

        app = create_app(
            {
                "TESTING": True,
                "AGENT_STORE_DIR": store_dir,
                "AGENT_TRANSPORT": model or ScriptedModel(),
                "MCP_TRANSPORT_FACTORY": github_transport(api),
            }
        )
        client = app.test_client()
        client.set_cookie("agent_session", "s17")
        return client

    def _chat17(self, client, text):
        response = client.post("/days/17/api/chat/stream", json={"message": text})
        return response, [json.loads(line) for line in response.get_data(as_text=True).splitlines() if line.strip()]

    def test_day_seventeen_lists_the_tools_of_its_own_server(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            data = self._day_seventeen_client(store_dir).get("/days/17/api/tools").get_json()

        self.assertTrue(data["ok"])
        self.assertEqual(data["server"]["key"], "github")
        self.assertEqual(
            [tool["name"] for tool in data["tools"]],
            ["search_repositories", "get_repository", "list_user_repositories"],
        )

    def test_day_seventeen_turn_streams_the_call_and_is_saved(self):
        import os
        import tempfile

        from tests.test_mcp import FakeGitHub, ScriptedModel, call_reply, text_reply

        with tempfile.TemporaryDirectory() as store_dir:
            api = FakeGitHub()
            model = ScriptedModel(
                call_reply("search_repositories", {"query": "mcp language:python", "limit": 2}),
                text_reply("Нашёл два репозитория."),
            )
            client = self._day_seventeen_client(store_dir, model, api)
            response, events = self._chat17(client, "Найди два репозитория про MCP")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "application/x-ndjson")
            self.assertEqual([e["type"] for e in events], ["connected", "model", "tool_call", "tool_result", "answer", "done"])
            self.assertEqual(events[2]["arguments"], {"query": "mcp language:python", "limit": 2})
            self.assertEqual(api.requests[0][1]["q"], "mcp language:python")
            state = events[-1]["state"]
            self.assertEqual(state["summary"]["tool_calls"], 1)
            self.assertEqual(state["summary"]["llm_calls"], 2)
            self.assertTrue(state["file"]["exists"])
            self.assertIn("s17-d17.json", os.listdir(store_dir))
            self.assertNotIn("for_model", state["calls"][0])

            restored = client.get("/days/17/api/state").get_json()
            self.assertEqual([m["role"] for m in restored["messages"]], ["user", "assistant", "tool", "assistant"])
            page = client.get("/days/17/").get_data(as_text=True)
            self.assertIn('id="agent-initial"', page)

            cleared = client.post("/days/17/api/reset").get_json()
            self.assertEqual(cleared["messages"], [])
            self.assertNotIn("s17-d17.json", os.listdir(store_dir))

    def test_day_seventeen_reports_a_failed_turn(self):
        import tempfile

        from tests.test_mcp import ScriptedModel

        with tempfile.TemporaryDirectory() as store_dir:
            client = self._day_seventeen_client(store_dir, ScriptedModel(error_at=1))
            _, events = self._chat17(client, "Что угодно")

            self.assertEqual(events[-1], {"type": "error", "error": "DeepSeek недоступен"})
            self.assertEqual(client.get("/days/17/api/state").get_json()["messages"], [])

    def test_day_seventeen_rejects_an_empty_message_and_serves_the_scenario(self):
        import tempfile

        with tempfile.TemporaryDirectory() as store_dir:
            client = self._day_seventeen_client(store_dir)
            self.assertEqual(client.post("/days/17/api/chat/stream", json={"message": " "}).status_code, 400)

        scenario = self.client.get("/days/17/api/scenario").get_json()
        self.assertEqual(scenario["key"], "mcp-github")
        self.assertEqual(len(scenario["messages"]), 4)

    def test_empty_prompt_is_rejected_on_day_one_api(self):
        response = self.client.post("/days/1/api/chat", json={"prompt": "  "})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "Введите запрос"})

    def test_non_object_json_is_rejected_instead_of_crashing(self):
        for payload in ("hello", 1, True, ["prompt"]):
            with self.subTest(payload=payload):
                response = self.client.post("/days/1/api/chat", json=payload)

                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.get_json(),
                    {"error": "Ожидался JSON-объект с полем prompt"},
                )

    @patch("advent.days.day01.routes.request_completion")
    def test_successful_completion_is_forwarded(self, request_completion):
        request_completion.return_value = (
            {"choices": [{"message": {"content": "Готово"}}]},
            200,
        )

        response = self.client.post(
            "/days/1/api/chat",
            json={"prompt": "  Привет  "},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["choices"][0]["message"]["content"],
            "Готово",
        )
        request_completion.assert_called_once_with("Привет")

    @patch("advent.days.day01.routes.request_completion")
    def test_upstream_error_status_is_preserved(self, request_completion):
        request_completion.return_value = (
            {"error": {"message": "Недоступная модель"}},
            422,
        )

        response = self.client.post(
            "/days/1/api/chat",
            json={"prompt": "Тест"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["error"]["message"], "Недоступная модель")

    @patch("advent.days.day02.routes.request_day_two_comparison")
    def test_day_two_forwards_valid_comparison_parameters(self, comparison_request):
        comparison_request.return_value = (
            {
                "controlled": {"content": "Готово"},
                "baseline": {"content": "Готово"},
                "analysis": ["Тест"],
                "settings": {"output_format": "json", "max_tokens": 256},
            },
            200,
        )

        response = self.client.post(
            "/days/2/api/compare",
            json={
                "prompt": "  Страны Европы  ",
                "model": "deepseek-v4-flash",
                "output_format": "json",
                "max_tokens": 256,
                "stop_sequence": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["analysis"], ["Тест"])
        comparison_request.assert_called_once_with(
            "Страны Европы",
            model="deepseek-v4-flash",
            output_format="json",
            max_tokens=256,
            stop_sequence="",
        )

    @patch("advent.days.day02.routes.stream_day_two_answer")
    def test_day_two_proxies_stream_events_as_ndjson(self, stream_answer):
        stream_answer.return_value = iter(
            [
                {
                    "type": "start",
                    "variant": "controlled",
                    "request": {"method": "POST", "url": "https://example.test"},
                },
                {"type": "delta", "variant": "controlled", "content": "При"},
                {
                    "type": "done",
                    "variant": "controlled",
                    "answer": {"content": "Привет"},
                },
            ]
        )

        response = self.client.post(
            "/days/2/api/compare/stream",
            json={
                "variant": "controlled",
                "prompt": "  Страны Европы  ",
                "model": "deepseek-v4-flash",
                "output_format": "json",
                "max_tokens": 256,
                "stop_sequence": "",
            },
        )
        events = [json.loads(line) for line in response.get_data(as_text=True).splitlines()]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/x-ndjson")
        self.assertEqual([event["type"] for event in events], ["start", "delta", "done"])
        stream_answer.assert_called_once_with(
            "Страны Европы",
            variant="controlled",
            model="deepseek-v4-flash",
            output_format="json",
            max_tokens=256,
            stop_sequence="",
        )

    @patch("advent.days.day02.routes.request_day_two_meta_analysis")
    def test_day_two_requests_meta_analysis_for_both_answers(self, meta_analysis):
        meta_analysis.return_value = ({"content": "Краткое сравнение"}, 200)
        controlled = {"content": "JSON", "characters": 4}
        baseline = {"content": "Текст", "characters": 5}

        response = self.client.post(
            "/days/2/api/compare/meta-analysis",
            json={
                "prompt": "Страны Европы",
                "model": "deepseek-v4-pro",
                "output_format": "json",
                "max_tokens": 256,
                "stop_sequence": "",
                "controlled": controlled,
                "baseline": baseline,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["content"], "Краткое сравнение")
        meta_analysis.assert_called_once_with(
            "Страны Европы",
            model="deepseek-v4-pro",
            output_format="json",
            max_tokens=256,
            stop_sequence="",
            controlled=controlled,
            baseline=baseline,
        )

    def test_day_two_rejects_stop_sequence_for_strict_json(self):
        response = self.client.post(
            "/days/2/api/compare",
            json={
                "prompt": "Тест",
                "model": "deepseek-v4-flash",
                "output_format": "json",
                "max_tokens": 256,
                "stop_sequence": "<END>",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {"error": "Для строгого JSON оставьте стоп-последовательность пустой"},
        )

    def test_legacy_api_address_still_works(self):
        response = self.client.post("/api/chat", json={"prompt": ""})

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()

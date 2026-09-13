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

    def test_every_day_collapses_its_homework_block_by_default(self):
        for number in (1, 2, 3, 4, 5, 6, 7, 8):
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

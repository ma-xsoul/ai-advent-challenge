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

    def test_every_day_collapses_its_homework_block_by_default(self):
        for number in (1, 2, 3, 4, 5):
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
        self.assertIn('href="/days/5/"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn('class="homework-divider"', html)

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

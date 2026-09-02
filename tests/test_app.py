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

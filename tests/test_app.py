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
        self.assertIn("Напишите минимальный код", html)
        self.assertIn("css/day01-homework.css", html)
        self.assertIn('aria-current="page"', html)
        self.assertEqual(html.count('aria-current="page"'), 1)
        self.assertIn("js/day01.js", html)
        self.assertNotIn('href="/days/2/"', html)

    def test_hidden_day_two_route_still_exists_without_a_menu_item(self):
        response = self.client.get("/days/2/")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Задание — на подходе", html)
        self.assertIn("css/day02.css", html)
        self.assertNotIn("js/day01.js", html)
        self.assertNotIn('href="/days/2/"', html)

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

    def test_legacy_api_address_still_works(self):
        response = self.client.post("/api/chat", json={"prompt": ""})

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()

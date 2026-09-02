import json
import os
import unittest
from unittest.mock import Mock, patch

from advent.services.deepseek import (
    DEEPSEEK_API_URL,
    DEEPSEEK_BETA_API_URL,
    request_day_two_comparison,
    request_day_two_meta_analysis,
    stream_day_two_answer,
)


def make_response(payload):
    response = Mock()
    response.status_code = 200
    response.json.return_value = payload
    return response


class DeepSeekServiceTestCase(unittest.TestCase):
    @patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"})
    @patch("advent.services.deepseek.requests.post")
    def test_meta_analysis_is_limited_and_receives_both_variants(self, post):
        post.return_value = make_response(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "Первый вариант предсказуемее."},
                    }
                ],
                "usage": {"completion_tokens": 12},
                "model": "deepseek-v4-pro",
            }
        )

        result, status = request_day_two_meta_analysis(
            "Страны Европы",
            model="deepseek-v4-pro",
            output_format="json",
            max_tokens=256,
            stop_sequence="",
            controlled={"content": "JSON", "characters": 4, "valid_json": True},
            baseline={"content": "Текст", "characters": 5},
        )

        self.assertEqual(status, 200)
        self.assertEqual(result["content"], "Первый вариант предсказуемее.")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["max_tokens"], 400)
        self.assertIn("не более чем за 400 токенов", payload["messages"][0]["content"])
        self.assertIn('"with_parameters"', payload["messages"][1]["content"])
        self.assertIn('"without_parameters"', payload["messages"][1]["content"])

    @patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"})
    @patch("advent.services.deepseek.requests.post")
    def test_controlled_json_answer_is_streamed_and_normalized(self, post):
        chunks = [
            {
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "finish_reason": None,
                        "delta": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "arguments": '{"valid":true,"countries":['
                                    }
                                }
                            ]
                        },
                    }
                ],
            },
            {
                "choices": [
                    {
                        "finish_reason": None,
                        "delta": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "arguments": (
                                            '{"code":"AL","name":"Albania",'
                                            '"nameRu":"Албания"}]}'
                                        )
                                    }
                                }
                            ]
                        },
                    }
                ]
            },
            {
                "choices": [{"finish_reason": "tool_calls", "delta": {}}],
                "usage": {"completion_tokens": 18},
            },
        ]
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = [
            ("data: " + json.dumps(chunk, ensure_ascii=False)).encode("utf-8")
            for chunk in chunks
        ] + [b"data: [DONE]"]
        post.return_value = response

        events = list(
            stream_day_two_answer(
                "Все страны на A",
                variant="controlled",
                model="deepseek-v4-flash",
                output_format="json",
                max_tokens=256,
                stop_sequence="",
            )
        )

        self.assertEqual(events[0]["type"], "start")
        self.assertTrue(events[0]["request"]["body"]["stream"])
        self.assertEqual(
            "".join(event["content"] for event in events if event["type"] == "delta"),
            '{"valid":true,"countries":[{"code":"AL","name":"Albania",'
            '"nameRu":"Албания"}]}',
        )
        self.assertEqual(events[-1]["type"], "done")
        self.assertIn('"original": "Все страны на A"', events[-1]["answer"]["content"])
        self.assertEqual(events[-1]["answer"]["output_tokens"], 18)
        self.assertTrue(post.call_args.kwargs["stream"])

    @patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"})
    @patch("advent.services.deepseek.requests.post")
    def test_json_comparison_uses_strict_tool_and_clean_baseline(self, post):
        post.side_effect = [
            make_response(
                {
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "tool_calls": [
                                    {
                                        "function": {
                                            "name": "return_country_search",
                                            "arguments": (
                                                '{"valid":true,"countries":['
                                                '{"code":"al","name":"Albania",'
                                                '"nameRu":"Албания","note":"extra"}],'
                                                '"unexpected":"ignored"}'
                                            ),
                                        }
                                    }
                                ]
                            },
                        }
                    ],
                    "usage": {"completion_tokens": 18},
                    "model": "deepseek-v4-flash",
                }
            ),
            make_response(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": "Обычный ответ"},
                        }
                    ],
                    "usage": {"completion_tokens": 9},
                    "model": "deepseek-v4-flash",
                }
            ),
        ]

        result, status = request_day_two_comparison(
            "Все страны на A",
            model="deepseek-v4-flash",
            output_format="json",
            max_tokens=256,
            stop_sequence="",
        )

        self.assertEqual(status, 200)
        controlled_json = result["controlled"]["content"]
        self.assertIn('"original": "Все страны на A"', controlled_json)
        self.assertIn('"total": 1', controlled_json)
        self.assertIn('"code": "AL"', controlled_json)
        self.assertNotIn("note", controlled_json)
        self.assertNotIn("unexpected", controlled_json)
        self.assertEqual(result["baseline"]["content"], "Обычный ответ")
        self.assertEqual(
            result["request_preview"]["controlled"]["url"],
            DEEPSEEK_BETA_API_URL,
        )
        self.assertEqual(post.call_count, 2)

        controlled_call, baseline_call = post.call_args_list
        self.assertEqual(controlled_call.args[0], DEEPSEEK_BETA_API_URL)
        self.assertEqual(controlled_call.kwargs["json"]["max_tokens"], 256)
        self.assertIn("tools", controlled_call.kwargs["json"])
        self.assertIn("tool_choice", controlled_call.kwargs["json"])
        self.assertNotIn("response_format", controlled_call.kwargs["json"])
        self.assertNotIn("stop", controlled_call.kwargs["json"])
        self.assertEqual(controlled_call.kwargs["json"]["model"], "deepseek-v4-flash")
        self.assertEqual(
            controlled_call.kwargs["json"]["messages"][0]["role"], "system"
        )
        self.assertIn(
            "всегда устанавливай valid=true",
            controlled_call.kwargs["json"]["messages"][0]["content"],
        )
        self.assertIn(
            "полностью завершённый ответ не более чем за 256 токенов",
            controlled_call.kwargs["json"]["messages"][0]["content"],
        )

        self.assertEqual(baseline_call.args[0], DEEPSEEK_API_URL)
        self.assertNotIn("max_tokens", baseline_call.kwargs["json"])
        self.assertNotIn("tools", baseline_call.kwargs["json"])
        self.assertNotIn("stop", baseline_call.kwargs["json"])
        self.assertEqual(
            baseline_call.kwargs["json"]["messages"],
            [{"role": "user", "content": "Все страны на A"}],
        )

    @patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"})
    @patch("advent.services.deepseek.requests.post")
    def test_text_comparison_passes_selected_stop_sequence(self, post):
        post.side_effect = [
            make_response(
                {
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": "Коротко"}}
                    ]
                }
            ),
            make_response(
                {
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": "Обычно"}}
                    ]
                }
            ),
        ]

        result, status = request_day_two_comparison(
            "Тест",
            model="deepseek-v4-pro",
            output_format="text",
            max_tokens=128,
            stop_sequence="### END ###",
        )

        self.assertEqual(status, 200)
        self.assertEqual(post.call_args_list[0].args[0], DEEPSEEK_API_URL)
        self.assertEqual(post.call_args_list[0].kwargs["json"]["stop"], ["### END ###"])
        self.assertEqual(
            post.call_args_list[0].kwargs["json"]["response_format"], {"type": "text"}
        )
        self.assertIn("### END ###", result["analysis"][-1])

    @patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"})
    @patch("advent.services.deepseek.requests.post")
    def test_truncated_json_is_shown_with_a_clear_warning(self, post):
        post.side_effect = [
            make_response(
                {
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {
                                "tool_calls": [
                                    {
                                        "function": {
                                            "name": "return_country_search",
                                            "arguments": '{"valid":true,"countries":[',
                                        }
                                    }
                                ]
                            },
                        }
                    ],
                    "usage": {"completion_tokens": 64},
                }
            ),
            make_response(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": "Полный базовый ответ"},
                        }
                    ]
                }
            ),
        ]

        result, status = request_day_two_comparison(
            "Тест",
            model="deepseek-v4-flash",
            output_format="json",
            max_tokens=64,
            stop_sequence="",
        )

        self.assertEqual(status, 200)
        self.assertFalse(result["controlled"]["valid_json"])
        self.assertEqual(result["controlled"]["finish_reason"], "length")
        self.assertIn("обрезан", result["controlled"]["warning"].lower())
        self.assertEqual(result["baseline"]["content"], "Полный базовый ответ")

    @patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"})
    @patch("advent.services.deepseek.requests.post")
    def test_unrelated_request_is_rejected_only_by_controlled_request(self, post):
        post.side_effect = [
            make_response(
                {
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "tool_calls": [
                                    {
                                        "function": {
                                            "name": "return_country_search",
                                            "arguments": '{"valid":false,"countries":[]}',
                                        }
                                    }
                                ]
                            },
                        }
                    ]
                }
            ),
            make_response(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": "def example(): pass"},
                        }
                    ]
                }
            ),
        ]

        result, status = request_day_two_comparison(
            "Напиши функцию на Python",
            model="deepseek-v4-flash",
            output_format="json",
            max_tokens=512,
            stop_sequence="",
        )

        self.assertEqual(status, 200)
        self.assertTrue(result["controlled"]["rejected"])
        self.assertEqual(result["controlled"]["content"], "Некорректный запрос")
        self.assertEqual(result["baseline"]["content"], "def example(): pass")


if __name__ == "__main__":
    unittest.main()

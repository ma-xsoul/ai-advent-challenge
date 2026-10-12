import json
import unittest
from unittest.mock import Mock, patch

import requests

from advent.local import OllamaClient, OllamaError
from advent.local.ollama import metrics, think_for


def stream_response(lines, status=200):
    response = Mock(status_code=status)
    response.iter_lines.return_value = [json.dumps(line).encode() for line in lines]
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


DONE = {
    "done": True,
    "done_reason": "stop",
    "load_duration": 2_500_000_000,
    "prompt_eval_count": 20,
    "prompt_eval_duration": 200_000_000,
    "eval_count": 40,
    "eval_duration": 1_000_000_000,
    "total_duration": 3_800_000_000,
}


class OllamaClientTestCase(unittest.TestCase):
    def test_chat_stream_splits_thinking_and_answer_and_measures(self):
        lines = [
            {"message": {"thinking": "Считаю…"}},
            {"message": {"content": "Кан"}},
            {"message": {"content": "берра"}},
            {"message": {"content": ""}, **DONE},
        ]
        with patch("advent.local.ollama.requests.post", return_value=stream_response(lines)) as post:
            events = list(OllamaClient(url="http://ollama.test").chat_stream("gemma4:12b", [{"role": "user", "content": "?"}], think=False))

        payload = post.call_args.kwargs["json"]
        self.assertEqual((payload["model"], payload["stream"], payload["think"]), ("gemma4:12b", True, False))
        self.assertEqual([event["type"] for event in events], ["thinking", "delta", "delta", "done"])
        self.assertEqual("".join(event["text"] for event in events if event["type"] == "delta"), "Канберра")
        done = events[-1]
        self.assertEqual((done["load_s"], done["output_tps"], done["prompt_tps"], done["total_s"]), (2.5, 40.0, 100.0, 3.8))
        self.assertEqual((done["prompt_tokens"], done["output_tokens"], done["done_reason"]), (20, 40, "stop"))
        self.assertIsNotNone(done["first_token_s"])

    def test_errors_are_explained(self):
        client = OllamaClient(url="http://ollama.test")
        with patch("advent.local.ollama.requests.get", side_effect=requests.ConnectionError("refused")):
            with self.assertRaisesRegex(OllamaError, "ollama serve"):
                client.version()
        missing = Mock(status_code=404, json=lambda: {"error": "model 'x' not found"})
        with patch("advent.local.ollama.requests.post", return_value=missing):
            with self.assertRaisesRegex(OllamaError, "ollama pull"):
                list(client.chat_stream("x", []))
        with patch("advent.local.ollama.requests.post", return_value=stream_response([{"error": "out of memory"}])):
            with self.assertRaisesRegex(OllamaError, "out of memory"):
                list(client.chat_stream("gemma4:12b", []))

    def test_models_know_what_they_can_do_and_what_is_loaded(self):
        tags = {"models": [
            {"name": "bge-m3:latest", "size": 1_200_000_000, "details": {"parameter_size": "567M", "quantization_level": "F16", "family": "bert"}},
            {"name": "gemma4:12b", "size": 8_000_000_000, "details": {"parameter_size": "11.9B", "quantization_level": "Q4_K_M", "family": "gemma4"}},
        ]}
        ps = {"models": [{"name": "gemma4:12b", "size": 8_000_000_000, "size_vram": 6_000_000_000, "context_length": 4096}]}

        def get(url, timeout):
            return Mock(status_code=200, json=lambda: tags if url.endswith("/api/tags") else ps)

        def post(url, json, timeout):
            return Mock(status_code=200, json=lambda: {"capabilities": ["embedding"] if json["model"].startswith("bge") else ["completion", "thinking"]})

        client = OllamaClient(url="http://ollama.test")
        with patch("advent.local.ollama.requests.get", side_effect=get), patch("advent.local.ollama.requests.post", side_effect=post):
            models = client.models()
            loaded = client.loaded()

        self.assertEqual([model["name"] for model in models], ["gemma4:12b", "bge-m3:latest"])
        self.assertTrue(models[0]["chat"])
        self.assertFalse(models[1]["chat"])
        self.assertEqual((models[0]["parameters"], models[0]["quantization"]), ("11.9B", "Q4_K_M"))
        self.assertEqual(loaded, [{"name": "gemma4:12b", "size": 8_000_000_000, "vram": 6_000_000_000, "context": 4096, "expires_at": None}])

    def test_gpt_oss_cannot_switch_thinking_off(self):
        self.assertEqual(think_for("gpt-oss:20b", False), "low")
        self.assertEqual(think_for("gpt-oss:20b", True), "medium")
        self.assertIs(think_for("gemma4:12b", False), False)
        self.assertIs(think_for("gemma4:12b", True), True)

    def test_metrics_without_durations(self):
        result = metrics({"done": True}, started=0.0, first=None)
        self.assertIsNone(result["output_tps"])
        self.assertIsNone(result["first_token_s"])
        self.assertEqual(result["load_s"], 0.0)


if __name__ == "__main__":
    unittest.main()

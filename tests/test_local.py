import json
import unittest
from unittest.mock import Mock, patch

import requests

from advent.agent import Agent, AgentConfig
from advent.agent.transport import TransportError
from advent.local import OllamaClient, OllamaError, OllamaTransport
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


def completion(content="Ответ", reasoning=None):
    message = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning"] = reasoning
    return {
        "model": "gpt-oss:20b",
        "choices": [{"message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 4},
    }


def sse_response(chunks, status=200):
    response = Mock(status_code=status)
    lines = [b": keepalive", b""]
    for chunk in chunks:
        data = chunk if isinstance(chunk, str) else json.dumps(chunk)
        lines.extend([f"data: {data}".encode(), b""])
    response.iter_lines.return_value = lines
    return response


class OllamaTransportTestCase(unittest.TestCase):
    def test_cloud_models_are_rejected_before_send_or_stream_reaches_network(self):
        transport = OllamaTransport("http://ollama.test")
        with patch("advent.local.transport.requests.post") as post:
            for model in ("gemma4:cloud", "gpt-oss:20b-cloud", "GPT-OSS:120B-CLOUD"):
                with self.subTest(model=model):
                    with self.assertRaisesRegex(TransportError, "облачные модели запрещены"):
                        transport.send({"model": model, "messages": []})
                    with self.assertRaisesRegex(TransportError, "облачные модели запрещены"):
                        list(transport.stream({"model": model, "messages": []}))
            post.assert_not_called()

    def test_send_preserves_agent_contract_without_a_key_or_mutating_payload(self):
        payload = {
            "model": "gpt-oss:20b",
            "messages": [{"role": "system", "content": "По-русски"}, {"role": "user", "content": "?"}],
            "temperature": 0.7,
            "max_tokens": 800,
            "thinking": {"type": "disabled"},
            "stream": True,
            "stream_options": {"include_usage": False},
            "response_format": {"type": "json_object"},
            "tools": [{"type": "function", "function": {"name": "branch"}}],
        }
        original = json.loads(json.dumps(payload))
        response = Mock(status_code=200, json=lambda: completion(reasoning="Считаю"))
        with patch("advent.local.transport.requests.post", return_value=response) as post:
            reply = OllamaTransport("http://ollama.test/", timeout=15).send(payload)
        self.assertEqual(payload, original)
        self.assertEqual(post.call_args.args, ("http://ollama.test/v1/chat/completions",))
        request = post.call_args.kwargs
        self.assertEqual(request["headers"], {"Content-Type": "application/json"})
        self.assertEqual((request["timeout"], request["stream"]), (15, False))
        self.assertNotIn("thinking", request["json"])
        self.assertNotIn("stream_options", request["json"])
        self.assertEqual(request["json"]["reasoning_effort"], "low")
        for key in ("messages", "tools", "response_format", "temperature", "max_tokens"):
            self.assertEqual(request["json"][key], original[key])
        self.assertEqual(reply["choices"][0]["message"]["reasoning_content"], "Считаю")
        self.assertEqual(reply["usage"]["completion_tokens"], 4)
        response.close.assert_called_once()

    def test_url_and_default_model_come_from_local_configuration(self):
        response = Mock(status_code=200, json=lambda: completion())
        with patch.dict("os.environ", {"OLLAMA_URL": "http://local.test:11434/"}), patch(
            "advent.local.transport.requests.post", return_value=response
        ) as post:
            OllamaTransport().send({"messages": [], "thinking": {"type": "enabled"}})
        self.assertEqual(post.call_args.args[0], "http://local.test:11434/v1/chat/completions")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "gpt-oss:20b")
        self.assertEqual(post.call_args.kwargs["json"]["reasoning_effort"], "medium")

    def test_thinking_translation_respects_explicit_reasoning_effort(self):
        response = Mock(status_code=200, json=lambda: completion())
        with patch("advent.local.transport.requests.post", return_value=response) as post:
            transport = OllamaTransport("http://ollama.test")
            transport.send({"model": "gemma4:12b", "thinking": {"type": "disabled"}})
            self.assertEqual(post.call_args.kwargs["json"]["reasoning_effort"], "none")
            transport.send({"model": "gpt-oss:20b", "thinking": {"type": "enabled"}, "reasoning_effort": "high"})
            self.assertEqual(post.call_args.kwargs["json"]["reasoning_effort"], "high")

    def test_send_explains_http_json_and_connection_errors_and_closes_response(self):
        cases = [
            (Mock(status_code=404, json=lambda: {"error": {"message": "model 'x' not found"}}), "ollama pull"),
            (Mock(status_code=503, json=Mock(side_effect=ValueError("not JSON"))), "HTTP 503"),
            (Mock(status_code=200, json=Mock(side_effect=ValueError("not JSON"))), "формате JSON"),
            (Mock(status_code=200, json=lambda: []), "структуру ответа"),
            (Mock(status_code=200, json=lambda: {"choices": []}), "вариант ответа"),
            (Mock(status_code=200, json=lambda: {"error": "out of memory"}), "out of memory"),
        ]
        for response, message in cases:
            with self.subTest(message=message), patch("advent.local.transport.requests.post", return_value=response):
                with self.assertRaisesRegex(TransportError, message):
                    OllamaTransport("http://ollama.test").send({"messages": []})
                response.close.assert_called_once()
        with patch("advent.local.transport.requests.post", side_effect=requests.ConnectionError("refused")):
            with self.assertRaisesRegex(TransportError, "ollama serve"):
                OllamaTransport("http://ollama.test").send({})

    def test_stream_keeps_usage_and_reasoning_for_the_existing_agent(self):
        chunks = [
            {"model": "gpt-oss:20b", "choices": [{"delta": {"reasoning": "Считаю"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "Канберра"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 24, "completion_tokens": 10}},
            "[DONE]",
        ]
        response = sse_response(chunks)
        agent = Agent(AgentConfig(model="gpt-oss:20b"), transport=OllamaTransport("http://ollama.test"))
        with patch("advent.local.transport.requests.post", return_value=response) as post:
            text = list(agent.stream("Столица Австралии?"))
        self.assertEqual(text, ["Канберра"])
        self.assertEqual(agent.history[-1].content, "Канберра")
        self.assertEqual((agent.usage.input_tokens, agent.usage.output_tokens), (24, 10))
        self.assertEqual(post.call_args.kwargs["json"]["stream_options"], {"include_usage": True})
        self.assertTrue(post.call_args.kwargs["stream"])
        response.iter_lines.assert_called_once_with(chunk_size=1)
        response.close.assert_called_once()

    def test_stream_rejects_corrupt_or_incomplete_results_and_closes_response(self):
        delta = {"choices": [{"delta": {"content": "Часть"}, "finish_reason": None}]}
        cases = [
            (["{bad json"], "повреждённый JSON"),
            ([[]], "структуру ответа"),
            ([{"error": "out of memory"}], "out of memory"),
            ([delta], "оборвался"),
            ([delta, "[DONE]"], "без итогового"),
        ]
        for chunks, message in cases:
            response = sse_response(chunks)
            with self.subTest(message=message), patch("advent.local.transport.requests.post", return_value=response):
                with self.assertRaisesRegex(TransportError, message):
                    list(OllamaTransport("http://ollama.test").stream({}))
                response.close.assert_called_once()

    def test_stream_closes_on_http_connection_errors_and_consumer_cancellation(self):
        response = sse_response([], status=500)
        response.json.return_value = {"error": {"message": "out of memory"}}
        with patch("advent.local.transport.requests.post", return_value=response):
            with self.assertRaisesRegex(TransportError, "out of memory"):
                list(OllamaTransport("http://ollama.test").stream({}))
        response.close.assert_called_once()
        response = sse_response([])
        response.iter_lines.side_effect = requests.exceptions.ChunkedEncodingError("connection lost")
        with patch("advent.local.transport.requests.post", return_value=response):
            with self.assertRaisesRegex(TransportError, "прерван"):
                list(OllamaTransport("http://ollama.test").stream({}))
        response.close.assert_called_once()
        response = sse_response([{ "choices": [{"delta": {"content": "Часть"}, "finish_reason": None}]}])
        with patch("advent.local.transport.requests.post", return_value=response):
            stream = OllamaTransport("http://ollama.test").stream({})
            next(stream)
            stream.close()
        response.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()

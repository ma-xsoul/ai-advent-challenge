import json
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import requests

from advent.agent.transport import TransportError
from advent.local import OllamaError
from advent.local.optimization import (
    BASELINE, FORMAT_VERSIONS, MODEL, OPTIMIZED, PRECISE_PROMPT, NativeTransport, Profile,
    answer, ask, iter_compare, native_metrics, parse_settings, quote_schema, summarize,
)
from advent.rag.grounded import ANSWER_PROMPT
from advent.rag.index import Hit
from advent.rag.probes import Fact, Probe, REFUSAL


HITS = [Hit(
    rank=n, score=0.8, chunk_id=f"c{n}", source="docs/memory.md", title="Память",
    section="Хранение", kind="doc", start_line=1, end_line=2,
    text="Память хранится в JSON-файле. Агент загружает файл при старте.",
) for n in range(1, 6)]
PROBE = Probe("Где хранится память?", ("docs/memory.md",), "JSON-файл",
              (Fact("JSON", ("json",)), Fact("файл", ("файл",))))
NEGATIVE = Probe("Какая завтра погода?", (), "Не знаю", (REFUSAL,))
GOOD = {"status": "answer", "quotes": [{"n": 1, "text": HITS[0].text}],
        "answer": "Память хранится в JSON-файле [1].", "clarify": ""}
UNKNOWN = {"status": "unknown", "quotes": [], "answer": "Не знаю", "clarify": "Уточните вопрос"}


def native_body(content=GOOD, **changes):
    return {
        "model": MODEL, "message": {"role": "assistant", "content": json.dumps(content, ensure_ascii=False), "thinking": "Проверяю"},
        "done": True, "done_reason": "stop", "load_duration": 1_000_000_000,
        "prompt_eval_count": 30, "prompt_eval_duration": 250_000_000,
        "eval_count": 20, "eval_duration": 2_000_000_000, "total_duration": 3_250_000_000,
        **changes,
    }


class FakeClient:
    url = "http://ollama.test:11434"
    timeout = 15

    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.context = 4096

    def loaded(self):
        self.events.append("memory")
        return [{"name": MODEL, "size": 12_000_000_000 + self.context,
                 "vram": 11_000_000_000 + self.context, "context": self.context}]


class FakeIndex:
    def __init__(self, events):
        self.events = events
        self.calls = []

    def search(self, question, embedder, strategy, *, k):
        self.events.append("retrieval")
        self.calls.append((question, embedder, strategy, k))
        return HITS


class OptimizationTestCase(unittest.TestCase):
    def test_profiles_validate_finite_numbers_integer_bounds_and_context_budget(self):
        self.assertIs(parse_settings(), OPTIMIZED)
        self.assertEqual(BASELINE.as_dict(), {"temperature": 0.1, "max_tokens": 2000,
                                            "num_ctx": 4096, "prompt": "baseline", "seed": 42})
        self.assertEqual(parse_settings({"temperature": 2, "max_tokens": 8192, "num_ctx": 16384}).max_tokens, 8192)
        invalid = [
            [], {"temperature": float("nan")}, {"temperature": float("inf")},
            {"temperature": -0.1}, {"temperature": 2.1}, {"temperature": True}, {"temperature": "0.1"},
            {"max_tokens": True}, {"max_tokens": 255}, {"max_tokens": 8193}, {"max_tokens": 1400.0},
            {"num_ctx": 4095}, {"num_ctx": 16385}, {"num_ctx": False}, {"num_ctx": "8192"},
            {"max_tokens": 4096, "num_ctx": 4096}, {"prompt": "anything"}, {"prompt": []},
            {"seed": 7}, {"seed": True}, {"model": "gpt-oss:120b-cloud"},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_settings(value)

    def test_native_request_applies_profile_without_changing_day28_payload_and_captures_memory(self):
        client = FakeClient()
        transport = NativeTransport(BASELINE, client=client)
        payload = {"model": MODEL, "messages": [{"role": "system", "content": ANSWER_PROMPT},
                                               {"role": "user", "content": "те же фрагменты"}],
                   "temperature": 1.8, "max_tokens": 256, "thinking": {"type": "enabled"}}
        original = json.loads(json.dumps(payload))
        response = Mock(status_code=200, json=lambda: native_body())
        with patch("advent.local.optimization.requests.post", return_value=response) as post:
            result = transport.send(payload)
        self.assertEqual(payload, original)
        self.assertEqual(post.call_args.args, ("http://ollama.test:11434/api/chat",))
        self.assertEqual(post.call_args.kwargs["timeout"], 15)
        sent = post.call_args.kwargs["json"]
        self.assertEqual(sent["options"], {"temperature": 0.1, "num_predict": 2000, "num_ctx": 4096, "seed": 42})
        self.assertEqual(sent["messages"], original["messages"])
        self.assertEqual((sent["model"], sent["think"], sent["format"], sent["stream"]), (MODEL, "low", "json", False))
        self.assertEqual(result["usage"], {"prompt_tokens": 30, "completion_tokens": 20})
        self.assertEqual(result["choices"][0]["message"]["reasoning_content"], "Проверяю")
        self.assertEqual(transport.metrics["generation_tps"], 10.0)
        self.assertEqual(transport.metrics["actual_ctx"], 4096)
        self.assertEqual(transport.memory, {"bytes": 12_000_004_096, "vram_bytes": 11_000_004_096, "context": 4096})
        self.assertEqual(client.events, ["memory"])
        response.close.assert_called_once()

    def test_native_generation_speed_and_load_are_separate_from_wall_time(self):
        measured = native_metrics(native_body(), wall_s=10)
        self.assertEqual((measured["generation_tps"], measured["prompt_tps"]), (10.0, 120.0))
        self.assertEqual((measured["load_s"], measured["total_s"], measured["warm_s"], measured["wall_s"]), (1.0, 3.25, 2.25, 10))
        self.assertIsNone(measured["first_token_s"])
        absent = native_metrics({}, wall_s=1)
        for field in ("load_s", "total_s", "generation_tps", "actual_ctx"):
            self.assertIsNone(absent[field])

    def test_same_top_five_are_sent_sequentially_to_both_profiles_before_next_retrieval(self):
        order = []
        index, client, embedder = FakeIndex(order), FakeClient(order), object()
        calls = []

        def post(url, *, json, timeout):
            calls.append(json)
            order.append("chat")
            client.context = json["options"]["num_ctx"]
            return Mock(status_code=200, json=lambda: native_body())

        emitted = []
        with patch("advent.local.optimization.requests.post", side_effect=post):
            results = ask(PROBE.question, index=index, embedder=embedder, profile=OPTIMIZED,
                          client=client, emit=emitted.append, probe=PROBE)
        self.assertEqual(order, ["retrieval", "chat", "memory", "chat", "memory"])
        self.assertEqual(index.calls, [(PROBE.question, embedder, "structure", 5)])
        self.assertEqual(calls[0]["messages"][1], calls[1]["messages"][1])
        for hit in HITS:
            self.assertIn(f"[{hit.rank}] docs/memory.md", calls[0]["messages"][1]["content"])
        self.assertEqual(calls[0]["messages"][0]["content"], ANSWER_PROMPT)
        self.assertEqual(calls[1]["messages"][0]["content"], PRECISE_PROMPT)
        self.assertEqual(calls[0]["format"], "json")
        self.assertIsInstance(calls[1]["format"], dict)
        self.assertEqual([call["options"]["seed"] for call in calls], [42, 42])
        self.assertEqual([event["type"] for event in emitted], ["retrieved", "answer_start", "answer", "answer_start", "answer"])
        for mode in ("before", "after"):
            self.assertTrue(results[mode]["check"]["passed"])
            self.assertEqual(results[mode]["quotes"][0]["status"], "exact")
            self.assertTrue(results[mode]["context_verified"])
            self.assertEqual(results[mode]["raw_response"], json.dumps(GOOD, ensure_ascii=False))
        self.assertEqual(results["before"]["format_version"], "json")
        self.assertEqual(results["after"]["format_version"], "source_lines_schema-v1")
        self.assertEqual(results["after"]["prompt_text"], PRECISE_PROMPT)
        self.assertEqual(results["before"]["actual_ctx"], 4096)
        self.assertEqual(results["after"]["actual_ctx"], 8192)

    def test_comparison_can_report_regression_with_unchanged_fact_and_refusal_checks(self):
        order = []
        index, client = FakeIndex(order), FakeClient(order)
        bodies = iter((native_body(GOOD), native_body(UNKNOWN), native_body(UNKNOWN), native_body(GOOD)))

        def post(url, *, json, timeout):
            client.context = json["options"]["num_ctx"]
            body = next(bodies)
            return Mock(status_code=200, json=lambda: body)

        with patch("advent.local.optimization.requests.post", side_effect=post):
            events = list(iter_compare(index=index, embedder=object(), profile=OPTIMIZED, client=client,
                                       probes=(PROBE, NEGATIVE)))
        rows = [event for event in events if event["type"] == "compare_row"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["modes"]["before"]["check"]["passed"])
        self.assertFalse(rows[0]["modes"]["after"]["check"]["passed"])
        summary = events[-1]["summary"]
        self.assertEqual((summary["before"]["passed"], summary["after"]["passed"]), (1, 0))
        self.assertEqual((summary["before"]["refused"], summary["after"]["refused"]), (1, 0))
        self.assertEqual(summary["after"]["false_unknown"], 1)
        self.assertEqual((summary["before"]["facts_found"], summary["before"]["facts_total"]), (2, 2))
        self.assertEqual((summary["after"]["facts_found"], summary["after"]["facts_total"]), (0, 2))
        self.assertEqual((summary["before"]["quotes_found"], summary["before"]["quotes_total"]), (1, 1))
        self.assertEqual((summary["after"]["quotes_found"], summary["after"]["quotes_total"]), (0, 0))
        self.assertEqual(summary["after"]["generation_tps"], 10.0)
        self.assertEqual(summary["after"]["load_s"], 1.0)
        self.assertEqual(summary["after"]["memory_bytes"], 12_000_008_192)
        self.assertEqual(summary["after"]["memory_aggregation"], "max")
        self.assertEqual(summary["after"]["memory_samples"], 2)
        self.assertEqual(summary["before"]["actual_ctx"], 4096)
        self.assertGreaterEqual(events[-1]["elapsed_s"], 0)
        self.assertEqual(order, ["retrieval", "memory", "memory", "retrieval", "memory", "memory"])

    def test_broken_json_and_truncated_answer_are_visible_without_changing_evaluator(self):
        client = FakeClient()
        body = native_body(done_reason="length")
        body["message"]["content"] = '{"quotes":'
        with patch("advent.local.optimization.requests.post", return_value=Mock(status_code=200, json=lambda: body)):
            result = answer(PROBE.question, HITS, mode="after", profile=OPTIMIZED, client=client, probe=PROBE)
        self.assertFalse(result["parsed"])
        self.assertFalse(result["check"]["passed"])
        self.assertEqual(result["finish_reason"], "length")

    def test_missing_memory_sample_never_invents_memory_or_context(self):
        client = FakeClient()
        client.loaded = Mock(side_effect=OllamaError("ps unavailable"))
        with patch("advent.local.optimization.requests.post", return_value=Mock(status_code=200, json=lambda: native_body())):
            result = answer(PROBE.question, HITS, mode="after", profile=OPTIMIZED, client=client, probe=PROBE)
        self.assertTrue(result["check"]["passed"])
        self.assertIsNone(result["memory"])
        self.assertIsNone(result["actual_ctx"])
        self.assertIsNone(result["context_verified"])
        self.assertEqual(result["memory_error"], "ps unavailable")

    def test_invalid_json_structure_does_not_abort_the_run_or_hide_native_metrics(self):
        body = native_body({"status": "answer", "quotes": 5, "answer": "Ответ"})
        with patch("advent.local.optimization.requests.post", return_value=Mock(status_code=200, json=lambda: body)):
            result = answer(PROBE.question, HITS, mode="after", profile=OPTIMIZED, client=FakeClient(), probe=PROBE)
        self.assertIn("неверную структуру JSON", result["format_error"])
        self.assertNotIn("error", result)
        self.assertFalse(result["parsed"])
        self.assertFalse(result["check"]["passed"])
        self.assertEqual(result["generation_tps"], 10.0)
        self.assertIn('"quotes": 5', result["raw_answer"])
        self.assertIsNotNone(result["memory"])
        summary = summarize([{"negative": False, "modes": {"before": result, "after": result}}])
        self.assertEqual(summary["after"]["errors"], 0)
        self.assertEqual(summary["after"]["format_errors"], 1)
        self.assertEqual(summary["after"]["broken_json"], 1)

    def test_guided_schema_binds_each_quote_to_real_complete_lines_of_its_source(self):
        first = replace(HITS[0], rank=7, text='  first line  \n\nsecond "quoted" line\n' + 'x' * 251)
        second = replace(HITS[1], rank=3, text="another source\n  Русская строка  ")
        schema = quote_schema([first, second])
        self.assertEqual(schema["required"], ["status", "quotes", "answer", "clarify"])
        self.assertEqual(schema["properties"]["status"]["enum"], ["answer", "unknown"])
        self.assertEqual(schema["properties"]["quotes"]["maxItems"], 4)
        variants = schema["properties"]["quotes"]["items"]["anyOf"]
        self.assertEqual(len(variants), 2)
        by_rank = {hit.rank: hit for hit in (first, second)}
        for variant in variants:
            rank = variant["properties"]["n"]["const"]
            self.assertEqual(variant["required"], ["n", "text"])
            self.assertFalse(variant["additionalProperties"])
            for line in variant["properties"]["text"]["enum"]:
                self.assertIn(line, by_rank[rank].text)
                self.assertTrue(line.strip())
                self.assertLessEqual(len(line), 250)
        self.assertEqual(variants[0]["properties"]["text"]["enum"], ["first line", 'second "quoted" line'])
        self.assertEqual(variants[1]["properties"]["text"]["enum"], ["another source", "Русская строка"])
        self.assertEqual(FORMAT_VERSIONS, {"baseline": "json", "precise": "source_lines_schema-v1"})

    def test_guided_schema_without_source_lines_allows_only_unknown_with_empty_quotes(self):
        for hits in ([], [replace(HITS[0], text="\n  \n" + "x" * 251)]):
            schema = quote_schema(hits)
            self.assertEqual(schema["properties"]["status"]["enum"], ["unknown"])
            self.assertEqual(schema["properties"]["quotes"]["maxItems"], 0)
        self.assertEqual(NativeTransport(OPTIMIZED, client=FakeClient()).response_format, quote_schema([]))

    def test_answer_latency_excludes_memory_polling_and_reports_sampling_separately(self):
        clock = [0.0]
        client = FakeClient()
        original_loaded = client.loaded

        def loaded():
            clock[0] += 5
            return original_loaded()

        def post(url, *, json, timeout):
            clock[0] += 2
            return Mock(status_code=200, json=lambda: native_body())

        client.loaded = loaded
        with patch("advent.local.optimization.time.perf_counter", side_effect=lambda: clock[0]), patch(
            "advent.local.optimization.requests.post", side_effect=post
        ):
            result = answer(PROBE.question, HITS, mode="before", profile=BASELINE, client=client, probe=PROBE)
        self.assertEqual(result["ms"], 2000)
        self.assertEqual(result["wall_s"], 2.0)
        self.assertEqual(result["memory_sample_s"], 5.0)
        self.assertEqual(result["wall_tps"], 10.0)
        summary = summarize([{"negative": False, "modes": {"before": result, "after": result}}])
        self.assertEqual(summary["before"]["avg_s"], 2.0)
        self.assertEqual(summary["before"]["memory_sample_s"], 5.0)

    def test_native_transport_rejects_other_models_and_closes_error_responses(self):
        client = FakeClient()
        with patch("advent.local.optimization.requests.post") as post:
            with self.assertRaisesRegex(TransportError, "только скачанную"):
                NativeTransport(BASELINE, client=client).send({"model": "gpt-oss:120b-cloud"})
            post.assert_not_called()
        cases = [
            Mock(status_code=500, json=lambda: {"error": "out of memory"}),
            Mock(status_code=200, json=Mock(side_effect=ValueError("bad JSON"))),
            Mock(status_code=200, json=lambda: native_body(done=False)),
            Mock(status_code=200, json=lambda: native_body(message={})),
        ]
        for response in cases:
            with self.subTest(response=response), patch("advent.local.optimization.requests.post", return_value=response):
                with self.assertRaises(TransportError):
                    NativeTransport(BASELINE, client=client).send({})
                response.close.assert_called_once()
        self.assertEqual(client.events, [])
        with patch("advent.local.optimization.requests.post", side_effect=requests.Timeout("timeout")):
            with self.assertRaisesRegex(TransportError, "недоступна"):
                NativeTransport(BASELINE, client=client).send({})


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from advent.agent import Agent, AgentConfig, TransportError


class FakeTransport:
    """Заглушка вместо сети: запоминает payload и отдаёт заготовленные ответы."""

    def __init__(self, replies=None, error=None):
        self.payloads = []
        self._replies = list(replies or [])
        self._error = error

    def send(self, payload):
        self.payloads.append(payload)
        if self._error is not None:
            raise self._error
        content = self._replies.pop(0) if self._replies else "ок"
        return {
            "model": "deepseek-v4-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    def stream(self, payload):
        self.payloads.append(payload)
        if self._error is not None:
            raise self._error
        content = self._replies.pop(0) if self._replies else "ок"
        # Режем ответ на куски по два символа, как настоящий поток.
        for i in range(0, len(content), 2):
            yield {"choices": [{"delta": {"content": content[i : i + 2]}}]}
        yield {
            "model": "deepseek-v4-flash",
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }


class MeasuringTransport(FakeTransport):
    """Та же заглушка, но prompt_tokens считаются по символам payload — как у живого API."""

    def _usage(self, payload, content):
        from advent.agent.tokens import estimate_message_tokens, estimate_tokens

        return {
            "prompt_tokens": sum(estimate_message_tokens(m["content"]) for m in payload["messages"]),
            "completion_tokens": estimate_tokens(content),
        }

    def send(self, payload):
        response = super().send(payload)
        response["usage"] = self._usage(payload, response["choices"][0]["message"]["content"])
        return response

    def stream(self, payload):
        chunks = list(super().stream(payload))
        content = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
        chunks[-1]["usage"] = self._usage(payload, content)
        yield from chunks


class AgentConfigTestCase(unittest.TestCase):
    def test_defaults_match_the_project_conventions(self):
        config = AgentConfig()

        self.assertEqual(config.model, "deepseek-v4-flash")
        self.assertFalse(config.thinking)
        self.assertIsNone(config.system_prompt)

    def test_rejects_a_temperature_outside_the_api_range(self):
        for value in (-0.1, 2.1):
            with self.subTest(temperature=value):
                with self.assertRaises(ValueError):
                    AgentConfig(temperature=value)

    def test_rejects_a_blank_model_and_a_non_positive_token_limit(self):
        with self.assertRaises(ValueError):
            AgentConfig(model="   ")
        with self.assertRaises(ValueError):
            AgentConfig(max_tokens=0)

    def test_is_frozen(self):
        config = AgentConfig()

        with self.assertRaises(Exception):
            config.temperature = 0.5


class AgentTestCase(unittest.TestCase):
    def test_sends_the_system_prompt_and_the_user_message(self):
        transport = FakeTransport()
        agent = Agent(AgentConfig(system_prompt="Отвечай кратко."), transport)

        agent.ask("Почему небо голубое?")

        self.assertEqual(
            transport.payloads[0]["messages"],
            [
                {"role": "system", "content": "Отвечай кратко."},
                {"role": "user", "content": "Почему небо голубое?"},
            ],
        )

    def test_remembers_the_conversation_between_calls(self):
        """Главное отличие от первых дней: второй запрос уходит вместе с первым."""
        transport = FakeTransport(replies=["Из-за рассеяния света.", "Рэлеевское рассеяние."])
        agent = Agent(AgentConfig(), transport)

        agent.ask("Почему небо голубое?")
        agent.ask("А теперь короче.")

        self.assertEqual(
            transport.payloads[1]["messages"],
            [
                {"role": "user", "content": "Почему небо голубое?"},
                {"role": "assistant", "content": "Из-за рассеяния света."},
                {"role": "user", "content": "А теперь короче."},
            ],
        )
        self.assertEqual([message.role for message in agent.history], ["user", "assistant", "user", "assistant"])

    def test_builds_the_payload_from_its_config(self):
        transport = FakeTransport()
        agent = Agent(
            AgentConfig(model="deepseek-v4-pro", temperature=0.2, max_tokens=300, thinking=True),
            transport,
        )

        agent.ask("Привет")
        payload = transport.payloads[0]

        self.assertEqual(payload["model"], "deepseek-v4-pro")
        self.assertEqual(payload["temperature"], 0.2)
        self.assertEqual(payload["max_tokens"], 300)
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertFalse(payload["stream"])

    def test_disables_thinking_by_default(self):
        transport = FakeTransport()

        Agent(AgentConfig(), transport).ask("Привет")

        self.assertEqual(transport.payloads[0]["thinking"], {"type": "disabled"})

    def test_returns_the_reply_with_its_cost(self):
        agent = Agent(AgentConfig(), FakeTransport(replies=["Ответ"]))

        reply = agent.ask("Вопрос")

        self.assertEqual(reply.content, "Ответ")
        self.assertEqual(reply.finish_reason, "stop")
        self.assertEqual(reply.model, "deepseek-v4-flash")
        self.assertEqual((reply.input_tokens, reply.output_tokens), (10, 5))
        self.assertIsNone(reply.reasoning)

    def test_accumulates_usage_across_the_dialogue(self):
        agent = Agent(AgentConfig(), FakeTransport())

        agent.ask("Раз")
        agent.ask("Два")
        agent.ask("Три")

        self.assertEqual(agent.usage.requests, 3)
        self.assertEqual(agent.usage.input_tokens, 30)
        self.assertEqual(agent.usage.output_tokens, 15)
        self.assertEqual(agent.usage.total_tokens, 45)

    def test_rolls_back_the_unanswered_message_when_the_transport_fails(self):
        """После ошибки стек не должен содержать user-сообщение без пары."""
        transport = FakeTransport(error=TransportError("сеть упала"))
        agent = Agent(AgentConfig(), transport)

        with self.assertRaises(TransportError):
            agent.ask("Вопрос")

        self.assertEqual(agent.history, ())
        self.assertEqual(agent.usage.requests, 0)

    def test_rolls_back_when_the_response_has_no_text(self):
        class EmptyTransport:
            def send(self, payload):
                return {"choices": [{"message": {"content": None}}]}

        agent = Agent(AgentConfig(), EmptyTransport())

        with self.assertRaises(TransportError):
            agent.ask("Вопрос")

        self.assertEqual(agent.history, ())

    def test_rejects_an_empty_question_without_touching_the_transport(self):
        transport = FakeTransport()
        agent = Agent(AgentConfig(), transport)

        with self.assertRaises(ValueError):
            agent.ask("   ")

        self.assertEqual(transport.payloads, [])
        self.assertEqual(agent.history, ())

    def test_reset_clears_the_stack_and_the_usage_but_keeps_the_config(self):
        config = AgentConfig(system_prompt="Ты помощник.")
        agent = Agent(config, FakeTransport())
        agent.ask("Раз")

        agent.reset()

        self.assertEqual(agent.history, ())
        self.assertEqual(agent.usage.requests, 0)
        self.assertIs(agent.config, config)

    def test_snapshot_exposes_debug_data_as_plain_json(self):
        agent = Agent(AgentConfig(name="demo", temperature=0.4), FakeTransport(replies=["Ответ"]))
        agent.ask("Вопрос")

        snapshot = agent.snapshot()

        self.assertEqual(snapshot["name"], "demo")
        self.assertEqual(snapshot["config"]["temperature"], 0.4)
        self.assertEqual(
            snapshot["messages"],
            [{"role": "user", "content": "Вопрос"}, {"role": "assistant", "content": "Ответ"}],
        )
        self.assertEqual(snapshot["usage"]["total_tokens"], 15)

    def test_two_agents_do_not_share_state(self):
        """Агент — экземпляр, а не модуль: под будущих суб-агентов."""
        first = Agent(AgentConfig(name="first"), FakeTransport())
        second = Agent(AgentConfig(name="second"), FakeTransport())

        first.ask("Только первому")

        self.assertEqual(len(first.history), 2)
        self.assertEqual(second.history, ())
        self.assertEqual(second.usage.requests, 0)

    def test_exposes_reasoning_when_the_model_returns_it(self):
        class ReasoningTransport:
            def send(self, payload):
                return {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": "5/14", "reasoning_content": "Считаем…"},
                        }
                    ],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 40},
                }

        reply = Agent(AgentConfig(thinking=True), ReasoningTransport()).ask("Вероятность?")

        self.assertEqual(reply.content, "5/14")
        self.assertEqual(reply.reasoning, "Считаем…")


class AgentStreamTestCase(unittest.TestCase):
    def _drain(self, generator):
        chunks = []
        while True:
            try:
                chunks.append(next(generator))
            except StopIteration as stop:
                return chunks, stop.value

    def test_yields_chunks_and_returns_the_full_reply(self):
        agent = Agent(AgentConfig(), FakeTransport(replies=["Ответ агента"]))

        chunks, reply = self._drain(agent.stream("Вопрос"))

        self.assertEqual("".join(chunks), "Ответ агента")
        self.assertEqual(reply.content, "Ответ агента")
        self.assertEqual(reply.finish_reason, "stop")
        self.assertEqual((reply.input_tokens, reply.output_tokens), (10, 5))

    def test_asks_the_transport_for_a_stream_with_the_full_stack(self):
        transport = FakeTransport(replies=["Раз", "Два"])
        agent = Agent(AgentConfig(system_prompt="Кратко."), transport)

        self._drain(agent.stream("Первый"))
        self._drain(agent.stream("Второй"))

        self.assertTrue(transport.payloads[1]["stream"])
        self.assertEqual(
            [m["role"] for m in transport.payloads[1]["messages"]],
            ["system", "user", "assistant", "user"],
        )

    def test_updates_the_stack_and_usage_only_after_the_stream_ends(self):
        agent = Agent(AgentConfig(), FakeTransport(replies=["Ответ"]))
        generator = agent.stream("Вопрос")

        next(generator)
        self.assertEqual([m.role for m in agent.history], ["user"])
        self.assertEqual(agent.usage.requests, 0)

        self._drain(generator)
        self.assertEqual([m.role for m in agent.history], ["user", "assistant"])
        self.assertEqual(agent.usage.requests, 1)

    def test_rolls_back_the_user_message_when_the_stream_fails(self):
        agent = Agent(AgentConfig(), FakeTransport(error=TransportError("обрыв")))

        with self.assertRaises(TransportError):
            self._drain(agent.stream("Вопрос"))

        self.assertEqual(agent.history, ())

    def test_closing_partial_stream_preserves_previous_history_and_usage(self):
        agent = Agent(AgentConfig(), FakeTransport(replies=["Сохранённый ответ", "Незавершённый ответ"]))
        agent.ask("Первый вопрос")
        before = agent.snapshot()
        generator = agent.stream("Второй вопрос")
        next(generator)
        generator.close()
        self.assertEqual(agent.snapshot(), before)

    def test_rolls_back_when_the_stream_carries_no_text(self):
        class SilentTransport:
            def stream(self, payload):
                yield {"choices": [{"delta": {"reasoning_content": "думаю"}}]}
                yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

        agent = Agent(AgentConfig(), SilentTransport())

        with self.assertRaises(TransportError):
            self._drain(agent.stream("Вопрос"))

        self.assertEqual(agent.history, ())


class AgentRegistryTestCase(unittest.TestCase):
    def test_creates_one_agent_per_session_and_reuses_it(self):
        from advent.agent import AgentRegistry

        registry = AgentRegistry(lambda saved: Agent(AgentConfig(), FakeTransport()))

        first = registry.get("s1")
        again = registry.get("s1")
        other = registry.get("s2")

        self.assertIs(first, again)
        self.assertIsNot(first, other)
        self.assertEqual(len(registry), 2)
        self.assertIn("s1", registry)

    def test_peek_does_not_register_an_unknown_session(self):
        from advent.agent import AgentRegistry

        registry = AgentRegistry(lambda saved: Agent(AgentConfig(), FakeTransport()))

        ghost = registry.peek("probe")

        self.assertEqual(ghost.history, ())
        self.assertEqual(len(registry), 0)
        self.assertIsNot(registry.peek("probe"), ghost)

    def test_peek_returns_the_registered_agent_when_there_is_one(self):
        from advent.agent import AgentRegistry

        registry = AgentRegistry(lambda saved: Agent(AgentConfig(), FakeTransport()))
        real = registry.get("s1")

        self.assertIs(registry.peek("s1"), real)
        self.assertEqual(len(registry), 1)

    def test_reset_replaces_the_agent_and_drops_its_stack(self):
        from advent.agent import AgentRegistry

        registry = AgentRegistry(lambda saved: Agent(AgentConfig(), FakeTransport()))
        old = registry.get("s1")
        old.ask("Вопрос")

        fresh = registry.reset("s1")

        self.assertIsNot(fresh, old)
        self.assertEqual(fresh.history, ())
        self.assertIs(registry.get("s1"), fresh)


class AgentRestoreTestCase(unittest.TestCase):
    def test_restore_is_the_inverse_of_snapshot(self):
        agent = Agent(AgentConfig(name="demo", temperature=0.3), FakeTransport(replies=["Ответ"]))
        agent.ask("Вопрос")

        restored = Agent.restore(agent.snapshot(), FakeTransport())

        self.assertEqual(restored.config, agent.config)
        self.assertEqual(restored.history, agent.history)
        self.assertEqual(restored.usage.total_tokens, agent.usage.total_tokens)

    def test_restored_agent_continues_the_dialogue(self):
        transport = FakeTransport(replies=["Дальше"])
        agent = Agent(AgentConfig(), FakeTransport(replies=["Раз"]))
        agent.ask("Первый")

        restored = Agent.restore(agent.snapshot(), transport)
        restored.ask("Второй")

        self.assertEqual(
            [m["role"] for m in transport.payloads[0]["messages"]],
            ["user", "assistant", "user"],
        )

    def test_rejects_a_snapshot_with_a_broken_stack(self):
        with self.assertRaises(ValueError):
            Agent.restore({"config": {}, "messages": [{"role": "system", "content": "x"}]})
        with self.assertRaises(ValueError):
            Agent.restore({"messages": []})


class JsonStoreTestCase(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        from advent.agent import JsonStore

        self.store = JsonStore(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_then_load_round_trips_the_snapshot(self):
        agent = Agent(AgentConfig(name="demo"), FakeTransport(replies=["Ответ"]))
        agent.ask("Вопрос")

        path = self.store.save("s1", agent)
        data = self.store.load("s1")

        self.assertTrue(path.exists())
        self.assertEqual(data["session_id"], "s1")
        self.assertEqual(data["version"], 1)
        self.assertIn("saved_at", data)
        self.assertEqual([m["content"] for m in data["messages"]], ["Вопрос", "Ответ"])
        self.assertEqual(data["config"]["name"], "demo")

    def test_save_leaves_no_temporary_file_behind(self):
        self.store.save("s1", Agent(AgentConfig(), FakeTransport()))

        from pathlib import Path

        leftovers = list(Path(self._tmp.name).glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_load_returns_none_for_an_unknown_session(self):
        self.assertIsNone(self.store.load("nope"))
        self.assertIsNone(self.store.describe("nope"))

    def test_load_raises_on_a_corrupt_file(self):
        from pathlib import Path
        from advent.agent import StoreError

        Path(self._tmp.name, "bad.json").write_text("{не json", encoding="utf-8")

        with self.assertRaises(StoreError):
            self.store.load("bad")

    def test_delete_removes_the_file(self):
        self.store.save("s1", Agent(AgentConfig(), FakeTransport()))

        self.assertTrue(self.store.delete("s1"))
        self.assertIsNone(self.store.load("s1"))
        self.assertFalse(self.store.delete("s1"))

    def test_refuses_unsafe_session_ids(self):
        from advent.agent import StoreError

        with self.assertRaises(StoreError):
            self.store.load("../etc/passwd")

    def test_describe_reports_the_file_without_the_agent(self):
        agent = Agent(AgentConfig(), FakeTransport(replies=["Ответ"]))
        agent.ask("Вопрос")
        self.store.save("s1", agent)

        info = self.store.describe("s1")

        self.assertEqual(info.messages, 2)
        self.assertTrue(info.path.endswith("s1.json"))
        self.assertGreater(info.bytes, 0)
        self.assertIsNotNone(info.saved_at)


class PersistentRegistryTestCase(unittest.TestCase):
    def setUp(self):
        import tempfile
        from advent.agent import AgentRegistry, JsonStore

        self._tmp = tempfile.TemporaryDirectory()
        self.replies = ["Раз", "Два", "Три"]

        def factory(saved):
            transport = FakeTransport(replies=list(self.replies))
            if saved:
                return Agent.restore(saved, transport)
            return Agent(AgentConfig(name="persist"), transport)

        self.registry = AgentRegistry(factory, store=JsonStore(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_survives_eviction_by_reading_the_disk(self):
        """Имитация перезапуска: агента нет в памяти, но есть файл."""
        agent = self.registry.get("s1")
        agent.ask("Первый")
        self.registry.save("s1")

        self.assertTrue(self.registry.evict("s1"))
        self.assertNotIn("s1", self.registry)

        restored = self.registry.get("s1")

        self.assertIsNot(restored, agent)
        self.assertEqual([m.content for m in restored.history], ["Первый", "Раз"])
        self.assertEqual(restored.usage.requests, 1)

    def test_peek_restores_from_disk_but_does_not_invent_sessions(self):
        self.registry.get("s1").ask("Первый")
        self.registry.save("s1")
        self.registry.evict("s1")

        restored = self.registry.peek("s1")
        ghost = self.registry.peek("nobody")

        self.assertEqual([m.content for m in restored.history], ["Первый", "Раз"])
        self.assertIn("s1", self.registry)
        self.assertEqual(ghost.history, ())
        self.assertNotIn("nobody", self.registry)

    def test_get_without_a_file_creates_a_fresh_agent(self):
        agent = self.registry.get("new")

        self.assertEqual(agent.history, ())
        self.assertEqual(agent.config.name, "persist")

    def test_reset_deletes_the_file_too(self):
        self.registry.get("s1").ask("Вопрос")
        self.registry.save("s1")
        self.assertIsNotNone(self.registry.stored("s1"))

        self.registry.reset("s1")

        self.assertIsNone(self.registry.stored("s1"))
        self.registry.evict("s1")
        self.assertEqual(self.registry.get("s1").history, ())

    def test_save_without_an_agent_in_memory_is_a_noop(self):
        self.registry.save("ghost")

        self.assertIsNone(self.registry.stored("ghost"))

    def test_memory_only_registry_ignores_save_and_evict_restores_nothing(self):
        from advent.agent import AgentRegistry

        registry = AgentRegistry(lambda saved: Agent(AgentConfig(), FakeTransport()))
        registry.get("s1").ask("Вопрос")
        registry.save("s1")
        registry.evict("s1")

        self.assertFalse(registry.persistent)
        self.assertEqual(registry.get("s1").history, ())


class TokenAccountingTestCase(unittest.TestCase):
    def test_estimate_counts_cyrillic_denser_than_latin(self):
        from advent.agent import estimate_tokens

        self.assertEqual(estimate_tokens(""), 0)
        self.assertGreater(estimate_tokens("Привет, мир"), estimate_tokens("Hi, world"))
        self.assertGreaterEqual(estimate_tokens("а"), 1)

    def test_each_reply_records_a_turn_with_the_three_numbers(self):
        agent = Agent(AgentConfig(), FakeTransport(replies=["Ответ"]))

        agent.ask("Почему небо голубое?")
        turn = agent.turns[0]

        self.assertEqual(turn.index, 1)
        self.assertGreater(turn.question_tokens, 0)
        self.assertEqual(turn.context_tokens, 10)
        self.assertEqual(turn.answer_tokens, 5)
        self.assertEqual(turn.total_tokens, 15)

    def test_turns_accumulate_and_survive_snapshot_restore(self):
        agent = Agent(AgentConfig(), FakeTransport())
        agent.ask("Раз")
        agent.ask("Два")

        restored = Agent.restore(agent.snapshot(), FakeTransport())

        self.assertEqual([t.index for t in restored.turns], [1, 2])
        self.assertEqual(restored.turns[1].total_tokens, 30)

    def test_stream_records_turns_too(self):
        agent = Agent(AgentConfig(), FakeTransport(replies=["Ответ"]))
        generator = agent.stream("Вопрос")
        while True:
            try:
                next(generator)
            except StopIteration:
                break

        self.assertEqual(len(agent.turns), 1)
        self.assertEqual(agent.turns[0].context_tokens, 10)

    def test_estimate_context_uses_api_numbers_after_the_first_turn(self):
        agent = Agent(AgentConfig(system_prompt="Кратко."), FakeTransport())
        before = agent.estimate_context("Вопрос")
        agent.ask("Вопрос")

        after = agent.estimate_context()

        self.assertGreater(before, 0)
        # Прошлый контекст (10) плюс прошлый ответ (5): точные числа из API.
        self.assertEqual(after, 15)

    def test_reset_clears_turns(self):
        agent = Agent(AgentConfig(), FakeTransport())
        agent.ask("Раз")

        agent.reset()

        self.assertEqual(agent.turns, ())


class ContextLimitTestCase(unittest.TestCase):
    def test_no_limit_by_default(self):
        self.assertIsNone(AgentConfig().context_limit)
        with self.assertRaises(ValueError):
            AgentConfig(context_limit=0)

    def test_refuses_before_sending_when_the_stack_would_overflow(self):
        from advent.agent import ContextOverflowError

        transport = FakeTransport()
        agent = Agent(AgentConfig(context_limit=20), transport)
        agent.ask("Раз")

        with self.assertRaises(ContextOverflowError) as raised:
            agent.ask("Очень длинный вопрос, который заведомо не поместится в двадцать токенов")

        self.assertEqual(raised.exception.limit, 20)
        self.assertGreater(raised.exception.estimated, 20)
        # Запрос не ушёл и стек не тронут.
        self.assertEqual(len(transport.payloads), 1)
        self.assertEqual([m.role for m in agent.history], ["user", "assistant"])

    def test_stream_checks_the_limit_as_well(self):
        from advent.agent import ContextOverflowError

        agent = Agent(AgentConfig(context_limit=5), FakeTransport())

        with self.assertRaises(ContextOverflowError):
            next(agent.stream("Вопрос длиннее пяти токенов, определённо"))

        self.assertEqual(agent.history, ())

    def test_with_config_keeps_the_dialogue_and_changes_the_limit(self):
        agent = Agent(AgentConfig(context_limit=20), FakeTransport(replies=["Ответ"]))
        agent.ask("Раз")

        relaxed = agent.with_config(context_limit=None)

        self.assertIsNone(relaxed.config.context_limit)
        self.assertEqual(relaxed.history, agent.history)
        self.assertEqual(relaxed.turns, agent.turns)
        self.assertEqual(relaxed.usage.total_tokens, agent.usage.total_tokens)
        self.assertIs(relaxed._transport, agent._transport)

    def test_snapshot_reports_the_context_estimate_and_limit(self):
        agent = Agent(AgentConfig(context_limit=500), FakeTransport())
        agent.ask("Раз")

        context = agent.snapshot()["context"]

        self.assertEqual(context, {"estimated": 15, "limit": 500, "messages": 2, "summarized": 0, "dropped": 0})


class ScenarioTestCase(unittest.TestCase):
    def test_spec_scenario_has_a_dozen_turns_ending_with_a_recall_question(self):
        from advent.agent import get_scenario

        scenario = get_scenario("spec")

        self.assertEqual(len(scenario.messages), 12)
        self.assertIn("Ничего не упусти", scenario.messages[-1])
        self.assertIsNone(get_scenario("nope"))


class PersistentRegistryReconfigureTestCase(unittest.TestCase):
    def test_reconfigure_swaps_the_agent_but_keeps_its_stack(self):
        from advent.agent import AgentRegistry

        registry = AgentRegistry(lambda saved: Agent(AgentConfig(), FakeTransport()) if not saved else Agent.restore(saved, FakeTransport()))
        old = registry.get("s1")
        old.ask("Раз")

        new = registry.reconfigure("s1", context_limit=300)

        self.assertIsNot(new, old)
        self.assertEqual(new.config.context_limit, 300)
        self.assertEqual(new.history, old.history)
        self.assertIs(registry.get("s1"), new)


class CompressionPolicyTestCase(unittest.TestCase):
    def test_defaults_keep_four_and_compress_every_ten(self):
        from advent.agent import CompressionPolicy

        policy = CompressionPolicy()

        self.assertEqual((policy.keep_last, policy.every), (4, 10))
        self.assertIsNone(AgentConfig().compression)

    def test_rejects_a_policy_that_could_never_compress(self):
        from advent.agent import CompressionPolicy

        for bad in ({"keep_last": -1}, {"every": 1}, {"keep_last": 10, "every": 10}, {"summary_max_tokens": 0}):
            with self.subTest(**bad):
                with self.assertRaises(ValueError):
                    CompressionPolicy(**bad)

    def test_config_rebuilds_the_policy_from_a_plain_dict(self):
        """Из файла на диске политика приходит словарём — как её положил asdict()."""
        from advent.agent import CompressionPolicy

        config = AgentConfig(compression={"keep_last": 2, "every": 6, "summary_max_tokens": 100})

        self.assertEqual(config.compression, CompressionPolicy(keep_last=2, every=6, summary_max_tokens=100))
        with self.assertRaises(ValueError):
            AgentConfig(compression="каждые десять")


def _drive(generator):
    """Прокрутить поток агента и вернуть события не-текст плюс результат."""
    events = []
    while True:
        try:
            chunk = next(generator)
        except StopIteration as stop:
            return events, stop.value
        if not isinstance(chunk, str):
            events.append(chunk)


class CompressionTestCase(unittest.TestCase):
    """День 9: конспект вместо старых сообщений."""

    def _agent(self, transport, **policy):
        from advent.agent import CompressionPolicy

        return Agent(
            AgentConfig(system_prompt="Кратко.", compression=CompressionPolicy(**policy)),
            transport,
        )

    def test_compresses_when_the_context_reaches_every_messages(self):
        from advent.agent import CompressionRecord

        transport = FakeTransport(replies=["а1", "а2", "а3", "КОНСПЕКТ: три факта", "а4"])
        agent = self._agent(transport, keep_last=2, every=6)

        agent.ask("в1")
        agent.ask("в2")
        self.assertIsNone(agent.summary)
        agent.ask("в3")  # шесть сообщений в контексте — пора

        self.assertEqual(agent.summary.text, "КОНСПЕКТ: три факта")
        self.assertEqual(agent.summary.covers, 4)
        self.assertEqual(agent.summary.made_after_turn, 3)
        # Память цела: все шесть сообщений на месте.
        self.assertEqual(len(agent.history), 6)
        # В модель уходят только последние два.
        self.assertEqual([m.content for m in agent.context_messages], ["в3", "а3"])
        record = agent.compressions[0]
        self.assertIsInstance(record, CompressionRecord)
        self.assertEqual((record.reason, record.compressed, record.covers, record.kept), ("every", 4, 4, 2))
        self.assertEqual(record.after_turn, 3)

        # Следующий ход: системный промпт с конспектом плюс хвост плюс вопрос.
        agent.ask("в4")
        messages = transport.payloads[-1]["messages"]
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user"])
        self.assertIn("Кратко.", messages[0]["content"])
        self.assertIn("КОНСПЕКТ: три факта", messages[0]["content"])
        self.assertIn("4 сообщений", messages[0]["content"])
        self.assertEqual([m["content"] for m in messages[1:]], ["в3", "а3", "в4"])

    def test_summary_request_goes_through_the_same_transport_and_counts_in_usage(self):
        transport = FakeTransport(replies=["а1", "а2", "а3", "конспект"])
        agent = self._agent(transport, keep_last=2, every=6)

        for i in range(3):
            agent.ask(f"в{i}")

        summary_payload = transport.payloads[3]
        self.assertFalse(summary_payload["stream"])
        self.assertEqual(summary_payload["messages"][0]["role"], "system")
        self.assertIn("конспект", summary_payload["messages"][0]["content"].lower())
        self.assertIn("Пользователь: в0", summary_payload["messages"][1]["content"])
        self.assertIn("Ассистент: а2", summary_payload["messages"][1]["content"])
        self.assertNotIn("Пользователь: в2", summary_payload["messages"][1]["content"])
        self.assertEqual(summary_payload["thinking"], {"type": "disabled"})
        # Четыре запроса: три хода и конспект. Расход — за все четыре.
        self.assertEqual(agent.usage.requests, 4)
        self.assertEqual(agent.usage.total_tokens, 4 * 15)

    def test_second_compression_merges_the_previous_summary(self):
        """Счётчик every идёт от прошлого сжатия: шесть сообщений — конспект, ещё шесть — следующий."""
        transport = FakeTransport(replies=["а1", "а2", "а3", "конспект-1", "а4", "а5", "а6", "конспект-2"])
        agent = self._agent(transport, keep_last=2, every=6)

        for i in range(5):
            agent.ask(f"в{i}")
        self.assertEqual(agent.summary.text, "конспект-1")
        agent.ask("в5")

        self.assertEqual(agent.summary.text, "конспект-2")
        self.assertEqual(agent.summary.covers, 10)
        self.assertEqual(agent.summary.stack_size, 12)
        self.assertEqual(len(agent.compressions), 2)
        second_request = transport.payloads[-1]["messages"][1]["content"]
        self.assertIn("Прошлый конспект:\nконспект-1", second_request)
        self.assertIn("Пользователь: в2", second_request)
        self.assertIn("Пользователь: в4", second_request)
        self.assertNotIn("Пользователь: в5", second_request)
        self.assertNotIn("Пользователь: в1", second_request)

    def test_manual_compression_moves_the_counter(self):
        transport = FakeTransport(replies=["а1", "конспект", "а2", "а3", "а4"])
        agent = self._agent(transport, keep_last=0, every=6)
        agent.ask("в1")
        agent.compress()

        agent.ask("в2")
        agent.ask("в3")
        # Без ручного сжатия шесть сообщений уже сработали бы; счётчик сдвинут — ещё нет.
        self.assertEqual(len(agent.compressions), 1)
        agent.ask("в4")
        # Шесть сообщений после ручного сжатия — теперь пора.
        self.assertEqual(len(agent.compressions), 2)
        self.assertEqual(len(agent.history), 8)

    def test_truncated_summary_is_flagged(self):
        class ChoppingTransport(FakeTransport):
            def send(self, payload):
                response = super().send(payload)
                response["choices"][0]["finish_reason"] = "length"
                return response

        transport = ChoppingTransport(replies=["а1", "обрыв"])
        agent = self._agent(transport, keep_last=0, every=10)
        agent.ask("в1")

        record = agent.compress()

        self.assertTrue(record.truncated)
        self.assertTrue(agent.snapshot()["compressions"][0]["truncated"])

    def test_tail_always_starts_with_a_user_message(self):
        transport = FakeTransport(replies=["а1", "а2", "а3", "конспект"])
        agent = self._agent(transport, keep_last=1, every=6)

        for i in range(3):
            agent.ask(f"в{i}")

        # keep_last=1 оставил бы один ответ без вопроса — оставляем пару.
        self.assertEqual([m.content for m in agent.context_messages], ["в2", "а3"])

    def test_manual_compress_and_nothing_to_compress(self):
        transport = FakeTransport(replies=["а1", "конспект"])
        agent = self._agent(transport, keep_last=0, every=10)
        agent.ask("в1")

        record = agent.compress()
        self.assertEqual((record.reason, record.compressed), ("manual", 2))
        self.assertEqual(agent.context_messages, ())
        self.assertIsNone(agent.compress())
        self.assertEqual(len(transport.payloads), 2)

    def test_agent_without_a_policy_cannot_compress(self):
        agent = Agent(AgentConfig(), FakeTransport())
        agent.ask("в1")

        with self.assertRaises(ValueError):
            agent.compress()

    def test_stream_reports_compression_as_events_after_the_answer(self):
        from advent.agent import CompressionRecord, CompressionStarted

        transport = FakeTransport(replies=["а1", "а2", "а3", "конспект"])
        agent = self._agent(transport, keep_last=2, every=6)
        for i in range(2):
            _drive(agent.stream(f"в{i}"))

        events, reply = _drive(agent.stream("в2"))

        self.assertEqual(reply.content, "а3")
        self.assertIsInstance(events[0], CompressionStarted)
        self.assertEqual((events[0].reason, events[0].messages), ("every", 4))
        self.assertIsInstance(events[1], CompressionRecord)
        self.assertEqual(agent.summary.text, "конспект")

    def test_failed_summary_does_not_undo_the_turn(self):
        from advent.agent import CompressionFailed

        from advent.agent.agent import SUMMARY_INSTRUCTION

        class FlakyTransport(FakeTransport):
            def send(self, payload):
                if payload["messages"][0]["content"] == SUMMARY_INSTRUCTION:
                    raise TransportError("сеть упала")
                return super().send(payload)

        transport = FlakyTransport(replies=["а1", "а2", "а3"])
        agent = self._agent(transport, keep_last=2, every=6)
        for i in range(2):
            _drive(agent.stream(f"в{i}"))

        events, reply = _drive(agent.stream("в2"))

        self.assertEqual(reply.content, "а3")
        self.assertIsInstance(events[-1], CompressionFailed)
        self.assertIn("сеть упала", events[-1].error)
        self.assertEqual(len(agent.history), 6)
        self.assertIsNone(agent.summary)
        # ask() тоже не роняет ход из-за конспекта.
        agent.ask("в3")
        self.assertEqual(len(agent.history), 8)

    def test_compresses_instead_of_overflowing_when_there_is_room_to_win(self):
        from advent.agent import CompressionPolicy, CompressionRecord, CompressionStarted, ContextOverflowError

        long_answer = "подробный ответ на вопрос " * 8
        transport = MeasuringTransport(replies=[long_answer, long_answer, "конспект: два факта"])
        agent = Agent(AgentConfig(compression=CompressionPolicy(keep_last=0, every=50)), transport)
        agent.ask("в1")
        agent.ask("в2")
        question = "третий вопрос"
        # Лимит чуть меньше того, что нужно стеку с вопросом: без сжатия — отказ.
        limit = agent.estimate_context(question) - 1
        agent = agent.with_config(context_limit=limit)

        events, reply = _drive(agent.stream(question))

        self.assertIsInstance(events[0], CompressionStarted)
        self.assertEqual(events[0].reason, "overflow")
        self.assertIsInstance(events[1], CompressionRecord)
        # Заглушка отдала конспект на третий send, а сам ответ — «ок» по умолчанию.
        self.assertEqual(reply.content, "ок")
        self.assertEqual(agent.summary.covers, 4)
        self.assertEqual(len(agent.history), 6)
        # Ушёл сжатый контекст: системный промпт с конспектом и один вопрос.
        sent = transport.payloads[-1]["messages"]
        self.assertEqual([m["role"] for m in sent], ["system", "user"])
        self.assertIn("конспект: два факта", sent[0]["content"])

        # А если и после сжатия не влезает — прежний отказ, стек цел.
        tight = agent.with_config(context_limit=5)
        with self.assertRaises(ContextOverflowError):
            _drive(tight.stream("ещё вопрос"))
        self.assertEqual(len(tight.history), 6)

    def test_overflow_without_anything_to_compress_still_refuses(self):
        from advent.agent import CompressionPolicy, ContextOverflowError

        transport = FakeTransport()
        agent = Agent(AgentConfig(context_limit=5, compression=CompressionPolicy(keep_last=2, every=10)), transport)

        with self.assertRaises(ContextOverflowError):
            agent.ask("вопрос длиннее пяти токенов, определённо")
        self.assertEqual(transport.payloads, [])

    def test_estimate_is_recounted_by_characters_right_after_compression(self):
        transport = FakeTransport(replies=["а1", "а2", "а3", "конспект"])
        agent = self._agent(transport, keep_last=2, every=6)
        for i in range(3):
            agent.ask(f"в{i}")

        after_compression = agent.estimate_context()
        agent.ask("в3")
        after_next_turn = agent.estimate_context()

        # Цифры API (10 + 5) описывают старый контекст — их не берём.
        self.assertNotEqual(after_compression, 15)
        self.assertEqual(after_next_turn, 15)

    def test_snapshot_and_restore_keep_the_summary_and_the_records(self):
        transport = FakeTransport(replies=["а1", "а2", "а3", "конспект"])
        agent = self._agent(transport, keep_last=2, every=6)
        for i in range(3):
            agent.ask(f"в{i}")

        snapshot = agent.snapshot()
        self.assertEqual(snapshot["summary"]["text"], "конспект")
        self.assertEqual(snapshot["summary"]["covers"], 4)
        self.assertEqual(snapshot["summary"]["compressions"], 1)
        self.assertEqual(snapshot["summary"]["input_tokens"], 10)
        self.assertEqual(snapshot["context"]["messages"], 2)
        self.assertEqual(snapshot["context"]["summarized"], 4)
        self.assertEqual(snapshot["config"]["compression"], {"keep_last": 2, "every": 6, "summary_max_tokens": 800})

        restored = Agent.restore(snapshot, FakeTransport(replies=["а4"]))
        self.assertEqual(restored.summary, agent.summary)
        self.assertEqual(restored.compressions, agent.compressions)
        self.assertEqual(restored.config.compression, agent.config.compression)
        restored.ask("в3")
        self.assertIn("конспект", restored._transport.payloads[0]["messages"][0]["content"])

    def test_with_config_can_switch_compression_on_for_a_running_dialogue(self):
        from advent.agent import CompressionPolicy

        agent = Agent(AgentConfig(), FakeTransport(replies=["а1", "конспект"]))
        agent.ask("в1")

        compressing = agent.with_config(compression=CompressionPolicy(keep_last=0, every=4))

        self.assertEqual(compressing.history, agent.history)
        self.assertIsNotNone(compressing.compress())

    def test_reset_drops_the_summary(self):
        transport = FakeTransport(replies=["а1", "а2", "а3", "конспект"])
        agent = self._agent(transport, keep_last=2, every=6)
        for i in range(3):
            agent.ask(f"в{i}")

        agent.reset()

        self.assertIsNone(agent.summary)
        self.assertEqual(agent.compressions, ())
        self.assertIsNone(agent.snapshot()["summary"])


class ScenarioFactsTestCase(unittest.TestCase):
    def test_checks_which_facts_survived_in_the_answer(self):
        from advent.agent import get_scenario

        scenario = get_scenario("spec")
        checks = scenario.check("ТЗ: Android 10+, рубли, доллары и евро, офлайн, Google, 800 тыс., 3 месяца, тёмная тема, CSV, уведомления, 299 руб.")

        self.assertEqual(len(checks), 13)
        missing = [c.label for c in checks if not c.found]
        self.assertEqual(missing, ["категории создаёт пользователь"])

    def test_both_currencies_are_required_for_one_fact(self):
        from advent.agent import get_scenario

        scenario = get_scenario("spec")
        by_label = {c.label: c.found for c in scenario.check("поддержка долларов")}

        self.assertFalse(by_label["поддержка долларов и евро"])


class SlidingWindowTestCase(unittest.TestCase):
    """День 10, стратегия 1: только последние N сообщений, остальное отбрасывается."""

    def test_only_the_last_n_messages_and_the_question_go_to_the_model(self):
        transport = FakeTransport(replies=["а1", "а2", "а3", "а4"])
        agent = Agent(AgentConfig(system_prompt="Кратко.", window=2), transport)

        for i in range(4):
            agent.ask(f"в{i}")

        sent = transport.payloads[-1]["messages"]
        # Окно — по истории до вопроса: одна пара плюс сам вопрос.
        self.assertEqual([m["content"] for m in sent], ["Кратко.", "в2", "а3", "в3"])
        # Память цела: все восемь сообщений на месте, в контексте — два.
        self.assertEqual(len(agent.history), 8)
        self.assertEqual([m.content for m in agent.context_messages], ["в3", "а4"])
        self.assertEqual(agent.config.strategy, "window")

    def test_window_keeps_pairs_whole(self):
        transport = FakeTransport(replies=["а1", "а2", "а3"])
        agent = Agent(AgentConfig(window=3), transport)

        for i in range(3):
            agent.ask(f"в{i}")

        sent = transport.payloads[-1]["messages"]
        # Три сообщения резали бы пару — хвост начинается с вопроса, пара целиком.
        self.assertEqual([m["content"] for m in sent], ["в0", "а1", "в1", "а2", "в2"])

    def test_window_makes_no_side_requests_and_reports_dropped_messages(self):
        transport = FakeTransport()
        agent = Agent(AgentConfig(window=2), transport)
        for i in range(3):
            agent.ask(f"в{i}")

        snapshot = agent.snapshot()

        self.assertEqual(len(transport.payloads), 3)
        self.assertEqual(snapshot["strategy"], "window")
        self.assertEqual(snapshot["context"]["messages"], 2)
        self.assertEqual(snapshot["context"]["dropped"], 4)
        self.assertEqual(snapshot["facts"], {})

    def test_estimate_is_by_characters_because_the_payload_changes_every_turn(self):
        agent = Agent(AgentConfig(window=2), FakeTransport())
        agent.ask("в0")
        agent.ask("в1")

        # Заглушка отдаёт prompt_tokens=10, но для окна цифры API не годятся.
        self.assertNotEqual(agent.estimate_context(), 15)

    def test_config_allows_only_one_strategy(self):
        from advent.agent import CompressionPolicy, FactsPolicy

        with self.assertRaises(ValueError):
            AgentConfig(window=2, facts=FactsPolicy())
        with self.assertRaises(ValueError):
            AgentConfig(window=2, compression=CompressionPolicy())
        with self.assertRaises(ValueError):
            AgentConfig(window=0)


class FactsJsonTransport(FakeTransport):
    """Заглушка, которая на запрос фактов отдаёт JSON, а на ходы — обычный текст."""

    def __init__(self, replies=None, facts=None):
        super().__init__(replies=replies)
        self._facts = list(facts or [])

    def send(self, payload):
        if payload.get("response_format") == {"type": "json_object"}:
            self.payloads.append(payload)
            content = self._facts.pop(0) if self._facts else '{"facts": {}}'
            return {
                "model": "deepseek-v4-flash",
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 8},
            }
        return super().send(payload)


class StickyFactsTestCase(unittest.TestCase):
    """День 10, стратегия 2: блок «ключ — значение» плюс последние N."""

    def _agent(self, transport, **policy):
        from advent.agent import FactsPolicy

        return Agent(AgentConfig(system_prompt="Кратко.", facts=FactsPolicy(**policy)), transport)

    def test_updates_facts_after_each_user_message_and_sends_them_with_the_tail(self):
        transport = FactsJsonTransport(
            replies=["а1", "а2"],
            facts=['{"facts": {"платформа": "Android 10"}}', '{"facts": {"платформа": "Android 10", "бюджет": "800 тыс."}}'],
        )
        agent = self._agent(transport, keep_last=2)

        agent.ask("Платформа — Android 10")
        self.assertEqual(agent.facts, {"платформа": "Android 10"})
        agent.ask("Бюджет 800 тысяч")

        self.assertEqual(agent.facts, {"платформа": "Android 10", "бюджет": "800 тыс."})
        # Запрос второго хода: системный промпт с фактами первого хода плюс хвост.
        second_turn = transport.payloads[2]["messages"]
        self.assertEqual([m["role"] for m in second_turn], ["system", "user", "assistant", "user"])
        self.assertIn("Кратко.", second_turn[0]["content"])
        self.assertIn("- платформа: Android 10", second_turn[0]["content"])
        # Запрос на факты: JSON-режим, текущие факты, реплика и ответ.
        facts_request = transport.payloads[3]
        self.assertEqual(facts_request["response_format"], {"type": "json_object"})
        self.assertFalse(facts_request["stream"])
        self.assertIn("Бюджет 800 тысяч", facts_request["messages"][1]["content"])
        self.assertIn('"платформа": "Android 10"', facts_request["messages"][1]["content"])
        self.assertIn("Ответ ассистента:\nа2", facts_request["messages"][1]["content"])
        # Четыре запроса — два хода и два обновления — в одном счётчике.
        self.assertEqual(agent.usage.requests, 4)
        self.assertEqual(agent.usage.total_tokens, 2 * 15 + 2 * 28)

    def test_records_what_changed(self):
        transport = FactsJsonTransport(
            replies=["а1", "а2"],
            facts=['{"бюджет": "800 тыс.", "срок": "3 месяца"}', '{"бюджет": "1 млн", "платформа": "Android"}'],
        )
        agent = self._agent(transport, keep_last=2)

        agent.ask("в1")
        agent.ask("в2")

        first, second = agent.fact_updates
        self.assertEqual((first.added, first.changed, first.removed), (("бюджет", "срок"), (), ()))
        self.assertEqual((second.added, second.changed, second.removed), (("платформа",), ("бюджет",), ("срок",)))
        self.assertEqual(second.facts, {"бюджет": "1 млн", "платформа": "Android"})
        self.assertEqual(second.after_turn, 2)

    def test_only_the_tail_goes_to_the_model_the_rest_lives_in_facts(self):
        transport = FactsJsonTransport(replies=["а1", "а2", "а3"], facts=['{"facts": {"цель": "ТЗ"}}'] * 3)
        agent = self._agent(transport, keep_last=2)
        for i in range(3):
            agent.ask(f"в{i}")

        sent = [m["content"] for m in transport.payloads[4]["messages"]]  # третий ход
        self.assertEqual(sent[1:], ["в1", "а2", "в2"])
        self.assertEqual(len(agent.history), 6)
        self.assertEqual(agent.snapshot()["context"]["dropped"], 4)

    def test_stream_reports_facts_events_after_the_answer(self):
        from advent.agent import FactsRecord, FactsStarted

        transport = FactsJsonTransport(replies=["а1"], facts=['{"facts": {"цель": "ТЗ"}}'])
        agent = self._agent(transport, keep_last=2)

        events, reply = _drive(agent.stream("в1"))

        self.assertEqual(reply.content, "а1")
        self.assertIsInstance(events[0], FactsStarted)
        self.assertIsInstance(events[1], FactsRecord)
        self.assertEqual(events[1].facts, {"цель": "ТЗ"})

    def test_broken_json_keeps_the_previous_facts_and_the_turn(self):
        from advent.agent import FactsFailed

        transport = FactsJsonTransport(replies=["а1", "а2"], facts=['{"facts": {"цель": "ТЗ"}}', "не json"])
        agent = self._agent(transport, keep_last=2)
        agent.ask("в1")

        events, reply = _drive(agent.stream("в2"))

        self.assertIsInstance(events[-1], FactsFailed)
        self.assertEqual(agent.facts, {"цель": "ТЗ"})
        self.assertEqual(len(agent.history), 4)

    def test_facts_are_cleaned_and_capped(self):
        from advent.agent.agent import _parse_facts

        facts = _parse_facts('{"facts": {" валюты ": ["RUB", "USD"], "пусто": "", "срок": {"мес": 3}}}', limit=10)
        self.assertEqual(facts, {"валюты": "RUB, USD", "срок": '{"мес": 3}'})
        self.assertEqual(len(_parse_facts('{"a": "1", "b": "2", "c": "3"}', limit=2)), 2)
        with self.assertRaises(ValueError):
            _parse_facts('["список"]', limit=10)

    def test_snapshot_restore_and_reset_carry_the_facts(self):
        transport = FactsJsonTransport(replies=["а1"], facts=['{"facts": {"цель": "ТЗ"}}'])
        agent = self._agent(transport, keep_last=2)
        agent.ask("в1")

        restored = Agent.restore(agent.snapshot(), FactsJsonTransport(replies=["а2"]))
        self.assertEqual(restored.facts, {"цель": "ТЗ"})
        self.assertEqual(restored.fact_updates, agent.fact_updates)
        self.assertEqual(restored.config.facts, agent.config.facts)
        restored.ask("в2")
        self.assertIn("- цель: ТЗ", restored._transport.payloads[0]["messages"][0]["content"])

        agent.reset()
        self.assertEqual(agent.facts, {})
        self.assertEqual(agent.fact_updates, ())


class ForkTestCase(unittest.TestCase):
    """День 10, стратегия 3: ветка — агент из обрезанного слепка."""

    def test_fork_cuts_the_snapshot_at_the_checkpoint(self):
        transport = FakeTransport(replies=["а1", "а2", "а3"])
        agent = Agent(AgentConfig(system_prompt="Кратко."), transport)
        for i in range(3):
            agent.ask(f"в{i}")

        saved = agent.fork(4)
        branch = Agent.restore(saved, FakeTransport(replies=["ветка"]))

        self.assertEqual([m.content for m in branch.history], ["в0", "а1", "в1", "а2"])
        self.assertEqual([t.index for t in branch.turns], [1, 2])
        self.assertEqual(branch.usage.requests, 2)
        self.assertEqual(branch.usage.total_tokens, 30)
        self.assertEqual(branch.config, agent.config)
        # Ветка живёт дальше сама, родитель не меняется.
        branch.ask("в-ветка")
        self.assertEqual(len(branch.history), 6)
        self.assertEqual(len(agent.history), 6)
        self.assertEqual(agent.history[4].content, "в2")

    def test_fork_requires_a_turn_boundary_inside_the_stack(self):
        agent = Agent(AgentConfig(), FakeTransport())
        agent.ask("в0")

        for bad in (1, 3, -2):
            with self.subTest(at=bad):
                with self.assertRaises(ValueError):
                    agent.fork(bad)
        self.assertEqual(Agent.restore(agent.fork(0), FakeTransport()).history, ())

    def test_fork_keeps_facts_as_they_were_at_the_checkpoint(self):
        from advent.agent import FactsPolicy

        transport = FactsJsonTransport(
            replies=["а1", "а2"],
            facts=['{"facts": {"цель": "ТЗ"}}', '{"facts": {"цель": "ТЗ", "бюджет": "800"}}'],
        )
        agent = Agent(AgentConfig(facts=FactsPolicy(keep_last=2)), transport)
        agent.ask("в1")
        agent.ask("в2")

        branch = Agent.restore(agent.fork(2), FactsJsonTransport())

        self.assertEqual(branch.facts, {"цель": "ТЗ"})
        self.assertEqual(len(branch.fact_updates), 1)
        # Расход ветки — ход плюс одно обновление фактов.
        self.assertEqual(branch.usage.requests, 2)
        self.assertEqual(branch.usage.total_tokens, 15 + 28)

    def test_fork_drops_a_summary_made_after_the_checkpoint(self):
        from advent.agent import CompressionPolicy

        transport = FakeTransport(replies=["а1", "а2", "а3", "конспект"])
        agent = Agent(AgentConfig(compression=CompressionPolicy(keep_last=2, every=6)), transport)
        for i in range(3):
            agent.ask(f"в{i}")
        self.assertIsNotNone(agent.summary)

        early = Agent.restore(agent.fork(2), FakeTransport())
        late = Agent.restore(agent.fork(6), FakeTransport())

        self.assertIsNone(early.summary)
        self.assertEqual(early.compressions, ())
        self.assertEqual(late.summary, agent.summary)
        self.assertEqual(late.usage.requests, 4)


class RegistrySpawnTestCase(unittest.TestCase):
    def test_spawn_registers_and_stores_an_agent_from_a_snapshot(self):
        import tempfile
        from pathlib import Path

        from advent.agent import AgentRegistry, JsonStore

        with tempfile.TemporaryDirectory() as store_dir:
            registry = AgentRegistry(
                lambda saved: Agent.restore(saved, FakeTransport()) if saved else Agent(AgentConfig(), FakeTransport()),
                store=JsonStore(store_dir),
            )
            root = registry.get("s-main")
            root.ask("в0")
            root.ask("в1")

            branch = registry.spawn("s-b1", root.fork(2))

            self.assertIs(registry.get("s-b1"), branch)
            self.assertEqual(len(branch.history), 2)
            self.assertTrue(Path(store_dir, "s-b1.json").exists())


class DialogTreeTestCase(unittest.TestCase):
    def test_checkpoint_fork_switch_and_round_trip(self):
        from advent.agent import DialogTree

        tree = DialogTree.fresh()
        point = tree.add_checkpoint("main", 10, "после десяти")
        branch_a = tree.add_branch(point.id, "A")
        branch_b = tree.add_branch(point.id)
        tree.switch(branch_b.id)

        self.assertEqual((point.id, point.branch, point.message), ("c1", "main", 10))
        self.assertEqual((branch_a.id, branch_a.parent, branch_a.forked_at), ("b1", "main", 10))
        self.assertEqual(branch_b.title, "Ветка 2")
        self.assertEqual(tree.active, "b2")

        restored = DialogTree.from_dict(tree.as_dict())
        self.assertEqual(restored.as_dict(), tree.as_dict())

    def test_rejects_bad_points(self):
        from advent.agent import DialogTree

        tree = DialogTree.fresh()
        with self.assertRaises(ValueError):
            tree.add_checkpoint("main", 3)
        with self.assertRaises(ValueError):
            tree.add_checkpoint("nope", 2)
        with self.assertRaises(ValueError):
            tree.add_branch("c9")
        with self.assertRaises(ValueError):
            tree.switch("b9")

    def test_store_round_trips_and_survives_a_broken_file(self):
        import tempfile
        from pathlib import Path

        from advent.agent import DialogTree, TreeStore

        with tempfile.TemporaryDirectory() as directory:
            store = TreeStore(directory)
            tree = DialogTree.fresh(runs=[{"strategy": "window"}])
            tree.add_checkpoint("main", 2)
            store.save("s1", tree)

            self.assertEqual(store.load("s1").as_dict(), tree.as_dict())
            self.assertEqual(store.load("s2").active, "main")
            Path(directory, "s1-tree.json").write_text("{", encoding="utf-8")
            self.assertEqual(store.load("s1").checkpoints, [])
            self.assertTrue(store.delete("s1"))
            self.assertFalse(store.delete("s1"))


class DeepSeekTransportTestCase(unittest.TestCase):
    def test_fails_fast_without_an_api_key(self):
        from advent.agent import DeepSeekTransport

        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(TransportError) as raised:
                DeepSeekTransport().send({"model": "x", "messages": []})

        self.assertIn("DEEPSEEK_API_KEY", str(raised.exception))

    def test_uses_the_default_agent_transport_when_none_is_given(self):
        from advent.agent import DeepSeekTransport

        agent = Agent(AgentConfig())

        self.assertIsInstance(agent._transport, DeepSeekTransport)


class MemoryLayersTestCase(unittest.TestCase):
    """День 11: три слоя памяти с разным сроком жизни."""

    def _memory(self):
        from advent.agent import LayeredMemory

        return LayeredMemory()

    def test_layer_reports_added_and_changed_and_keeps_its_ceiling(self):
        from advent.agent import MemoryLayer

        layer = MemoryLayer("chat")

        self.assertEqual(layer.put("name", "Максим"), "added")
        self.assertEqual(layer.put("name", "Максим"), "")
        self.assertEqual(layer.put("name", "Максим П."), "changed")

        for index in range(layer.limit + 3):
            layer.put(f"k{index}", f"v{index}")

        self.assertEqual(len(layer.items), layer.limit)
        # Вытесняется самое старое, а не самое новое.
        self.assertNotIn("name", layer.items)

    def test_render_signs_every_layer_and_says_there_are_three(self):
        memory = self._memory()
        memory["user"].put("name", "Максим")
        memory["task"].put("budget", "800 тысяч")

        text = memory.render()

        self.assertIn("три раздельные памяти", text)
        self.assertIn("Долговременная память", text)
        self.assertIn("- name: Максим", text)
        self.assertIn("Рабочая память", text)
        self.assertIn("- budget: 800 тысяч", text)
        # Пустой слой в промпт не лезет.
        self.assertNotIn("Краткосрочная память", text)

    def test_disabled_layer_is_not_rendered_but_stays_in_memory(self):
        memory = self._memory()
        memory["task"].put("budget", "800 тысяч")
        memory["user"].put("name", "Максим")

        text = memory.render(["user"])

        self.assertNotIn("800 тысяч", text)
        self.assertIn("Максим", text)
        self.assertEqual(len(memory["task"].items), 1)

    def test_apply_reports_what_landed_where(self):
        memory = self._memory()
        memory["task"].put("budget", "700 тысяч")

        update = memory.apply(
            {"chat": {"now": "думаем"}, "task": {"budget": "800 тысяч"}, "user": {"name": "Максим"}, "forget": []},
            mode="agent",
            turn=3,
        )

        self.assertEqual(update.added["user"], ["name"])
        self.assertEqual(update.added["chat"], ["now"])
        self.assertEqual(update.changed["task"], ["budget"])
        self.assertEqual(update.after_turn, 3)
        self.assertFalse(update.empty)

    def test_apply_forgets_what_the_reply_cancelled(self):
        memory = self._memory()
        memory["user"].put("style", "коротко")

        update = memory.apply({"forget": ["style"]}, mode="agent", turn=1)

        self.assertEqual(update.forgotten, ["style"])
        self.assertEqual(memory["user"].items, {})

    def test_heuristic_sorts_by_markers_without_a_request(self):
        from advent.agent import route_by_heuristic

        self.assertTrue(route_by_heuristic("Меня зовут Максим", 1)["user"])
        self.assertTrue(route_by_heuristic("Бюджет 800 тысяч", 2)["task"])
        self.assertTrue(route_by_heuristic("Ну давай подумаем", 3)["chat"])

    def test_router_parses_json_and_reports_its_cost(self):
        from advent.agent import route_by_agent

        transport = FakeTransport(replies=['{"chat": {}, "task": {"budget": "800"}, "user": {}}'])
        routed, reply = route_by_agent(transport, "deepseek-v4-flash", self._memory(), "вопрос", "ответ")

        self.assertEqual(routed["task"], {"budget": "800"})
        self.assertEqual(reply.input_tokens, 10)
        self.assertEqual(transport.payloads[0]["response_format"], {"type": "json_object"})

    def test_router_failure_is_a_transport_error(self):
        from advent.agent import route_by_agent

        with self.assertRaises(TransportError):
            route_by_agent(FakeTransport(replies=["не json"]), "m", self._memory(), "в", "о")

    def test_store_keeps_one_file_per_layer_and_clears_them_apart(self):
        import tempfile

        from advent.agent import MemoryStore

        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory)
            memory = self._memory()
            memory["chat"].put("now", "думаем")
            memory["task"].put("budget", "800")
            memory["user"].put("name", "Максим")
            store.save("s1", memory)

            self.assertEqual([f["exists"] for f in store.files("s1")], [True, True, True])

            store.clear("s1", "chat")
            back = store.load("s1")

            self.assertEqual(back["chat"].items, {})
            self.assertEqual(back["task"].items["budget"].value, "800")
            self.assertEqual(back["user"].items["name"].value, "Максим")

    def test_store_survives_a_broken_layer_file(self):
        import tempfile
        from pathlib import Path

        from advent.agent import MemoryStore

        with tempfile.TemporaryDirectory() as directory:
            store = MemoryStore(directory)
            memory = self._memory()
            memory["user"].put("name", "Максим")
            store.save("s1", memory)
            Path(directory, "s1-mem-task.json").write_text("{битый", encoding="utf-8")

            back = store.load("s1")

            self.assertEqual(back["task"].items, {})
            self.assertEqual(back["user"].items["name"].value, "Максим")

    def test_agent_puts_memory_text_into_the_system_prompt(self):
        transport = FakeTransport(replies=["ок"])
        agent = Agent(AgentConfig(system_prompt="Кратко."), transport)
        agent.memory_text = "Долговременная память:\n- name: Максим"

        agent.ask("привет")

        system = next(m for m in transport.payloads[-1]["messages"] if m["role"] == "system")["content"]
        self.assertIn("Кратко.", system)
        self.assertIn("name: Максим", system)


class TaskStateMachineTestCase(unittest.TestCase):
    """День 13: задача как конечный автомат."""

    def _task(self, plan=("шаг 1", "шаг 2", "шаг 3")):
        from advent.agent import new_task

        task = new_task("Нужна утилита для расходов из CSV", "t1")
        task.set_plan(list(plan))
        return task

    def test_task_is_born_in_planning_with_a_title_from_the_goal(self):
        from advent.agent import new_task

        task = new_task("Нужна утилита на Python: читает CSV с расходами и печатает сумму по категориям", "t1")

        self.assertEqual(task.stage, "planning")
        self.assertEqual(task.title, "Нужна утилита на Python: читает CSV с…")
        self.assertEqual(task.steps, [])
        self.assertIn("план", task.expected)

    def test_happy_path_walks_all_four_stages(self):
        task = self._task()

        task.approve_plan(turn=1)
        self.assertEqual((task.stage, task.current), ("execution", 0))
        self.assertIn("шаг 1", task.expected)

        task.complete_step(turn=2, note="сделано")
        task.complete_step(turn=3)
        self.assertEqual((task.stage, task.current), ("execution", 2))

        task.complete_step(turn=4)
        self.assertEqual(task.stage, "validation")
        self.assertTrue(all(step.done for step in task.steps))

        task.validate(True, turn=5)
        self.assertEqual(task.stage, "done")
        self.assertEqual([t["to"] for t in [x.as_dict() for x in task.log]], ["execution", "validation", "done"])

    def test_failed_validation_reopens_the_step_and_everything_after_it(self):
        task = self._task()
        task.approve_plan(turn=1)
        for turn in (2, 3, 4):
            task.complete_step(turn=turn)

        task.validate(False, turn=5, step=2, issues=["нет группировки"])

        self.assertEqual((task.stage, task.current), ("execution", 1))
        self.assertEqual([step.done for step in task.steps], [True, False, False])
        self.assertIn("возврат на шаг 2", task.log[-1].reason)

    def test_illegal_transitions_raise(self):
        from advent.agent import TaskError, new_task

        empty = new_task("цель", "t0")
        with self.assertRaises(TaskError):
            empty.approve_plan(turn=1)  # плана нет

        task = self._task()
        with self.assertRaises(TaskError):
            task.complete_step(turn=1)  # ещё planning
        with self.assertRaises(TaskError):
            task.validate(True, turn=1)  # ещё не validation

        task.approve_plan(turn=1)
        with self.assertRaises(TaskError):
            task.set_plan(["другой план"])  # план заморожен после утверждения

    def test_pause_and_resume_keep_the_stage_and_write_the_journal(self):
        task = self._task()
        task.approve_plan(turn=1)
        task.complete_step(turn=2)

        task.pause(turn=3)
        self.assertTrue(task.paused)
        self.assertIn("НА ПАУЗЕ", task.render())
        self.assertEqual((task.stage, task.current), ("execution", 1))

        task.resume(turn=3)
        self.assertFalse(task.paused)
        self.assertEqual([t.reason for t in task.log[-2:]], ["пауза", "продолжение"])

    def test_render_tells_the_agent_where_it_is_and_not_to_retell(self):
        task = self._task()
        task.approve_plan(turn=1)
        task.complete_step(turn=2)

        text = task.render()

        self.assertIn("Этап: execution", text)
        self.assertIn("[x] 1. шаг 1", text)
        self.assertIn("[>] 2. шаг 2", text)
        self.assertIn("Текущий шаг: 2 из 3", text)
        self.assertIn("Не пересказывай задачу", text)

    def test_observer_report_moves_the_machine_but_never_jumps_stages(self):
        task = self._task()

        record = task.apply_report({"plan": ["a", "b"], "plan_ready": True}, turn=1)
        self.assertEqual(record.applied, "план: 2 шагов")
        self.assertEqual(task.stage, "planning")  # утверждает пользователь, не наблюдатель

        task.approve_plan(turn=1)
        task.apply_report({"step_done": False, "note": ""}, turn=2)
        self.assertEqual(task.current, 0)
        task.apply_report({"step_done": True, "note": "ок"}, turn=3)
        self.assertEqual(task.current, 1)
        record = task.apply_report({"step_done": True, "note": "ок"}, turn=4)
        self.assertEqual(task.stage, "validation")
        self.assertIn("→ проверка", record.applied)

        task.apply_report({"verdict": "fail", "step": 1, "issues": ["x"]}, turn=5)
        self.assertEqual((task.stage, task.current), ("execution", 0))

    def test_observer_parses_json_per_stage_and_counts_tokens(self):
        from advent.agent import observe

        task = self._task()
        transport = FakeTransport(replies=['{"plan": ["один", "два"], "plan_ready": true}'])
        report, reply = observe(transport, "m", task, "вопрос", "ответ")

        self.assertEqual(report, {"plan": ["один", "два"], "plan_ready": True})
        self.assertEqual(reply.input_tokens, 10)
        self.assertEqual(transport.payloads[0]["response_format"], {"type": "json_object"})
        self.assertIn("планирования", transport.payloads[0]["messages"][0]["content"])

        task.approve_plan(turn=1)
        report, _ = observe(FakeTransport(replies=['{"step_done": "yes"}']), "m", task, "в", "о")
        self.assertEqual(report["step_done"], False)  # только настоящий true

        with self.assertRaises(TransportError):
            observe(FakeTransport(replies=["не json"]), "m", task, "в", "о")

    def test_retelling_detects_explanations_from_scratch(self):
        from advent.agent import retelling

        task = self._task()
        retold = retelling("Напомню, задача была: утилита для расходов из CSV. Итак…", task)
        clean = retelling("Шаг 2 сделан: функция group_by_category возвращает словарь.", task)

        self.assertTrue(retold["retold"])
        self.assertIn("Напомню", retold["phrases"])
        self.assertFalse(clean["retold"])

    def test_store_round_trips_the_task_and_survives_a_broken_file(self):
        import tempfile
        from pathlib import Path

        from advent.agent import TaskStore

        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(directory)
            task = self._task()
            task.approve_plan(turn=1)
            task.complete_step(turn=2, note="ок")
            task.pause(turn=3)
            store.save("s1", task)

            back = store.load("s1")
            self.assertEqual((back.stage, back.current, back.paused), ("execution", 1, True))
            self.assertEqual(back.steps[0].note, "ок")
            self.assertEqual(len(back.log), 2)

            Path(directory, "s1-task.json").write_text("{битый", encoding="utf-8")
            self.assertIsNone(store.load("s1"))


class InvariantsTestCase(unittest.TestCase):
    """День 14: инварианты в два рубежа."""

    def _presets(self):
        from advent.agent.invariants import PRESETS

        return list(PRESETS)

    def test_render_numbers_the_rules_and_demands_a_check_before_answering(self):
        from advent.agent import render_invariants

        text = render_invariants(self._presets())

        self.assertIn("1. [стек]", text)
        self.assertIn("4. [бизнес-правило]", text)
        self.assertIn("Перед ответом сверь запрос с каждым инвариантом", text)
        self.assertIn("«Не могу: нарушает инвариант N»", text)

    def test_disabled_rules_leave_the_prompt(self):
        from advent.agent import Invariant, render_invariants

        rules = [Invariant("a", "правило А", "stack", enabled=False), Invariant("b", "правило Б", "business")]
        text = render_invariants(rules)

        self.assertNotIn("правило А", text)
        self.assertIn("1. [бизнес-правило] правило Б", text)
        self.assertEqual(render_invariants([rules[0]]), "")

    def test_inspect_separates_refusal_from_violation(self):
        from advent.agent import inspect

        refused = inspect("Не могу: нарушает инвариант 1 — Django исключён. Вместо этого во Flask…", self._presets())
        yielded = inspect("Отлично, ставим Django и делаем startproject.", self._presets())
        clean = inspect("Храним в users.json, пароли через bcrypt.", self._presets())

        self.assertTrue(refused["refused"])
        self.assertEqual(refused["named"], ["stack"])
        self.assertEqual(refused["violations"], [])
        self.assertEqual([m["invariant"] for m in refused["mentions"]], ["stack"])

        self.assertFalse(yielded["refused"])
        self.assertEqual([v["invariant"] for v in yielded["violations"]], ["stack"])

        self.assertEqual(clean["violations"], [])

    def test_verdict_makes_the_reviewer_the_judge_over_regex_alarms(self):
        from advent.agent import CheckRecord

        base = dict(after_turn=1, question="в", refused=False, named=[], mentions=[])
        alarm = [{"invariant": "passwords", "evidence": "открытого текста", "source": "regex"}]

        self.assertEqual(CheckRecord(**base, regex=alarm, reviewer=[], reviewer_refused=False).verdict, "false_alarm")
        self.assertEqual(CheckRecord(**base, regex=alarm, reviewer=None, reviewer_refused=None).verdict, "caught")
        self.assertEqual(CheckRecord(**base, regex=[], reviewer=[{"invariant": "paid", "why": "x", "source": "reviewer"}], reviewer_refused=False).verdict, "caught")
        self.assertEqual(CheckRecord(**{**base, "refused": True}, regex=[], reviewer=[], reviewer_refused=True).verdict, "held")
        self.assertEqual(CheckRecord(**base, regex=[], reviewer=[], reviewer_refused=False).verdict, "clean")

    def test_reviewer_parses_json_and_ignores_unknown_ids(self):
        from advent.agent import review

        transport = FakeTransport(replies=['{"violations": [{"id": "paid", "why": "советует платный API"}, {"id": "nope", "why": "x"}], "refused": false}'])
        verdict, reply = review(transport, "m", self._presets(), "в", "о")

        self.assertEqual([v["invariant"] for v in verdict["violations"]], ["paid"])
        self.assertFalse(verdict["refused"])
        self.assertEqual(reply.input_tokens, 10)
        self.assertEqual(transport.payloads[0]["response_format"], {"type": "json_object"})
        with self.assertRaises(TransportError):
            review(FakeTransport(replies=["не json"]), "m", self._presets(), "в", "о")

    def test_invariant_validates_its_fields(self):
        from advent.agent import Invariant, InvariantError

        with self.assertRaises(InvariantError):
            Invariant("Bad Id", "правило")
        with self.assertRaises(InvariantError):
            Invariant("ok", "  ")
        with self.assertRaises(InvariantError):
            Invariant("ok", "правило", kind="magic")
        with self.assertRaises(InvariantError):
            Invariant("ok", "правило", check="(")

    def test_store_keeps_custom_rules_disabled_presets_and_checks(self):
        import tempfile

        from advent.agent import InvariantStore, all_invariants

        with tempfile.TemporaryDirectory() as directory:
            store = InvariantStore(directory)
            data = store.load("s1")
            data["custom"].append({"id": "mine", "rule": "своё", "kind": "decision", "check": None, "enabled": True})
            data["disabled"] = ["paid"]
            data["checks"] = [{"after_turn": 1, "refused": True}]
            store.save("s1", data)

            back = store.load("s1")
            rules = all_invariants(back)

            self.assertEqual([r.id for r in rules], ["stack", "storage", "passwords", "paid", "mine"])
            self.assertFalse(next(r for r in rules if r.id == "paid").enabled)
            self.assertTrue(next(r for r in rules if r.id == "mine").enabled)
            self.assertEqual(len(back["checks"]), 1)


class TransitionGateTestCase(unittest.TestCase):
    """День 15: таблица переходов и guard'ы — одни ворота для всех."""

    def _task(self, plan=("a", "b", "c")):
        from advent.agent import new_task

        task = new_task("Крестики-нолики", "t1", guarded=True)
        if plan:
            task.set_plan(list(plan))
        return task

    def test_missing_edges_are_refused_and_logged(self):
        task = self._task()

        record = task.request("done", actor="user", turn=1)

        self.assertTrue(record.refused)
        self.assertIn("переход planning → done не разрешён", record.reason)
        self.assertEqual(task.stage, "planning")
        self.assertEqual(len(task.refusals), 1)
        self.assertEqual(task.as_dict()["allowed"], ["execution"])

    def test_only_the_user_approves_the_plan_and_accepts_the_result(self):
        task = self._task()

        self.assertIn("только пользователь", task.request("execution", actor="model", turn=1).reason)
        self.assertIn("только пользователь", task.request("execution", actor="observer", turn=1).reason)
        self.assertFalse(task.request("execution", actor="user", turn=1).refused)
        self.assertEqual(task.stage, "execution")

        for turn in (2, 3, 4):
            task.complete_step(turn=turn)
        self.assertEqual(task.stage, "validation")
        self.assertIn("только пользователь", task.request("done", actor="observer", turn=5).reason)
        self.assertFalse(task.request("done", actor="user", turn=5).refused)
        self.assertEqual(task.stage, "done")

    def test_no_plan_no_approval_and_no_validation_with_open_steps(self):
        from advent.agent import new_task

        empty = new_task("цель", "t0", guarded=True)
        self.assertIn("плана ещё нет", empty.request("execution", actor="user", turn=1).reason)

        task = self._task()
        task.request("execution", actor="user", turn=1)
        self.assertIn("не закрыты шаги 1, 2, 3", task.request("validation", actor="model", turn=2).reason)
        task.complete_step(turn=2)
        self.assertIn("не закрыты шаги 2, 3", task.request("validation", actor="model", turn=3).reason)

    def test_paused_task_refuses_every_transition(self):
        task = self._task()
        task.pause(turn=1)

        record = task.request("execution", actor="user", turn=1)

        self.assertTrue(record.refused)
        self.assertIn("на паузе", record.reason)
        task.resume(turn=1)
        self.assertFalse(task.request("execution", actor="user", turn=1).refused)

    def test_internal_moves_cannot_skip_edges_either(self):
        from advent.agent import TaskError

        task = self._task()
        with self.assertRaises(TaskError):
            task._move("done", "прыжок", 1)

    def test_empty_observer_report_keeps_the_plan(self):
        task = self._task()

        record = task.apply_report({"plan": [], "plan_ready": False}, turn=2)

        self.assertEqual(record.applied, "без изменений")
        self.assertEqual(len(task.steps), 3)

    def test_guarded_prompt_lists_edges_and_forbids_jumping(self):
        from advent.agent import new_task, requested_transition

        text = self._task().render()
        self.assertIn("Разрешённые переходы", text)
        self.assertIn("planning → execution (план утверждён)", text)
        self.assertIn("Отсюда можно только в: execution", text)
        self.assertIn("не перепрыгиваешь", text)
        self.assertNotIn("Разрешённые переходы", new_task("цель", "t2").render())

        self.assertEqual(requested_transition("Все шаги закрыты.\n[переход: validation]"), "validation")
        self.assertIsNone(requested_transition("Шаг сделан."))

    def test_journal_entries_carry_actor_and_refusal_through_the_store(self):
        import tempfile

        from advent.agent import TaskStore

        task = self._task()
        task.request("done", actor="model", turn=1)
        task.request("execution", actor="user", turn=1)
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(directory, "task15")
            store.save("s1", task)
            back = store.load("s1")

        self.assertTrue(back.guarded)
        self.assertEqual([(t.actor, t.refused) for t in back.log], [("model", True), ("user", False)])


if __name__ == "__main__":
    unittest.main()

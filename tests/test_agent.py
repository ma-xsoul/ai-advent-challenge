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

        self.assertEqual(context, {"estimated": 15, "limit": 500, "messages": 2, "summarized": 0})


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


if __name__ == "__main__":
    unittest.main()

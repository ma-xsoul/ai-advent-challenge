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

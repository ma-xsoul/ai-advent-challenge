import json
import pathlib
import tempfile
import unittest

import anyio
from mcp import Client
from mcp.client._memory import InMemoryTransport

from advent import create_app
from advent.mcp.agent import ToolDialog
from advent.mcp.servers.crm import SEED, CrmStore, build_server
from advent.rag import HashingEmbedder, RagIndex
from advent.rag.support import MESSAGE_MARK, documents, ensure_index, history, message, turn


FAQ = """# FAQ · Сервис

## Ошибка 401 «Нужен ключ доступа»

Ключ отозван владельцем — попросите новый.

## Ошибка 429 «Лимит запросов в минуту»

Не отправляйте запросы пачкой, ждите Retry-After.
"""


def call(server, name, arguments=None):
    async def run():
        async with Client(InMemoryTransport(server)) as client:
            result = await client.call_tool(name, arguments or {})
            text = "\n".join(block.text for block in result.content if getattr(block, "type", None) == "text")
            return bool(result.is_error), text

    error, text = anyio.run(run)
    return error, (text if error else json.loads(text))


class ScriptedModel:
    def __init__(self, *replies):
        self.payloads = []
        self.replies = list(replies)

    def send(self, payload):
        self.payloads.append(payload)
        message = self.replies.pop(0)
        finish = "tool_calls" if message.get("tool_calls") else "stop"
        return {"choices": [{"finish_reason": finish, "message": message}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}


def tool_call(name, arguments):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}


ANSWER = {"role": "assistant", "content": "Здравствуйте, Анна! Ваш ключ отозван — попросите новый [1]."}


class CrmServerTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = CrmStore(pathlib.Path(directory.name) / "day33" / "crm.json")
        self.server = build_server(self.store)

    def test_ticket_comes_with_client_card_without_events(self):
        error, data = call(self.server, "get_ticket", {"ticket_id": "T-101"})

        self.assertFalse(error)
        self.assertEqual(data["subject"], "Почему не работает авторизация?")
        self.assertEqual(data["client"]["name"], "Анна Смирнова")
        self.assertEqual(data["client"]["key"]["status"], "revoked")
        self.assertEqual(data["client"]["rate_per_minute"], 20)
        self.assertNotIn("events", data["client"])

    def test_events_service_and_lookup_by_email(self):
        _, events = call(self.server, "client_events", {"client": "igor@datalab.test", "limit": 50})
        _, service = call(self.server, "service_status")
        _, client = call(self.server, "get_client", {"client": "C-4"})

        self.assertEqual(events["by_status"], {"200": 5, "429": 5})
        self.assertTrue(service["public_url"].startswith("https://"))
        self.assertEqual(client["plan"], "free")

    def test_reply_goes_to_working_copy_not_to_seed(self):
        seed = SEED.read_text(encoding="utf-8")
        error, result = call(self.server, "reply_to_ticket", {"ticket_id": "T-102", "text": "Ждите Retry-After"})
        _, answered = call(self.server, "list_tickets", {"status": "answered"})

        self.assertFalse(error)
        self.assertEqual(result, {"id": "T-102", "status": "answered", "messages": 2})
        self.assertEqual([item["id"] for item in answered["tickets"]], ["T-102"])
        self.assertEqual(SEED.read_text(encoding="utf-8"), seed)
        self.store.reset()
        self.assertEqual(self.store.ticket(self.store.load(), "T-102")["status"], "open")

    def test_unknown_or_malformed_ids_are_errors(self):
        self.assertTrue(call(self.server, "get_ticket", {"ticket_id": "T-999"})[0])
        self.assertTrue(call(self.server, "get_ticket", {"ticket_id": "../crm"})[0])
        self.assertTrue(call(self.server, "get_client", {"client": "nobody@test"})[0])


class SupportAssistantTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        folder = pathlib.Path(directory.name)
        self.docs_dir = folder / "docs"
        self.docs_dir.mkdir()
        (self.docs_dir / "faq.md").write_text(FAQ, encoding="utf-8")
        self.index = RagIndex(folder / "index.sqlite3")
        self.embedder = HashingEmbedder()
        server = build_server(CrmStore(folder / "crm.json"))
        self.factory = lambda spec, errlog: InMemoryTransport(server)

    def test_index_is_built_once_and_rebuilt_when_faq_changes(self):
        first = ensure_index(self.index, self.embedder, documents(self.docs_dir))
        again = ensure_index(self.index, self.embedder, documents(self.docs_dir))
        (self.docs_dir / "faq.md").write_text(FAQ + "\n## Новый раздел\n\nТекст.\n", encoding="utf-8")
        rebuilt = ensure_index(self.index, self.embedder, documents(self.docs_dir))

        self.assertEqual(first["built_at"], again["built_at"])
        self.assertGreater(rebuilt["corpus"]["chars"], first["corpus"]["chars"])

    def test_turn_reads_crm_and_cites_faq(self):
        ensure_index(self.index, self.embedder, documents(self.docs_dir))
        model = ScriptedModel(tool_call("get_ticket", {"ticket_id": "T-101"}), ANSWER)
        dialog = ToolDialog()
        events = list(turn("T-101", "Почему не работает доступ?", dialog=dialog, index=self.index, embedder=self.embedder,
                           transport=model, factory=self.factory, subject="авторизация"))
        kinds = [event["type"] for event in events]

        self.assertEqual(kinds[0], "retrieved")
        self.assertIn("tool_result", kinds)
        self.assertEqual(next(event for event in events if event["type"] == "answer")["cited"], [1])
        sent = model.payloads[0]["messages"][1]["content"]
        self.assertTrue(sent.startswith("Тикет: T-101"))
        self.assertIn(f"{MESSAGE_MARK} Почему не работает доступ?", sent)
        self.assertIn("reply_to_ticket", model.payloads[0]["messages"][0]["content"])
        self.assertEqual(history(dialog), [
            {"role": "client", "text": "Почему не работает доступ?"},
            {"role": "tool", "name": "get_ticket", "arguments": {"ticket_id": "T-101"}, "ok": True},
            {"role": "assistant", "text": ANSWER["content"]},
        ])

    def test_message_keeps_ticket_and_faq_numbers(self):
        ensure_index(self.index, self.embedder, documents(self.docs_dir))
        hits = self.index.search("ключ", self.embedder, "structure", k=2)

        self.assertIn("[1] ", message("T-7", "Привет", hits))


class DayThirtyThreeTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        folder = pathlib.Path(directory.name)
        docs = folder / "docs"
        docs.mkdir()
        (docs / "faq.md").write_text(FAQ, encoding="utf-8")
        crm = folder / "day33" / "crm.json"
        server = build_server(CrmStore(crm))
        self.model = ScriptedModel(tool_call("get_ticket", {"ticket_id": "T-101"}), ANSWER)
        self.app = create_app({
            "TESTING": True,
            "AGENT_STORE_DIR": str(folder / "agents"),
            "DAY33_CRM_PATH": str(crm),
            "DAY33_INDEX_PATH": str(folder / "day33" / "index.sqlite3"),
            "DAY33_DIALOGS_DIR": str(folder / "day33" / "dialogs"),
            "DAY33_DOCS_DIR": str(docs),
            "DAY33_MCP_FACTORY": lambda spec, errlog: InMemoryTransport(server),
            "RAG_EMBEDDER": HashingEmbedder(),
            "AGENT_TRANSPORT": self.model,
        })
        self.client = self.app.test_client()

    def test_page_lists_tickets_and_service(self):
        html = self.client.get("/days/33/").get_data(as_text=True)

        self.assertIn('data-ticket="T-101"', html)
        self.assertIn('data-ticket="T-105"', html)
        self.assertIn("Поддержка · Приватный AI-сервис", html)
        self.assertIn("js/day33.js", html)

    def test_chat_is_saved_and_reply_goes_through_mcp(self):
        response = self.client.post("/days/33/api/tickets/T-101/chat", json={"message": "Почему не работает доступ?"})
        events = [json.loads(line) for line in response.get_data(as_text=True).splitlines() if line]
        self.assertEqual(events[0]["type"], "retrieved")
        self.assertEqual(events[-1]["type"], "done")

        view = self.client.get("/days/33/api/tickets/T-101").get_json()
        self.assertEqual([item["role"] for item in view["chat"]], ["client", "tool", "assistant"])
        self.assertEqual(view["client"]["name"], "Анна Смирнова")
        self.assertEqual(view["events"][-1]["status"], 401)

        sent = self.client.post("/days/33/api/tickets/T-101/reply", json={"text": "Попросите новый ключ"}).get_json()
        self.assertEqual(sent["ticket"]["status"], "answered")
        self.assertEqual(sent["ticket"]["messages"][-1]["from"], "support")

        self.client.post("/days/33/api/tickets/T-101/reset")
        self.assertEqual(self.client.get("/days/33/api/tickets/T-101").get_json()["chat"], [])
        tickets = self.client.post("/days/33/api/crm/reset").get_json()["tickets"]
        self.assertEqual(tickets[0]["status"], "open")

    def test_validation(self):
        self.assertEqual(self.client.get("/days/33/api/tickets/x").status_code, 400)
        self.assertEqual(self.client.get("/days/33/api/tickets/T-999").status_code, 404)
        self.assertEqual(self.client.post("/days/33/api/tickets/T-101/chat", json={"message": " "}).status_code, 400)
        self.assertEqual(self.client.post("/days/33/api/tickets/T-999/chat", json={"message": "?"}).status_code, 404)
        self.assertEqual(self.client.post("/days/33/api/tickets/T-101/reply", json={}).status_code, 400)


if __name__ == "__main__":
    unittest.main()

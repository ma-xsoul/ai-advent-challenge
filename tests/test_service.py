import json
import pathlib
import tempfile
import threading
import time
import unittest
from urllib.parse import urlsplit

import requests

from advent import create_app
from advent.local import OllamaError
from advent.local import loadtest
from advent.local.server import create_service_app
from advent.local.service import (
    KeyStore, Limits, ModelQueue, PrivateService, RateLimiter, ServiceError, origin_of,
)


class FakeOllama:
    """Ответ «ok» за пару миллисекунд; gate — чтобы задержать ответ в тесте очереди."""

    url = "http://ollama.test"

    def __init__(self, gate=None, fail=False):
        self.gate = gate
        self.fail = fail
        self.calls = []

    def version(self):
        return "0.test"

    def chat_stream(self, model, messages, *, think=False, options=None):
        self.calls.append({"model": model, "messages": messages, "think": think, "options": options})
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail:
            raise OllamaError("Ollama недоступна")
        time.sleep(0.01)
        yield {"type": "thinking", "text": "думаю"}
        yield {"type": "delta", "text": "o"}
        yield {"type": "delta", "text": "k"}
        yield {"type": "done", "output_tokens": 2, "output_tps": 50.0, "prompt_tokens": 10, "load_s": 0.0, "wall_s": 0.01, "done_reason": "stop"}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def lines(response):
    return [json.loads(line) for line in response.get_data(as_text=True).splitlines() if line]


class ServicePartsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = pathlib.Path(directory.name) / "day30" / "keys.json"

    def test_keys_are_issued_found_and_revoked(self):
        store = KeyStore(self.path)
        item = store.add("  телефон  ")

        self.assertEqual(item["name"], "телефон")
        self.assertTrue(item["key"].startswith("sk-"))
        self.assertTrue(store.admin().startswith("adm-"))
        self.assertEqual(store.find(item["key"])["name"], "телефон")
        self.assertIsNone(store.find("sk-wrong"))
        self.assertIsNone(store.find(""))
        with self.assertRaisesRegex(ValueError, "уже есть"):
            store.add("Телефон")
        with self.assertRaisesRegex(ValueError, "от 1 до 32"):
            store.add("<script>")
        self.assertTrue(store.remove("телефон"))
        self.assertFalse(store.remove("телефон"))
        self.assertIsNone(store.find(item["key"]))

    def test_admin_key_survives_and_broken_file_is_empty(self):
        store = KeyStore(self.path)
        admin = store.admin()
        self.assertEqual(KeyStore(self.path).admin(), admin)

        self.path.write_text("не json", encoding="utf-8")
        self.assertEqual(KeyStore(self.path).keys(), [])

    def test_rate_limit_is_a_sliding_minute_per_client(self):
        clock = Clock()
        limiter = RateLimiter(2, clock=clock)

        self.assertIsNone(limiter.hit("а"))
        clock.now += 10
        self.assertIsNone(limiter.hit("а"))
        self.assertEqual(limiter.hit("а"), 50)
        self.assertIsNone(limiter.hit("б"))
        self.assertEqual(limiter.used("а"), 2)
        clock.now += 50
        self.assertIsNone(limiter.hit("а"))
        self.assertEqual(limiter.used("а"), 2)

    def test_queue_gives_places_in_order_and_refuses_when_full(self):
        line = ModelQueue(1)
        first = line.enter("а", {})
        second = line.enter("б", {})

        self.assertEqual(line.position(first), 0)
        self.assertEqual(line.position(second), 1)
        with self.assertRaises(ServiceError) as caught:
            line.enter("в", {})
        self.assertEqual(caught.exception.status, 503)
        line.start(first)
        snapshot = line.snapshot()
        self.assertEqual(snapshot["active"]["client"], "а")
        self.assertEqual([item["client"] for item in snapshot["waiting"]], ["б"])
        line.leave(first)
        self.assertEqual(line.wait(second, 0.1), 0)
        self.assertEqual(line.peak, 2)

    def test_origin_shows_device_and_masks_address(self):
        local = origin_of({"User-Agent": "curl/8.0"}, "127.0.0.1")
        phone = origin_of({"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)", "CF-Connecting-IP": "85.140.12.34"}, "127.0.0.1")

        self.assertEqual(local, {"device": "curl", "address": "127.0.0.1", "via": "localhost"})
        self.assertEqual(phone, {"device": "iPhone", "address": "85.140.x.x", "via": "интернет"})


class ServiceAppTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.keys = KeyStore(pathlib.Path(directory.name) / "keys.json")
        self.key = self.keys.add("ноутбук")["key"]
        self.ollama = FakeOllama()
        self.service = PrivateService(keys=self.keys, client=self.ollama, limits=Limits(rate_per_minute=3, max_message_chars=50, max_context_chars=80, max_messages=4, queue_size=2))
        self.app = create_service_app(self.service)
        self.client = self.app.test_client()

    def auth(self, key=None):
        return {"Authorization": f"Bearer {key or self.key}"}

    def test_chat_page_and_health_need_no_key(self):
        page = self.client.get("/")
        html = page.get_data(as_text=True)

        self.assertEqual(page.status_code, 200)
        self.assertIn("Ключ доступа", html)
        self.assertIn("js/service.js", html)
        self.assertIn("noindex", html)
        self.assertEqual(self.client.get("/api/health").get_json(), {"ok": True, "model": "gpt-oss:20b", "busy": False, "waiting": 0})

    def test_without_key_answer_is_401(self):
        for headers in ({}, self.auth("sk-wrong")):
            response = self.client.post("/api/chat", json={"message": "Привет"}, headers=headers)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.get_json()["code"], "bad_key")
        self.assertEqual(self.ollama.calls, [])

    def test_me_tells_name_and_usage(self):
        self.client.post("/api/chat", json={"message": "Привет", "stream": False}, headers=self.auth())
        data = self.client.get("/api/me", headers=self.auth()).get_json()

        self.assertEqual(data["name"], "ноутбук")
        self.assertEqual(data["used"], 1)
        self.assertEqual(data["limits"]["rate_per_minute"], 3)

    def test_stream_goes_queue_start_deltas_done(self):
        response = self.client.post("/api/chat", json={"message": "Привет"}, headers=self.auth())
        events = lines(response)

        self.assertEqual(response.mimetype, "application/x-ndjson")
        self.assertEqual([event["type"] for event in events], ["queued", "start", "delta", "delta", "done"])
        self.assertEqual(events[0]["position"], 0)
        self.assertEqual("".join(event["text"] for event in events if event["type"] == "delta"), "ok")
        call = self.ollama.calls[0]
        self.assertEqual(call["messages"][0]["role"], "system")
        self.assertEqual(call["messages"][-1], {"role": "user", "content": "Привет"})
        self.assertEqual(call["think"], "low")
        self.assertEqual(call["options"]["num_ctx"], 8192)
        self.assertEqual(call["options"]["num_predict"], 1500)

    def test_whole_answer_without_stream(self):
        response = self.client.post("/api/chat", json={"messages": [{"role": "user", "content": "Привет"}], "stream": False}, headers=self.auth())

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["answer"], "ok")
        self.assertEqual(data["queue_position"], 0)
        self.assertEqual(data["output_tokens"], 2)

    def test_max_context_and_bad_body(self):
        cases = [
            ({"message": "x" * 51}, 413, "длиннее 50"),
            ({"messages": [{"role": "user", "content": "x" * 45}, {"role": "assistant", "content": "y" * 45}, {"role": "user", "content": "?"}]}, 413, "Диалог длиннее 80"),
            ({"messages": [{"role": "user", "content": "a"}] * 5}, 413, "больше 4 сообщений"),
            ({"message": "  "}, 400, "Пустое"),
            ({"messages": [{"role": "system", "content": "a"}]}, 400, "user | assistant"),
            ({"messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]}, 400, "от user"),
        ]
        for body, status, text in cases:
            response = self.client.post("/api/chat", json=body, headers=self.auth())
            self.assertEqual(response.status_code, status, body)
            self.assertIn(text, response.get_json()["error"])
        self.assertEqual(self.ollama.calls, [])
        self.assertEqual(self.service.rate.used("ноутбук"), 0)

    def test_rate_limit_answers_429_with_retry_after(self):
        for _ in range(3):
            self.assertEqual(self.client.post("/api/chat", json={"message": "a", "stream": False}, headers=self.auth()).status_code, 200)
        response = self.client.post("/api/chat", json={"message": "a"}, headers=self.auth())

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.get_json()["code"], "rate_limited")
        self.assertGreaterEqual(int(response.headers["Retry-After"]), 1)

    def test_second_client_waits_in_queue_while_first_is_answered(self):
        gate = threading.Event()
        self.ollama.gate = gate
        second_key = self.keys.add("телефон")["key"]
        results = {}

        def ask(name, key):
            response = self.app.test_client().post("/api/chat", json={"message": name}, headers=self.auth(key))
            results[name] = lines(response)

        first = threading.Thread(target=ask, args=("первый", self.key))
        first.start()
        time.sleep(0.1)
        second = threading.Thread(target=ask, args=("второй", second_key))
        second.start()
        time.sleep(0.1)
        snapshot = self.service.queue.snapshot()
        self.assertEqual(snapshot["active"]["client"], "ноутбук")
        self.assertEqual([item["client"] for item in snapshot["waiting"]], ["телефон"])
        gate.set()
        first.join(5)
        second.join(5)

        self.assertEqual(results["первый"][0], {"type": "queued", "position": 0})
        self.assertEqual(results["второй"][0], {"type": "queued", "position": 1})
        self.assertEqual(results["второй"][-1]["type"], "done")
        self.assertGreater(results["второй"][-1]["waited_s"], 0)
        self.assertEqual(self.service.queue.snapshot()["active"], None)

    def test_model_error_is_an_event_and_frees_the_queue(self):
        self.ollama.fail = True
        events = lines(self.client.post("/api/chat", json={"message": "a"}, headers=self.auth()))

        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(events[-1]["code"], "model_error")
        self.assertEqual(self.service.queue.snapshot()["waiting"], [])
        self.assertEqual(self.service.log.counts["error"], 1)

    def test_admin_status_needs_admin_key_and_shows_journal(self):
        self.client.post("/api/chat", json={"message": "a"}, headers={"User-Agent": "Mozilla/5.0 (iPhone)", "CF-Connecting-IP": "85.140.1.2"})
        self.client.post("/api/chat", json={"message": "a", "stream": False}, headers=self.auth())

        self.assertEqual(self.client.get("/admin/status").status_code, 403)
        self.assertEqual(self.client.get("/admin/status", headers={"X-Admin-Token": self.key}).status_code, 403)
        data = self.client.get("/admin/status", headers={"X-Admin-Token": self.keys.admin()}).get_json()
        kinds = [event["kind"] for event in data["events"]]
        self.assertEqual(kinds, ["rejected", "queued", "start", "done"])
        self.assertEqual(data["events"][0]["origin"], {"device": "iPhone", "address": "85.140.x.x", "via": "интернет"})
        self.assertEqual(data["events"][0]["status"], 401)
        self.assertEqual(data["counts"]["rejected_401"], 1)
        self.assertEqual(data["clients"], [{"name": "ноутбук", "test": False, "used": 1}])
        later = self.client.get("/admin/status?after=3", headers={"X-Admin-Token": self.keys.admin()}).get_json()
        self.assertEqual([event["kind"] for event in later["events"]], ["done"])


class FlaskHttp:
    """requests-подобный клиент поверх тестового клиента сервиса — для проверки нагрузки."""

    def __init__(self, app):
        self.app = app

    def get(self, url, headers=None, params=None, timeout=None):
        return FlaskResponse(self.app.test_client().get(urlsplit(url).path, headers=headers, query_string=params))

    def post(self, url, headers=None, json=None, stream=False, timeout=None):
        return FlaskResponse(self.app.test_client().post(urlsplit(url).path, headers=headers, json=json))


class FlaskResponse:
    def __init__(self, response):
        self.response = response
        self.status_code = response.status_code

    def json(self):
        return self.response.get_json()

    def iter_lines(self):
        return self.response.get_data().splitlines()

    def close(self):
        self.response.close()


class DayThirtyTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.keys_path = pathlib.Path(directory.name) / "day30" / "keys.json"
        self.service = PrivateService(keys=KeyStore(self.keys_path), client=FakeOllama())
        self.service_app = create_service_app(self.service)
        self.app = create_app({
            "TESTING": True,
            "OLLAMA_CLIENT": FakeOllama(),
            "AGENT_STORE_DIR": str(pathlib.Path(directory.name) / "agents"),
            "DAY30_KEYS_PATH": str(self.keys_path),
            "DAY30_SERVICE_URL": "http://service.test",
            "DAY30_HTTP": FlaskHttp(self.service_app),
        })
        self.client = self.app.test_client()

    def test_page_lists_owner_keys_but_not_test_keys(self):
        store = KeyStore(self.keys_path)
        store.add("телефон")
        store.add("тест-А-1", test=True)
        html = self.client.get("/days/30/").get_data(as_text=True)

        self.assertIn('data-key-row="телефон"', html)
        self.assertNotIn("тест-А-1", html)
        self.assertIn("js/day30.js", html)
        self.assertIn(".venv/bin/python service.py", html)
        self.assertIn("Ollama 0.test", html)

    def test_keys_are_issued_and_revoked_from_the_page(self):
        created = self.client.post("/days/30/api/keys", json={"name": "друг"})
        self.assertEqual(created.status_code, 201)
        key = created.get_json()["key"]
        self.assertEqual(self.client.post("/days/30/api/keys", json={"name": "друг"}).status_code, 400)
        self.assertEqual(self.client.post("/days/30/api/keys", json={}).status_code, 400)

        self.assertEqual(self.service_app.test_client().get("/api/me", headers={"Authorization": f"Bearer {key}"}).status_code, 200)
        self.assertEqual(self.client.delete("/days/30/api/keys/друг").status_code, 200)
        self.assertEqual(self.client.delete("/days/30/api/keys/друг").status_code, 404)
        self.assertEqual(self.service_app.test_client().get("/api/me", headers={"Authorization": f"Bearer {key}"}).status_code, 401)

    def test_status_comes_from_service_or_says_it_is_down(self):
        data = self.client.get("/days/30/api/status").get_json()
        self.assertTrue(data["running"])
        self.assertEqual(data["model"], "gpt-oss:20b")
        self.assertIsNone(data["public_url"])

        class Down:
            def get(self, *args, **kwargs):
                raise requests.ConnectionError("нет")

        self.app.config["DAY30_HTTP"] = Down()
        self.assertEqual(self.client.get("/days/30/api/status").get_json(), {"running": False, "error": "Сервис не запущен"})
        self.assertEqual(self.client.post("/days/30/api/load", json={}).status_code, 409)

    def test_public_load_needs_a_tunnel(self):
        response = self.client.post("/days/30/api/load", json={"target": "public"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("туннель", response.get_json()["error"])

    def test_load_check_queues_answers_and_refuses_by_limits(self):
        events = lines(self.client.post("/days/30/api/load", json={"target": "local"}))
        summary = next(event for event in events if event["type"] == "load_summary")["summary"]

        self.assertEqual(events[0]["type"], "load_start")
        self.assertEqual(events[0]["target"], "http://service.test")
        self.assertEqual(summary["sent"], 10)
        self.assertEqual(summary["answered"], 7)
        self.assertEqual(summary["rejected"], {"429": 1, "413": 1, "401": 1})
        self.assertEqual(summary["errors"], 0)
        self.assertEqual(events[-1], {"type": "done"})
        self.assertEqual(KeyStore(self.keys_path).keys(), [])

    def test_scenario_follows_the_limits(self):
        plan = loadtest.scenario(Limits(max_message_chars=100))
        self.assertGreater(len(plan[8]["message"]), 100)
        self.assertEqual(sum(item["client"] == "А" for item in plan), 6)
        self.assertEqual([item["expect"] for item in plan].count(200), 7)


if __name__ == "__main__":
    unittest.main()

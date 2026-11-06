import json
import unittest
from unittest.mock import patch

from line_bridge.cli import parser, run
from line_bridge.errors import BridgeError
from line_bridge.monitor import Monitor
from line_bridge.notify import Notifier, post_webhook, save_target
from support import environment

# 2026-11-02 12:00 JST (03:00 UTC), outside the default 0-7 quiet hours.
NOON = 1793588400.0


class NotifierTests(unittest.TestCase):
    def setUp(self):
        self.temp, self.root, self.config, self.protector, self.store, self.providers, self.bridge = environment()
        self.config.notify.enabled = True
        self.now = NOON
        self.posts = []
        self.status = 200
        save_target(self.root, self.protector, "https://hooks.example.invalid/routine", "fixture-key")
        self.notifier = Notifier(self.store, self.config, self.root, self.protector, clock=lambda: self.now, post=self.post)
        generation = self.providers["personal"].generation
        self.direct = self.store.reference("personal", "u" + "a" * 32, "Fixture friend", generation)
        self.group = self.store.reference("personal", "c" + "b" * 32, "Fixture busy group", generation)
        self.count = 0

    def tearDown(self):
        self.temp.cleanup()

    def post(self, url, key, body):
        self.posts.append((url, key, json.loads(body)))
        return self.status

    def message(self, chat_ref, text="fixture text", **raw):
        self.count += 1
        message = {"source": "personal", "chat_ref": chat_ref, "chat_name": "Fixture", "message_ref": f"message:{self.count}",
                   "sender": "Fixture sender", "created_ms": int(self.now * 1000), "kind": "text", "plain_text": text}
        return {"id": str(self.count), **raw}, message

    def add(self, chat_ref, direct=False, **raw):
        return self.notifier.enqueue(chat_ref, direct, [self.message(chat_ref, **raw)])

    def advance(self, seconds):
        self.now += seconds
        return self.notifier.evaluate()

    def test_digest_waits_for_the_window(self):
        self.assertEqual(self.notifier.evaluate()["state"], "waiting")
        self.add(self.group)
        self.assertEqual(self.advance(59 * 60)["state"], "waiting")
        result = self.advance(60)
        self.assertEqual((result["state"], result["reason"]), ("sent", "digest"))
        self.assertEqual(self.posts[0][2]["chats"][0]["count"], 1)
        self.assertEqual(self.advance(60)["state"], "waiting")

    def test_direct_message_is_debounced_then_sent_with_pending_digest(self):
        self.add(self.group)
        self.add(self.direct, direct=True)
        self.assertEqual(self.advance(60)["state"], "waiting")
        self.add(self.direct, direct=True)
        result = self.advance(60)
        self.assertEqual((result["reason"], result["messages"]), ("instant", 3))
        body = self.posts[0][2]
        self.assertEqual(body["total"], 3)
        self.assertEqual({c["kind"] for c in body["chats"]}, {"direct", "group"})
        self.assertEqual(self.posts[0][:2], ("https://hooks.example.invalid/routine", "fixture-key"))

    def test_mention_is_urgent_even_in_quiet_hours(self):
        self.now = NOON - 10 * 3600  # 02:00 JST
        self.add(self.direct, direct=True)
        self.assertEqual(self.advance(600)["state"], "waiting")
        self.add(self.group, mentions_me=True)
        result = self.notifier.evaluate()
        self.assertEqual(result["reason"], "urgent")
        chats = self.posts[0][2]["chats"]
        self.assertTrue(chats[0]["urgent"])
        self.assertEqual(chats[0]["previews"][0]["reason"], "mention")

    def test_keyword_and_reply_to_own_message_escalate(self):
        self.add(self.group, text="Is YAMADA around?")
        self.config.notify.keywords = ["yamada"]
        self.add(self.group, text="Is yamada around?")
        own = {"source": "personal", "chat_ref": self.group, "chat_name": "", "sender": "", "created_ms": 1,
               "kind": "text", "native_type": "NONE", "availability": "complete", "reason": None, "plain_text": "mine",
               "content": None, "text_truncated": False, "from_me": True, "unsent": False,
               "message_ref": self.store.reference("personal", self.group + "\0" + "900", "", self.providers["personal"].generation, "message")}
        self.store.archive([own])
        self.add(self.group, reply_to="900")
        self.add(self.group, reply_to="901")
        self.notifier.evaluate()
        reasons = sorted(str(p["reason"]) for p in self.posts[0][2]["chats"][0]["previews"])
        self.assertEqual(reasons, ["None", "keyword", "reply"])

    def test_muted_chat_and_hourly_cap(self):
        self.config.notify.muted_chats = [self.group]
        self.assertEqual(self.add(self.group, mentions_me=True), 0)
        self.config.notify.hourly_cap = 2
        for _ in range(3):
            self.add(self.direct, direct=True, mentions_me=True)
            self.advance(1)
        self.assertEqual(len(self.posts), 2)
        self.assertEqual(self.advance(3600)["state"], "sent")
        self.assertEqual(self.posts[-1][2]["total"], 1)

    def test_failed_delivery_keeps_messages_for_the_next_cycle(self):
        self.status = 500
        self.add(self.direct, direct=True, mentions_me=True)
        self.assertEqual(self.notifier.evaluate()["state"], "delivery_failed")
        self.status = 204
        self.assertEqual(self.advance(1)["messages"], 1)
        self.assertEqual(self.notifier.status()["pending"], {})

    def test_overdue_digest_window_does_not_skip_the_debounce(self):
        self.now += 2 * 3600
        self.add(self.direct, direct=True)
        self.assertEqual(self.notifier.evaluate()["state"], "waiting")
        self.assertEqual(self.advance(120)["reason"], "instant")

    def test_redirect_fails_delivery_without_forwarding_the_key(self):
        import http.server
        import threading
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append((self.path, self.headers.get("Authorization")))
                self.send_response(302)
                self.send_header("Location", "/elsewhere")
                self.end_headers()

            do_GET = do_POST

            def log_message(self, *args):
                pass
        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        try:
            with self.assertRaises(Exception):
                post_webhook(f"http://127.0.0.1:{server.server_port}/hook", "fixture-key", b"{}")
        finally:
            thread.join(10)
            server.server_close()
        self.assertEqual(seen, [("/hook", "Bearer fixture-key")])

    def test_previews_are_bounded(self):
        for _ in range(5):
            self.add(self.group, text="x" * 500)
        self.add(self.group, mentions_me=True)
        self.notifier.evaluate()
        chat = self.posts[0][2]["chats"][0]
        self.assertEqual((chat["count"], len(chat["previews"])), (6, 3))
        self.assertEqual(chat["previews"][0]["reason"], "mention")
        self.assertTrue(all(len(p["text"]) <= 200 for p in chat["previews"]))


class MonitorNotifyTests(unittest.TestCase):
    def setUp(self):
        self.temp, self.root, self.config, self.protector, self.store, self.providers, self.bridge = environment()
        self.ref = self.bridge.personal_chats()["chats"][0]["chat_ref"]
        self.config.monitor.enabled = True
        self.config.monitor.targets = [self.ref]
        self.config.notify.enabled = True
        save_target(self.root, self.protector, "https://hooks.example.invalid/routine", "fixture-key")
        self.monitor = Monitor(self.bridge)
        self.monitor.notifier.post = lambda *args: 200

    def tearDown(self):
        self.temp.cleanup()

    def pending(self):
        return sum(self.monitor.notifier.status()["pending"].values())

    def test_only_new_incoming_messages_after_baseline_are_queued(self):
        self.assertEqual(self.monitor.cycle()["new_for_notify"], 0)
        self.providers["personal"].rows = [{"id": "new", "text": "new fixture", "created_ms": 1, "from_me": False},
                                           {"id": "mine", "text": "own fixture", "created_ms": 2, "from_me": True}]
        self.assertEqual(self.monitor.cycle()["new_for_notify"], 1)
        self.assertEqual(self.pending(), 1)
        self.assertEqual(self.monitor.cycle()["new_for_notify"], 0)

    def test_unchanged_personal_chat_is_not_read(self):
        self.monitor.cycle()
        original = self.providers["personal"].read

        def read(operation, args):
            if operation == "chats":
                return [{"chat_id": "normal-room", "name": "Fixture normal room", "last_message_id": "m1"}]
            return original(operation, args)
        self.providers["personal"].read = read
        self.providers["personal"].calls.clear()
        self.assertEqual(self.monitor.cycle()["unchanged_chats_skipped"], 1)
        self.assertNotIn("messages", [c[0] for c in self.providers["personal"].calls])


class NotifyCliTests(unittest.TestCase):
    def setUp(self):
        self.temp, self.root, self.config, self.protector, self.store, self.providers, self.bridge = environment()
        self.config.save(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *argv, key="fixture-key"):
        args = parser().parse_args(["--data-dir", str(self.root), "notify", *argv])
        with patch("line_bridge.cli.load", return_value=(self.config, self.protector)), \
                patch("line_bridge.cli.check_local_path", side_effect=lambda p: p), \
                patch.dict("os.environ", {"LINEBRIDGE_NOTIFY_KEY": key}):
            return run(args)

    def test_enable_requires_monitor_and_https(self):
        with self.assertRaisesRegex(BridgeError, "enable_monitor_first"):
            self.run_cli("enable", "--url", "https://hooks.example.invalid/x")
        self.config.monitor.enabled = True
        with self.assertRaisesRegex(BridgeError, "notify_url_must_be_https"):
            self.run_cli("enable", "--url", "http://hooks.example.invalid/x")
        result = self.run_cli("enable", "--url", "https://hooks.example.invalid/x", "--quiet-hours", "23-6")
        self.assertTrue(result["notify_enabled"])
        self.assertEqual((result["quiet_start"], result["quiet_end"], result["digest_minutes"]), (23, 6, 60))
        self.assertNotIn(b"fixture-key", (self.root / "notify-webhook.dpapi").read_bytes())
        before = (self.root / "notify-webhook.dpapi").read_bytes()
        with self.assertRaisesRegex(BridgeError, "invalid_notify_limits"):
            self.run_cli("enable", "--url", "https://other.example.invalid/x", "--quiet-hours", "25-6")
        self.assertEqual((self.root / "notify-webhook.dpapi").read_bytes(), before)
        self.assertFalse(self.run_cli("disable")["notify_enabled"])


if __name__ == "__main__":
    unittest.main()

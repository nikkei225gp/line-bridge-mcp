import threading
import unittest
import uuid
from unittest.mock import patch

from line_bridge.errors import BridgeError
from line_bridge.sending import Sending
from line_bridge.providers import PERSONAL_READS, NativeDesktop, NativeDesktopPersonal, NativeOpenChat, make_providers
from support import environment


class SendingTests(unittest.TestCase):
    source = "openchat"

    def setUp(self):
        self.temp, self.root, self.config, self.protector, self.store, self.providers, self.bridge = environment()
        self.config.sending_enabled = True
        self.provider = self.providers[self.source]
        self.sender = Sending(self.store, self.config, self.providers, clock=lambda: 1000)
        self.ref = (self.bridge.openchat_listing()["chats"][0]["chat_ref"] if self.source == "openchat" else
                    self.store.reference("personal", "u" + "b" * 32, "Fixture personal chat", self.provider.generation))
        self.request = str(uuid.uuid4())

    def tearDown(self):
        self.temp.cleanup()

    def send(self, request_id=None, text="Exact fixture text", ref=None):
        return self.sender.send(ref or self.ref, text, request_id or self.request)

    def archived(self):
        with self.store.db() as db:
            return [self.store.open(row[0]) for row in db.execute("SELECT payload FROM cache")]

    def test_send_sends_once(self):
        self.assertEqual(self.send()["state"], "sent_confirmed")
        self.assertEqual(len(self.provider.send_calls), 1)

    def test_disabled_denied(self):
        self.config.sending_enabled = False
        with self.assertRaises(BridgeError):
            self.send()

    def test_invalid_uuid_denied(self):
        with self.assertRaises(BridgeError):
            self.sender.send(self.ref, "text", "../invalid")

    def test_utf16_limit(self):
        with self.assertRaises(BridgeError):
            self.sender.send(self.ref, "😀" * 2501, self.request)

    def test_lone_surrogate_text_is_rejected(self):
        with self.assertRaises(BridgeError):
            self.sender.send(self.ref, "\ud800", self.request)

    def test_uuid_is_idempotent(self):
        self.assertEqual(self.send()["state"], "sent_confirmed")
        self.assertEqual(self.send()["state"], "duplicate_request_not_sent")
        self.assertEqual(len(self.provider.send_calls), 1)

    def test_uuid_content_binding(self):
        self.send()
        with self.assertRaises(BridgeError) as error:
            self.sender.send(self.ref, "Different text", self.request)
        self.assertEqual(error.exception.code, "uuid_already_bound_to_different_content")
        self.assertEqual(len(self.provider.send_calls), 1)

    def test_unknown_never_resent(self):
        self.provider.send_fail = True
        self.assertEqual(self.send()["state"], "send_outcome_unknown_no_retry")
        self.assertEqual(self.send()["state"], "duplicate_request_not_sent")
        self.assertEqual(len(self.provider.send_calls), 1)

    def test_reservation_survives_new_sender(self):
        self.provider.send_fail = True
        self.send()
        fresh = Sending(self.store, self.config, self.providers, clock=lambda: 1001)
        self.assertEqual(fresh.send(self.ref, "Exact fixture text", self.request)["state"], "duplicate_request_not_sent")

    def test_concurrent_send_only_one(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.send())) for _ in range(5)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(self.provider.send_calls), 1)
        self.assertEqual(len(results), 5)

    def test_destination_rename_does_not_send(self):
        self.provider.rename = True
        self.assertEqual(self.send()["state"], "not_sent_destination_changed")

    def test_send_does_not_reread_destination(self):
        self.provider.calls.clear()
        self.assertEqual(self.send()["state"], "sent_confirmed")
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(set(self.provider.send_calls[0]), {"chat_id", "text", "expected_generation", "sequence"})

    def test_back_to_back_sends_and_sequence(self):
        self.assertEqual(self.send()["state"], "sent_confirmed")
        self.assertEqual(self.sender.send(self.ref, "Another text", str(uuid.uuid4()))["state"], "sent_confirmed")
        self.assertEqual([c["sequence"] for c in self.provider.send_calls], [1, 2])

    def test_text_encrypted_on_disk(self):
        self.send()
        self.assertNotIn(b"Exact fixture text", self.store.path.read_bytes())

    def test_no_session_or_text_in_send_results(self):
        self.provider.send_result = {"state": "sent_confirmed", "text": "private fixture", "session": "private fixture"}
        self.assertEqual(self.send(), {"state": "sent_confirmed", "request_id": self.request})

    def test_unknown_provider_state_is_unknown_without_retry(self):
        self.provider.send_result = {"state": "unexpected fixture text"}
        self.assertEqual(self.send()["state"], "send_outcome_unknown_no_retry")
        self.assertEqual(self.send()["state"], "duplicate_request_not_sent")
        self.assertEqual(len(self.provider.send_calls), 1)

    def test_sequence_exhaustion_fails_before_send(self):
        self.store.put_meta("send_sequence", {"next": 2147483648, "last": 0})
        with self.assertRaises(BridgeError) as error:
            self.send()
        self.assertEqual(error.exception.code, "send_sequence_exhausted")
        self.assertEqual(self.provider.send_calls, [])

    def test_confirmed_outgoing_is_archived(self):
        self.provider.send_result = {"state": "sent_confirmed", "message_id": "123", "created_ms": 1700000000000}
        self.assertEqual(self.send(), {"state": "sent_confirmed", "request_id": self.request})
        messages = self.archived()
        self.assertEqual(len(messages), 1)
        message = messages[0]
        self.assertEqual(set(message), {"source", "chat_ref", "chat_name", "message_ref", "sender", "created_ms",
                                        "kind", "native_type", "availability", "reason", "plain_text", "content",
                                        "text_truncated", "from_me", "unsent"})
        self.assertEqual(message["source"], self.source)
        self.assertEqual(message["chat_ref"], self.ref)
        self.assertEqual(message["created_ms"], 1700000000000)
        self.assertEqual(message["plain_text"], "Exact fixture text")
        self.assertEqual(message["kind"], "text")
        self.assertEqual(message["native_type"], "NONE")
        self.assertEqual(message["availability"], "complete")
        self.assertIsNone(message["reason"])
        self.assertIsNone(message["content"])
        self.assertTrue(message["from_me"])
        self.assertFalse(message["text_truncated"])
        self.assertFalse(message["unsent"])
        self.assertEqual(self.send()["state"], "duplicate_request_not_sent")
        self.assertEqual(len(self.archived()), 1)
        self.assertEqual(len(self.provider.send_calls), 1)
        with self.store.db() as db:
            recorded = self.store.open(db.execute("SELECT result FROM sends WHERE request_id=?", (self.request,)).fetchone()[0])
        self.assertEqual(recorded, {"state": "sent_confirmed"})

    def test_unknown_outcome_with_message_id_is_not_archived(self):
        self.provider.send_result = {"state": "send_outcome_unknown_no_retry", "message_id": "123", "created_ms": 1700000000000}
        self.assertEqual(self.send()["state"], "send_outcome_unknown_no_retry")
        self.assertEqual(self.archived(), [])

    def test_confirmed_without_usable_id_never_fabricates_archive_reference(self):
        for index, message_id in enumerate((None, "", "synthetic:fixture", "0", "0123", "-1", 123, "9223372036854775808", "1١")):
            with self.subTest(message_id=message_id):
                self.request = str(uuid.uuid4())
                self.sender.clock = lambda: 1000 + index * 11
                self.provider.send_result = {"state": "sent_confirmed", "message_id": message_id}
                self.assertEqual(self.send()["state"], "sent_confirmed")
                self.assertEqual(self.archived(), [])
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM refs WHERE kind='message'").fetchone()[0], 0)

    def test_confirmed_without_timestamp_is_archived_without_local_time_substitution(self):
        self.provider.send_result = {"state": "sent_confirmed", "message_id": "123"}
        self.send()
        self.assertEqual(self.archived()[0]["created_ms"], 0)

    def test_archive_failure_preserves_reservation_and_never_resends(self):
        self.provider.send_result = {"state": "sent_confirmed", "message_id": "123", "created_ms": 1700000000000}
        with patch.object(self.store, "archive", side_effect=OSError("Fixture archive unavailable")):
            with self.assertRaises(OSError):
                self.send()
        self.assertEqual(self.send()["state"], "duplicate_request_not_sent")
        self.assertEqual(len(self.provider.send_calls), 1)
        self.assertEqual(self.archived(), [])

    def test_source_neutral_facade(self):
        self.bridge.sending.clock = self.sender.clock
        result = self.bridge.call("local", "send", {"chat_ref": self.ref, "text": "Exact fixture text", "request_id": self.request})
        self.assertEqual(result, {"state": "sent_confirmed", "request_id": self.request})
        self.assertEqual(self.bridge.call("local", "send", {"chat_ref": self.ref, "text": "Exact fixture text", "request_id": self.request})["state"],
                         "duplicate_request_not_sent")


class PersonalSendingTests(SendingTests):
    source = "personal"

    def test_joined_group_routes_to_personal(self):
        self.ref = self.store.reference("personal", "c" + "c" * 32, "Fixture group", self.provider.generation)
        self.assertEqual(self.send()["state"], "sent_confirmed")
        self.assertEqual(self.provider.send_calls[0]["chat_id"], "c" + "c" * 32)

    def test_legacy_room_is_not_a_send_destination(self):
        self.ref = self.store.reference("personal", "r" + "c" * 32, "Unsupported room", self.provider.generation)
        with self.assertRaises(BridgeError):
            self.send()
        self.assertEqual(self.provider.send_calls, [])

    def test_e2ee_unavailable_is_terminal_without_retry(self):
        self.provider.send_result = {"state": "not_sent_e2ee_unavailable"}
        self.assertEqual(self.send()["state"], "not_sent_e2ee_unavailable")
        self.assertEqual(self.send()["state"], "duplicate_request_not_sent")
        self.assertEqual(len(self.provider.send_calls), 1)

    def test_stale_personal_chat_ref_is_rejected(self):
        self.provider.generation = "changed-login"
        with self.assertRaises(BridgeError):
            self.send()

    def test_contact_ref_cannot_be_used_as_chat_ref(self):
        self.ref = self.store.reference("personal", "u" + "b" * 32, "Fixture contact", self.provider.generation, kind="contact")
        with self.assertRaises(BridgeError):
            self.send()

    def test_native_personal_without_send_capability_is_rejected(self):
        self.provider.send_once = None
        with self.assertRaises(BridgeError) as error:
            self.send()
        self.assertEqual(error.exception.code, "sending_disabled")

    def test_sequence_is_shared_across_sources(self):
        self.assertEqual(self.send()["state"], "sent_confirmed")
        ref = self.bridge.openchat_listing()["chats"][0]["chat_ref"]
        self.assertEqual(self.sender.send(ref, "Different source", str(uuid.uuid4()))["state"], "sent_confirmed")
        self.assertEqual(self.provider.send_calls[0]["sequence"], 1)
        self.assertEqual(self.providers["openchat"].send_calls[0]["sequence"], 2)


class PersonalProviderSendingTests(unittest.TestCase):
    def test_personal_send_has_dedicated_action(self):
        class Desktop:
            generation = "fixture-generation"

            def __init__(self):
                self.calls = []

            def _call(self, action, arguments):
                self.calls.append((action, arguments))
                return {"result": {"id": "fixture-id"}} if action == "personal" else {"state": "sent_confirmed"}

        desktop = Desktop()
        personal = NativeDesktopPersonal(desktop)
        self.assertEqual(personal.read("chat", {"chat_id": "fixture-id"}), {"id": "fixture-id"})
        self.assertEqual(personal.send_once({"text": "mock"}), {"state": "sent_confirmed"})
        self.assertEqual([c[0] for c in desktop.calls], ["personal", "personal_send"])
        self.assertEqual(PERSONAL_READS, {"chats", "chat", "messages", "find_contact", "groups", "contact_chats", "members"})
        for operation in ("send_destination", "send", "send_message", "join", "mark_read", "login", "profile", "group_members", "last_interaction", "context", "receipts", "image"):
            with self.assertRaises(BridgeError):
                personal.read(operation, {})

    def test_worker_failure_is_unknown_and_not_retried(self):
        temp, root, config, protector, _, _, _ = environment()
        try:
            desktop = NativeDesktop(root, config, protector)
            with patch.object(desktop, "credentials", return_value={"bridge_generation": "mock"}), \
                    patch.object(desktop, "_start", side_effect=TimeoutError("private detail")) as start, \
                    patch.object(desktop, "close"):
                self.assertEqual(NativeDesktopPersonal(desktop).send_once({"expected_generation": "mock"}), {"state": "send_outcome_unknown_no_retry"})
                self.assertEqual(start.call_count, 1)
        finally:
            temp.cleanup()

    def test_native_modes_require_unified_generation_without_loading_old_sessions(self):
        temp, root, config, protector, _, _, _ = environment()
        try:
            with patch("line_bridge.providers.load_secret") as read:
                with self.assertRaises(BridgeError) as error:
                    make_providers(root, config, protector)
                self.assertEqual(error.exception.code, "unified_auth_required")
                with self.assertRaises(BridgeError) as error:
                    NativeOpenChat(root, config, protector).credentials()
                self.assertEqual(error.exception.code, "unified_auth_required")
                read.assert_not_called()
        finally:
            temp.cleanup()

    def test_unified_factory_keeps_one_process_and_only_line_hosts(self):
        temp, root, config, protector, _, _, _ = environment()
        try:
            config.unified_generation = str(uuid.uuid4())
            providers = make_providers(root, config, protector)
            self.assertIs(providers["personal"].desktop, providers["openchat"])
            self.assertEqual(providers["openchat"].network_hosts, "legy.line-apps.com:443,gf.line.naver.jp:443,obs.line-apps.com:443")
        finally:
            temp.cleanup()

    def test_worker_rechecks_generation_before_starting_a_send(self):
        temp, root, config, protector, _, _, _ = environment()
        try:
            desktop = NativeDesktop(root, config, protector)
            with patch.object(desktop, "credentials", return_value={"bridge_generation": "new-login"}), \
                    patch.object(desktop, "_start") as start:
                result = NativeDesktopPersonal(desktop).send_once({"expected_generation": "approved-login"})
                self.assertEqual(result, {"state": "not_sent_destination_changed"})
                start.assert_not_called()
        finally:
            temp.cleanup()

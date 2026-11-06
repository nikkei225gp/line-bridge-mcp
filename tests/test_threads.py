import unittest
import uuid

from line_bridge.errors import BridgeError
from line_bridge.rpc import Rpc
from support import environment


class ThreadTests(unittest.TestCase):
    def setUp(self):
        self.temp, self.root, self.config, self.protector, self.store, self.providers, self.bridge = environment()
        self.config.sending_enabled = True
        self.provider = self.providers["openchat"]
        self.chat = self.bridge.openchat_listing()["chats"][0]["chat_ref"]
        self.provider.rows = [{"id": "301", "text": "root", "created_ms": 10, "thread": {"reply_count": 2, "last_reply_ms": 30}},
                              {"id": "302", "text": "plain", "created_ms": 11}]
        messages = self.bridge.messages("openchat", self.chat, 10)["messages"]
        self.root_message, self.plain = messages

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def call(self, name, arguments):
        return Rpc(self.bridge).dispatch("local", "tools/call", {"name": name, "arguments": arguments})

    def test_thread_summary_is_shown_only_on_messages_with_a_thread(self):
        self.assertEqual(self.root_message["thread"], {"reply_count": 2, "last_reply_ms": 30})
        self.assertNotIn("thread", self.plain)

    def test_thread_replies_are_read_and_saved_under_their_root(self):
        self.provider.rows = [{"id": "311", "text": "reply", "created_ms": 20, "sender": "Fixture member"}]
        response = self.call("openchat_thread_messages", {"message_ref": self.root_message["message_ref"]})
        self.assertFalse(response["isError"])
        result = response["structuredContent"]
        self.assertEqual(self.provider.calls[-1], ("thread_messages", {"chat_id": "open-room", "message_id": "301", "limit": 20}))
        self.assertTrue(result["thread_exists"])
        self.assertEqual((result["chat_ref"], result["thread_root_ref"]), (self.chat, self.root_message["message_ref"]))
        self.assertEqual([m["thread_root_ref"] for m in result["messages"]], [self.root_message["message_ref"]])
        saved = self.bridge.archive_messages({"query": "reply"})["messages"]
        self.assertEqual(saved[0]["thread_root_ref"], self.root_message["message_ref"])

    def test_personal_message_cannot_root_a_thread(self):
        personal = self.bridge.personal_chats()["chats"][0]["chat_ref"]
        message = self.bridge.messages("personal", personal, 10)["messages"][0]
        with self.assertRaises(BridgeError):
            self.bridge.openchat_thread(message["message_ref"], 20)
        direct = self.store.reference("personal", "u" + "b" * 32, "Fixture direct chat", self.providers["personal"].generation)
        with self.assertRaises(BridgeError) as error:
            self.bridge.sending.send(direct, "text", str(uuid.uuid4()), message["message_ref"])
        self.assertEqual(error.exception.code, "threads_are_openchat_only")
        self.assertEqual(self.providers["personal"].send_calls, [])

    def test_thread_reply_is_sent_under_the_root_and_archived_there(self):
        self.provider.send_result = {"state": "sent_confirmed", "message_id": "321", "created_ms": 40}
        response = self.call("send", {"chat_ref": self.chat, "text": "Exact reply", "request_id": str(uuid.uuid4()),
                                      "thread_message_ref": self.root_message["message_ref"]})
        self.assertEqual(response["structuredContent"]["state"], "sent_confirmed")
        self.assertEqual(self.provider.send_calls[0]["thread_message_id"], "301")
        saved = self.bridge.archive_messages({"query": "Exact reply"})["messages"]
        self.assertEqual(saved[0]["thread_root_ref"], self.root_message["message_ref"])

    def test_root_from_another_chat_is_refused_before_sending(self):
        other = self.store.reference("openchat", "other-room", "Other", self.provider.generation)
        with self.assertRaises(BridgeError) as error:
            self.bridge.sending.send(other, "text", str(uuid.uuid4()), self.root_message["message_ref"])
        self.assertEqual(error.exception.code, "message_belongs_to_different_chat")
        self.assertEqual(self.provider.send_calls, [])

    def test_thread_lookup_failure_is_a_definite_non_send(self):
        self.provider.send_result = {"state": "not_sent_thread_unavailable"}
        result = self.bridge.sending.send(self.chat, "text", str(uuid.uuid4()), self.root_message["message_ref"])
        self.assertEqual(result["state"], "not_sent_thread_unavailable")

    def test_same_uuid_cannot_move_between_thread_and_chat(self):
        request = str(uuid.uuid4())
        self.bridge.sending.send(self.chat, "text", request, self.root_message["message_ref"])
        with self.assertRaises(BridgeError) as error:
            self.bridge.sending.send(self.chat, "text", request)
        self.assertEqual(error.exception.code, "uuid_already_bound_to_different_content")


class QuoteReplyTests(ThreadTests):
    """Quote replies reuse the thread fixtures: one saved OpenChat chat, and a saved personal one."""

    def test_quote_reply_passes_the_answered_message_and_reports_the_echo(self):
        self.provider.send_result = {"state": "sent_confirmed", "message_id": "321", "created_ms": 40, "reply_confirmed": True}
        response = self.call("send", {"chat_ref": self.chat, "text": "Quoted", "request_id": str(uuid.uuid4()),
                                      "reply_to_message_ref": self.plain["message_ref"]})
        self.assertEqual(response["structuredContent"]["state"], "sent_confirmed")
        self.assertTrue(response["structuredContent"]["reply_confirmed"])
        self.assertEqual(self.provider.send_calls[0]["reply_to"], "302")
        self.assertNotIn("thread_message_id", self.provider.send_calls[0])

    def test_unechoed_quote_is_reported_not_assumed(self):
        self.provider.send_result = {"state": "sent_confirmed", "message_id": "322"}
        result = self.bridge.sending.send(self.chat, "Quoted", str(uuid.uuid4()), "", self.plain["message_ref"])
        self.assertIs(result["reply_confirmed"], False)
        self.provider.send_result = {"state": "send_outcome_unknown_no_retry"}
        result = self.bridge.sending.send(self.chat, "Quoted again", str(uuid.uuid4()), "", self.plain["message_ref"])
        self.assertNotIn("reply_confirmed", result)

    def test_plain_send_has_no_reply_fields(self):
        result = self.bridge.sending.send(self.chat, "Plain", str(uuid.uuid4()))
        self.assertNotIn("reply_confirmed", result)
        self.assertNotIn("reply_to", self.provider.send_calls[0])

    def test_quote_reply_and_thread_reply_cannot_be_combined(self):
        with self.assertRaises(BridgeError) as error:
            self.bridge.sending.send(self.chat, "x", str(uuid.uuid4()), self.root_message["message_ref"], self.plain["message_ref"])
        self.assertEqual(error.exception.code, "reply_and_thread_are_exclusive")
        self.assertEqual(self.provider.send_calls, [])

    def test_quoted_message_must_belong_to_the_same_chat(self):
        other = self.store.reference("openchat", "other-room", "Other", self.provider.generation)
        with self.assertRaises(BridgeError) as error:
            self.bridge.sending.send(other, "x", str(uuid.uuid4()), "", self.plain["message_ref"])
        self.assertEqual(error.exception.code, "message_belongs_to_different_chat")
        with self.assertRaises(BridgeError):
            self.bridge.sending.send(self.chat, "x", str(uuid.uuid4()), "", "message:v1:unknown")
        self.assertEqual(self.provider.send_calls, [])

    def test_personal_quote_reply(self):
        direct = self.store.reference("personal", "u" + "b" * 32, "Fixture direct chat", self.providers["personal"].generation)
        message = self.store.reference("personal", direct + "\0" + "900", "", self.providers["personal"].generation, "message")
        self.bridge.sending.send(direct, "Quoted", str(uuid.uuid4()), "", message)
        self.assertEqual(self.providers["personal"].send_calls[0]["reply_to"], "900")

    def test_same_uuid_cannot_move_between_quote_and_plain(self):
        request = str(uuid.uuid4())
        self.bridge.sending.send(self.chat, "text", request, "", self.plain["message_ref"])
        with self.assertRaises(BridgeError) as error:
            self.bridge.sending.send(self.chat, "text", request)
        self.assertEqual(error.exception.code, "uuid_already_bound_to_different_content")


if __name__ == "__main__":
    unittest.main()

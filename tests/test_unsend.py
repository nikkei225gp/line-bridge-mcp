import unittest
import uuid

from line_bridge.catalog import available
from line_bridge.errors import BridgeError
from support import environment


class UnsendTests(unittest.TestCase):
    def setUp(self):
        self.temp, self.root, self.config, self.protector, self.store, self.providers, self.bridge = environment()
        self.config.sending_enabled = True
        self.openchat = self.providers["openchat"]
        self.chat = self.bridge.openchat_listing()["chats"][0]["chat_ref"]
        self.openchat.rows = [{"id": "401", "text": "ours", "created_ms": 10, "from_me": True},
                              {"id": "402", "text": "theirs", "created_ms": 11, "from_me": False}]
        self.ours, self.theirs = self.bridge.messages("openchat", self.chat, 10)["messages"]

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def saved(self, ref):
        return next(m for m in self.bridge.archive_messages({})["messages"] if m["message_ref"] == ref)

    def test_own_openchat_message_is_unsent_and_marked(self):
        result = self.bridge.call("local", "unsend", {"message_ref": self.ours["message_ref"]})
        self.assertEqual(result, {"message_ref": self.ours["message_ref"], "state": "unsent"})
        self.assertEqual(self.openchat.removals, [("unsend", {"chat_id": "open-room", "message_id": "401"})])
        self.assertTrue(self.saved(self.ours["message_ref"])["unsent"])

    def test_someone_elses_message_cannot_be_unsent(self):
        with self.assertRaises(BridgeError) as error:
            self.bridge.call("local", "unsend", {"message_ref": self.theirs["message_ref"]})
        self.assertEqual(error.exception.code, "not_own_message")
        self.assertEqual(self.openchat.removals, [])

    def test_thread_reply_is_unsent_inside_its_thread(self):
        self.openchat.rows = [{"id": "411", "text": "reply", "created_ms": 20, "from_me": True}]
        reply = self.bridge.openchat_thread(self.theirs["message_ref"], 20)["messages"][0]
        self.bridge.sending.remove(reply["message_ref"], "unsend")
        self.assertEqual(self.openchat.removals[-1], ("unsend", {"chat_id": "open-room", "message_id": "411", "thread_message_id": "402"}))

    def test_openchat_delete_reports_refusal_without_marking(self):
        self.openchat.remove_result = {"state": "not_deleted", "reason": "upstream_failed"}
        result = self.bridge.call("local", "openchat_delete_message", {"message_ref": self.theirs["message_ref"]})
        self.assertEqual(result["state"], "not_deleted")
        self.assertEqual(self.openchat.removals, [("destroy", {"chat_id": "open-room", "message_id": "402"})])
        self.assertFalse(self.saved(self.theirs["message_ref"])["unsent"])

    def test_personal_unsend_uses_the_shared_sequence(self):
        personal = self.providers["personal"]
        ref = self.store.reference("personal", "u" + "b" * 32, "Fixture direct chat", personal.generation)
        personal.send_result = {"state": "sent_confirmed", "message_id": "501", "created_ms": 30}
        self.bridge.sending.send(ref, "to take back", str(uuid.uuid4()))
        message = self.bridge.archive_messages({"query": "to take back"})["messages"][0]
        self.assertEqual(self.bridge.sending.remove(message["message_ref"], "unsend")["state"], "unsent")
        call = personal.removals[0]
        self.assertEqual((call[0], call[1]["chat_id"], call[1]["message_id"]), ("unsend", "u" + "b" * 32, "501"))
        self.assertEqual(call[1]["sequence"], personal.send_calls[0]["sequence"] + 1)
        with self.assertRaises(BridgeError) as error:
            self.bridge.sending.remove(message["message_ref"], "destroy")
        self.assertEqual(error.exception.code, "delete_is_openchat_only")

    def test_unsaved_message_and_disabled_sending_are_refused(self):
        with self.assertRaises(BridgeError) as error:
            self.bridge.sending.remove("message:v1:unknown", "unsend")
        self.assertEqual(error.exception.code, "message_not_retrieved")
        self.config.sending_enabled = False
        self.assertNotIn("unsend", available(self.config, "local"))
        self.assertNotIn("openchat_delete_message", available(self.config, "local"))
        with self.assertRaises(BridgeError):
            self.bridge.sending.remove(self.ours["message_ref"], "unsend")

    def test_unsent_rows_from_line_are_marked(self):
        self.openchat.rows = [{"id": "403", "text": "", "created_ms": 12, "content_metadata": {"UNSENT": "true"}}]
        self.assertTrue(self.bridge.messages("openchat", self.chat, 10)["messages"][0]["unsent"])


if __name__ == "__main__":
    unittest.main()

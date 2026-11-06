import unittest

from line_bridge.errors import BridgeError
from line_bridge.storage import Store
from support import environment


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp, self.root, self.config, self.protector, self.store, self.providers, self.bridge = environment()
        self.personal = self.bridge.personal_chats()["chats"][0]["chat_ref"]
        self.openchat = self.bridge.openchat_listing()["chats"][0]["chat_ref"]

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def save(self, source, ref, count, text="fixture", first=1):
        messages = [self.bridge.normalize_message(source, ref, "Fixture", {
            "id": str(i), "text": text, "created_ms": i
        }, self.providers[source].generation) for i in range(first, first + count)]
        self.store.archive(messages)
        return messages

    def test_ordinary_reads_archive_both_sources_without_notifications(self):
        self.bridge.messages("personal", self.personal, 10)
        self.bridge.messages("openchat", self.openchat, 10)
        result = self.bridge.call("local", "archive_messages", {})
        self.assertEqual({m["source"] for m in result["messages"]}, {"personal", "openchat"})
        self.assertEqual(result["provenance"], "local")
        self.assertFalse(self.config.monitor.enabled)

    def test_archive_is_encrypted_deduplicated_and_permanent(self):
        self.save("personal", self.personal, 1, "private fixture body")
        self.save("personal", self.personal, 1, "private fixture body")
        with self.store.db() as db:
            db.execute("UPDATE cache SET stored=0")
        reopened = Store(self.root, self.config.profile_id, self.protector)
        self.assertEqual(reopened.counts()["archived_messages"], 1)
        self.assertNotIn(b"private fixture body", reopened.path.read_bytes())
        self.assertEqual(self.bridge.archive_messages({})["messages"][0]["plain_text"], "private fixture body")

    def test_exact_notification_reference_can_be_recovered_without_line(self):
        message = self.save("openchat", self.openchat, 1)[0]
        self.providers["openchat"].fail = True
        result = self.bridge.archive_messages({"chat_ref": self.openchat, "message_ref": message["message_ref"]})
        self.assertEqual(result["messages"], [message])
        result = self.bridge.archive_messages({"chat_ref": self.personal, "message_ref": message["message_ref"]})
        self.assertEqual(result["messages"], [])

    def test_pagination_with_identical_timestamps_has_no_duplicates(self):
        messages = self.save("personal", self.personal, 5)
        for message in messages:
            message["created_ms"] = 100
        self.store.archive(messages)
        args = {"chat_ref": self.personal, "limit": 2}
        refs = []
        while True:
            result = self.bridge.archive_messages(args)
            refs.extend(m["message_ref"] for m in result["messages"])
            if not result["truncated"]:
                break
            args["before_message_ref"] = result["next_before_message_ref"]
        self.assertEqual(len(refs), 5)
        self.assertEqual(len(set(refs)), 5)

    def test_search_filters_chat_before_bounded_decryption(self):
        self.save("personal", self.personal, 1, "needle")
        self.save("openchat", self.openchat, 501, first=2)
        result = self.bridge.archive_messages({"chat_ref": self.personal, "query": "NEEDLE"})
        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["scanned"], 1)

    def test_bounded_search_returns_cursor_even_when_no_matches(self):
        self.save("personal", self.personal, 1, "needle")
        self.save("personal", self.personal, 501, first=2)
        result = self.bridge.archive_messages({"query": "needle"})
        self.assertEqual(result["messages"], [])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["scanned"], 500)
        result = self.bridge.archive_messages({"query": "needle", "before_message_ref": result["next_before_message_ref"]})
        self.assertEqual(len(result["messages"]), 1)

    def test_timestamp_filter_and_foreign_cursor(self):
        self.save("personal", self.personal, 5)
        result = self.bridge.archive_messages({"after_ms": 2, "before_ms": 3})
        self.assertEqual([m["created_ms"] for m in result["messages"]], [2, 3])
        with self.assertRaisesRegex(BridgeError, "archive_cursor_not_available"):
            self.bridge.archive_messages({"before_message_ref": "foreign"})

    def test_upstream_history_uses_persisted_trusted_timestamp(self):
        from unittest.mock import patch
        message = self.save("personal", self.personal, 1, first=1234)[0]
        with patch.object(self.providers["personal"], "read", return_value=[]) as read:
            self.bridge.messages("personal", self.personal, 10, message["message_ref"])
        self.assertEqual(read.call_args.args[1]["before_created_ms"], 1234)

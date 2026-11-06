import time
import unittest

from line_bridge.errors import BridgeError
from line_bridge.monitor import Monitor
from support import environment


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp, self.root, self.config, self.protector, self.store, self.providers, self.bridge = environment()
        self.ref = self.bridge.personal_chats()["chats"][0]["chat_ref"]
        self.config.monitor.enabled = True
        self.config.monitor.targets = [self.ref]
        self.monitor = Monitor(self.bridge)

    def tearDown(self):
        self.temp.cleanup()

    def archived(self):
        return self.store.counts()["archived_messages"]

    def test_first_cycle_archives_history(self):
        result = self.monitor.cycle()
        self.assertEqual(self.archived(), 1)
        self.assertEqual(result["baselining_chats"], 1)

    def test_new_message_archived_once(self):
        self.monitor.cycle()
        self.providers["personal"].rows = [{"id": "new", "text": "new fixture", "created_ms": int(time.time() * 1000) + 1, "from_me": False}]
        self.assertEqual(self.monitor.cycle()["archived_this_cycle"], 1)
        self.assertEqual(self.monitor.cycle()["archived_this_cycle"], 0)
        self.assertEqual(self.archived(), 2)

    def test_failure_does_not_advance_checkpoint(self):
        self.monitor.cycle()
        with self.store.db() as db:
            before = [(r[0], bytes(r[1])) for r in db.execute("SELECT * FROM meta WHERE key LIKE 'checkpoint:%'")]
        self.providers["personal"].fail = True
        result = self.monitor.cycle()
        with self.store.db() as db:
            after = [(r[0], bytes(r[1])) for r in db.execute("SELECT * FROM meta WHERE key LIKE 'checkpoint:%'")]
        self.assertEqual(before, after)
        self.assertEqual(result["failed_chats"], 1)
        self.assertEqual(result["state"], "partial_failure")

    def test_no_selected_target_no_collection(self):
        self.config.monitor.targets = []
        self.providers["personal"].calls.clear()
        self.monitor.cycle()
        self.assertEqual(self.providers["personal"].calls, [])

    def test_disabled_cycle_refused(self):
        self.config.monitor.enabled = False
        with self.assertRaises(BridgeError):
            self.monitor.cycle()

    def test_reauthentication_stale_reference_fails_closed(self):
        self.monitor.cycle()
        self.providers["personal"].generation = "new-login"
        self.assertEqual(self.monitor.cycle()["failed_chats"], 1)

    def test_openchat_target_is_archived(self):
        ref = self.bridge.openchat_listing()["chats"][0]["chat_ref"]
        self.config.monitor.targets = [ref]
        self.monitor.cycle()
        self.assertEqual(self.archived(), 1)

    def test_existing_personal_checkpoint_seeds_archive(self):
        import hashlib
        key = "checkpoint:" + hashlib.sha256(self.ref.encode()).hexdigest()
        self.store.put_meta(key, {"generation": self.providers["personal"].generation,
                                  "ids": [str(m["id"]) for m in self.providers["personal"].rows]})
        self.monitor.cycle()
        self.assertEqual(self.archived(), 1)
        self.assertTrue(self.store.get_meta(key)["archive_seeded"])
        self.assertEqual(self.monitor.cycle()["archived_this_cycle"], 0)

    def test_restart_keeps_archive_without_duplicates(self):
        self.monitor.cycle()
        Monitor(self.bridge).cycle()
        self.assertEqual(self.archived(), 1)
        self.assertEqual(self.store.get_meta("worker_status")["state"], "running")

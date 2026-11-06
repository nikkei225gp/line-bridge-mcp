import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from line_bridge.config import Config
from line_bridge.errors import BridgeError
from line_bridge.locking import ProcessLock
from line_bridge.security import profile_protector, create_private_directory, save_secret, verify_acl
from line_bridge.storage import Store
from line_bridge.transport import forward, serve
from line_bridge.bridge import Bridge
from support import FakeProvider


@unittest.skipUnless(os.name == "nt" or sys.platform.startswith("linux"), "Windows/Linux")
class PlatformTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="linebridge-win-")
        self.root = create_private_directory(Path(self.temp.name) / "profile")
        self.config = Config(personal_mode="native", openchat_mode="native")
        self.protector = profile_protector(self.root, self.config.profile_id, create=True)
        self.providers = {s: FakeProvider(s) for s in ("personal", "openchat")}
        self.bridge = Bridge(self.config, Store(self.root, self.config.profile_id, self.protector), self.providers)
        for role in ("local", "admin"):
            save_secret(self.root / (role + "-key.dpapi"), secrets.token_urlsafe(48), self.protector)
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=serve, args=(self.root, self.protector, self.bridge, self.stop, lambda _: self.ready.set()))
        self.thread.start()
        self.assertTrue(self.ready.wait(10))

    def tearDown(self):
        self.stop.set()
        self.thread.join(timeout=10)
        self.temp.cleanup()

    def rpc(self, role, name, arguments=None):
        return forward(self.root, self.protector, role, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}})

    def test_private_acl_and_dpapi_binding(self):
        verify_acl(self.root)
        cipher = self.protector.seal(b"fixture secret")
        self.assertEqual(self.protector.open(cipher), b"fixture secret")
        with self.assertRaises(BridgeError):
            profile_protector(self.root, Config().profile_id).open(cipher)

    def test_inherited_unsafe_directory_refused(self):
        unsafe = Path(self.temp.name) / "unsafe"
        unsafe.mkdir()
        if sys.platform.startswith("linux"):
            unsafe.chmod(0o755)
        with self.assertRaises(BridgeError):
            create_private_directory(unsafe)

    def test_shared_daemon_multiple_callers(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.rpc("local", "personal_chats"))) for _ in range(6)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(results), 6)
        refs = {r["result"]["structuredContent"]["chats"][0]["chat_ref"] for r in results}
        self.assertEqual(len(refs), 1)
        self.assertFalse(self.config.monitor.enabled)

    def test_removed_roles_are_refused(self):
        for role in ("notify", "read"):
            with self.assertRaisesRegex(BridgeError, "invalid_connection_role"):
                self.rpc(role, "personal_chats")
        self.assertEqual(self.providers["personal"].calls, [])

    def test_unauthenticated_loopback_refused(self):
        url = json.loads((self.root / "endpoint.json").read_text())["url"]
        req = urllib.request.Request(url + "/rpc/local", data=b"{}", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req)
        self.assertEqual(error.exception.code, 401)

    def test_origin_refused(self):
        url = json.loads((self.root / "endpoint.json").read_text())["url"]
        req = urllib.request.Request(url + "/rpc/local", data=b"{}", headers={"Origin": "https://example.com", "Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req)
        self.assertEqual(error.exception.code, 403)

    def test_admin_stop_scope_and_cleanup(self):
        refused = forward(self.root, self.protector, "local", {"operation": "stop"})
        self.assertIn("error", refused)
        self.assertFalse(self.stop.is_set())
        result = forward(self.root, self.protector, "admin", {"operation": "stop"})
        self.assertEqual(result["state"], "stopping")
        self.thread.join(10)
        self.assertFalse((self.root / "endpoint.json").exists())

    def test_exclusive_profile_lock(self):
        with ProcessLock(self.root / "test.lock"):
            with self.assertRaises(BridgeError):
                with ProcessLock(self.root / "test.lock"):
                    pass

    def test_bad_proxy_endpoint_rejected(self):
        (self.root / "endpoint.json").write_text(json.dumps({"url": "https://example.com"}))
        with self.assertRaisesRegex(BridgeError, "daemon_not_running"):
            self.rpc("local", "bridge_status")

"""Synthetic agent login: QR/PIN output, async completion, roles and legacy reuse."""
import base64
from copy import deepcopy
import io
import json
import threading
import unittest
from unittest.mock import patch

from line_bridge.authentication import desktop_qr, validate_unified_session
from line_bridge.catalog import available
from line_bridge.cli import parser, run
from line_bridge.config import Config
from line_bridge.devices import DEVICES, normalize_device
from line_bridge.errors import BridgeError
from line_bridge.rpc import Rpc
from support import environment
from test_authentication import session


class AgentLoginTests(unittest.TestCase):
    def setUp(self):
        self.temp, self.root, self.config, self.protector, self.store, self.providers, self.bridge = environment()
        self.config.save(self.root)

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def test_devices_and_legacy_session_validation(self):
        validate_unified_session(session())
        for device, version in DEVICES.items():
            value = session()
            value.update(schema="secondary-shared-v2", device=device, version=version)
            validate_unified_session(value)
        for device in ("ANDROID", "IOS", "CHROMEOS"):
            with self.assertRaises(BridgeError):
                normalize_device(device)
        self.assertEqual(normalize_device("android"), "ANDROIDSECONDARY")

    def test_existing_config_keeps_disabled_send_and_pc_device(self):
        self.config.unified_generation = session()["bridge_generation"]
        self.config.save(self.root)
        path = self.root / "config.json"
        raw = json.loads(path.read_text())
        raw.pop("login_device")
        path.write_text(json.dumps(raw))
        loaded = Config.load(self.root)
        self.assertFalse(loaded.sending_enabled)
        self.assertEqual(loaded.login_device, "DESKTOPWIN")

    def test_async_mcp_returns_image_pin_then_commits_without_exporting_saved_keys(self):
        release = threading.Event()
        pin_ready = threading.Event()
        saved = session()
        saved.update(schema="secondary-shared-v2", device="ANDROIDSECONDARY", version=DEVICES["ANDROIDSECONDARY"])
        def worker(config, script, device, channel, cancel):
            self.assertEqual(device, "ANDROIDSECONDARY")
            channel.put(("qr", "https://example.invalid/synthetic-qr"))
            channel.put(("pin", "123456"))
            pin_ready.set()
            release.wait(5)
            return deepcopy(saved)
        try:
            with patch("line_bridge.login.run_qr_worker", side_effect=worker):
                start = self.bridge.call("local", "login_start", {})
                self.assertTrue(pin_ready.wait(2))
                response = Rpc(self.bridge).dispatch("local", "tools/call", {"name": "login_status", "arguments": {"login_id": start["login_id"]}})
                payload = response["structuredContent"]
                self.assertEqual(payload["pin"], "123456")
                self.assertTrue(base64.b64decode(payload["qr_base64"]).startswith(b"\x89PNG\r\n\x1a\n"))
                self.assertEqual(response["content"][1]["type"], "image")
                self.assertNotIn(saved["accessToken"], json.dumps(payload))
                self.assertNotIn("e2eeStorage", payload)
                release.set()
                self.bridge.login.thread.join(5)
                result = self.bridge.call("local", "login_status", {"login_id": start["login_id"]})
                self.assertEqual(result["state"], "authenticated")
                self.assertEqual(self.config.login_device, "ANDROIDSECONDARY")
                self.assertNotIn("qr_base64", result)
                self.assertNotIn("pin", result)
                self.assertNotIn("accessToken", result)
                self.assertEqual(self.bridge.login.cancel(start["login_id"])["state"], "authenticated")
        finally:
            release.set()

    def test_login_does_not_depend_on_sending(self):
        self.config.sending_enabled = False
        self.assertIn("login_start", available(self.config, "local"))
        for role in ("read", "notify"):
            with self.assertRaises(BridgeError):
                available(self.config, role)
        with self.assertRaises(BridgeError):
            self.bridge.call("local", "login_start", {"replace": "true"})

    def test_cancel_removes_temporary_qr_and_never_commits(self):
        def worker(config, script, device, channel, cancel):
            channel.put(("qr", "https://example.invalid/synthetic-qr"))
            cancel.wait(5)
            return None
        with patch("line_bridge.login.run_qr_worker", side_effect=worker):
            start = self.bridge.call("local", "login_start", {})
            self.bridge.call("local", "login_cancel", {"login_id": start["login_id"]})
            self.bridge.login.thread.join(2)
            result = self.bridge.call("local", "login_status", {"login_id": start["login_id"]})
            self.assertEqual(result["state"], "cancelled")
            self.assertNotIn("qr_base64", result)
            self.assertEqual(self.config.unified_generation, "")

    def test_json_cli_streams_temporary_qr_and_pin_only(self):
        def worker(config, script, device, output, cancel):
            output.put(("qr", "https://example.invalid/synthetic-qr"))
            output.put(("pin", "123456"))
            return session()
        captured = io.StringIO()
        with patch("line_bridge.authentication.run_qr_worker", side_effect=worker), patch("sys.stdout", captured):
            desktop_qr(self.config, "login-unified.ts", "android", "json")
        frames = [json.loads(row) for row in captured.getvalue().splitlines()]
        self.assertEqual([f["event"] for f in frames], ["qr", "pin"])
        self.assertEqual(frames[1]["pin"], "123456")
        self.assertTrue(frames[0]["qr_data_url"].startswith("data:image/png;base64,"))
        self.assertNotIn("accessToken", captured.getvalue())

    def test_agent_stdio_auto_starts_saved_daemon_without_login(self):
        args = parser().parse_args(["--data-dir", str(self.root), "stdio", "--auto-start"])
        with patch("line_bridge.cli.load", return_value=(self.config, self.protector)), \
             patch("line_bridge.cli.start_daemon") as start, patch("line_bridge.cli.stdio") as stdio:
            run(args)
        start.assert_called_once()
        stdio.assert_called_once()

    def test_sending_enable_has_no_acknowledgement_gate(self):
        args = parser().parse_args(["--data-dir", str(self.root), "sending", "enable"])
        with patch("line_bridge.cli.load", return_value=(self.config, self.protector)):
            self.assertTrue(run(args)["sending_enabled"])

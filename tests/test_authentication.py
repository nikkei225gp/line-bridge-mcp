import base64
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
import uuid

from line_bridge.authentication import unified_login, unified_session_path, validate_unified_session
from line_bridge.cli import parser, run
from line_bridge.config import Config
from line_bridge.errors import BridgeError
from line_bridge.providers import NativeDesktopPersonal, make_providers
from line_bridge.security import load_secret, save_secret
from support import environment


def session():
    key = {"keyId": 1, "privKey": base64.b64encode(bytes(32)).decode(),
           "pubKey": base64.b64encode(bytes(32)).decode(), "e2eeVersion": 1}
    return {"schema": "desktop-shared-v1", "device": "DESKTOPWIN", "version": "26.5.0.3975",
            "accessToken": "mock-" + "x" * 30, "mid": "u" + "a" * 32,
            "e2eeStorage": {"e2eeKeys:1": json.dumps(key)}, "bridge_generation": str(uuid.uuid4())}


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp, self.root, self.config, self.protector, self.store, self.providers, self.bridge = environment()
        self.config.save(self.root)

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def test_login_defaults_to_one_shared_qr(self):
        self.assertEqual(parser().parse_args(["login"]).provider, "unified")
        self.assertEqual(parser().parse_args(["login", "unified"]).provider, "unified")

    def test_single_login_enables_both_and_resets_monitor(self):
        value = session()
        self.config.monitor.enabled = True
        self.config.sending_enabled = True
        with patch("line_bridge.authentication.desktop_qr", return_value=value) as qr:
            result = unified_login(self.root, self.config, self.protector)
        qr.assert_called_once_with(self.config, "login-unified.ts", "ANDROIDSECONDARY", "gui")
        self.assertEqual(result["qr_scans"], 1)
        self.assertEqual((self.config.personal_mode, self.config.openchat_mode), ("native", "native"))
        self.assertEqual(Config.load(self.root).unified_generation, value["bridge_generation"])
        self.assertFalse(self.config.monitor.enabled)
        self.assertTrue(self.config.sending_enabled)
        saved = unified_session_path(self.root, self.config.unified_generation)
        self.assertEqual(load_secret(saved, self.protector), value)
        self.assertNotIn(value["accessToken"].encode(), saved.read_bytes())
        self.assertNotIn("accessToken", json.dumps(result))

    def test_old_credentials_remain_unchanged(self):
        files = [self.root / (p + "-session.dpapi") for p in ("personal", "openchat")]
        for file in files:
            save_secret(file, {"old": "mock credential"}, self.protector)
        before = [f.read_bytes() for f in files]
        with patch("line_bridge.authentication.desktop_qr", return_value=session()):
            unified_login(self.root, self.config, self.protector)
        self.assertEqual([f.read_bytes() for f in files], before)

    def test_cancel_preserves_active_config_and_creds(self):
        before = (self.root / "config.json").read_bytes()
        with patch("line_bridge.authentication.desktop_qr", side_effect=BridgeError("qr_login_cancelled_or_failed")):
            with self.assertRaises(BridgeError):
                unified_login(self.root, self.config, self.protector)
        self.assertEqual((self.root / "config.json").read_bytes(), before)
        self.assertEqual(list(self.root.glob("desktop-*.dpapi")), [])

    def test_bad_keys_never_activate_or_save(self):
        value = session()
        value["e2eeStorage"]["refreshToken"] = "not-allowed"
        with patch("line_bridge.authentication.desktop_qr", return_value=value):
            with self.assertRaises(BridgeError):
                unified_login(self.root, self.config, self.protector)
        self.assertFalse(self.config.unified_generation)
        self.assertEqual(list(self.root.glob("desktop-*.dpapi")), [])

    def test_failed_config_commit_does_not_replace_old_generation(self):
        old, new = session(), session()
        self.config.unified_generation = old["bridge_generation"]
        save_secret(unified_session_path(self.root, self.config.unified_generation), old, self.protector)
        self.config.save(self.root)
        before = (self.root / "config.json").read_bytes()
        with patch("line_bridge.authentication.desktop_qr", return_value=new), patch.object(Config, "save", side_effect=OSError("mock disk failure")):
            with self.assertRaises(OSError):
                unified_login(self.root, self.config, self.protector)
        self.assertEqual((self.root / "config.json").read_bytes(), before)
        self.assertEqual(self.config.unified_generation, old["bridge_generation"])
        self.assertEqual(load_secret(unified_session_path(self.root, self.config.unified_generation), self.protector), old)

    def test_reauthentication_uses_new_file_and_invalidates_both_refs(self):
        first, second = session(), session()
        with patch("line_bridge.authentication.desktop_qr", return_value=first):
            unified_login(self.root, self.config, self.protector)
        ref = self.store.reference("personal", "mock-chat", "Mock", first["bridge_generation"])
        square = self.store.reference("openchat", "mock-square", "Mock", first["bridge_generation"])
        with patch("line_bridge.authentication.desktop_qr", return_value=second):
            unified_login(self.root, self.config, self.protector)
        for source, old_ref in (("personal", ref), ("openchat", square)):
            with self.assertRaises(BridgeError):
                self.store.resolve(old_ref, source, second["bridge_generation"])
        self.assertEqual(load_secret(unified_session_path(self.root, first["bridge_generation"]), self.protector), first)

    def test_both_providers_share_one_lazy_process(self):
        value = session()
        self.config.unified_generation = value["bridge_generation"]
        save_secret(unified_session_path(self.root, self.config.unified_generation), value, self.protector)
        providers = make_providers(self.root, self.config, self.protector)
        self.assertIs(providers["personal"].desktop, providers["openchat"])
        self.assertIsNone(providers["openchat"].process)
        self.assertEqual(providers["personal"].generation, providers["openchat"].generation)
        self.assertIsNone(providers["openchat"].process)
        with patch.object(providers["openchat"], "_call", return_value={"result": []}) as call:
            self.assertEqual(providers["personal"].read("messages", {"chat_id": "mock", "count": 1}), [])
        call.assert_called_once_with("personal", {"operation": "messages", "arguments": {"chat_id": "mock", "count": 1}})

    def test_personal_rejects_mutations_before_process(self):
        from unittest.mock import Mock
        desktop = Mock()
        personal = NativeDesktopPersonal(desktop)
        for operation in ("send", "mark_read", "login", "join", "registerE2EEKeyPair"):
            with self.assertRaises(BridgeError):
                personal.read(operation, {})
        desktop._call.assert_not_called()

    def test_session_file_cannot_escape_profile(self):
        for generation in ("../outside", "C:/outside", "", None, True):
            with self.assertRaises(BridgeError):
                unified_session_path(self.root, generation)

    def test_strict_session_validation(self):
        original = session()
        changes = [{"refreshToken": "forbidden"}, {"device": "CHROMEOS"}, {"mid": "invalid"},
                   {"e2eeStorage": {}}, {"bridge_generation": "invalid"}, {"accessToken": "short"}]
        for change in changes:
            value = deepcopy(original)
            value.update(change)
            with self.assertRaises(BridgeError):
                validate_unified_session(value)

    def test_cli_replace_required_before_launching_qr(self):
        self.config.unified_generation = str(uuid.uuid4())
        args = parser().parse_args(["--data-dir", str(self.root), "login"])
        with patch("line_bridge.cli.load", return_value=(self.config, self.protector)), patch("line_bridge.authentication.unified_login") as login:
            with self.assertRaisesRegex(BridgeError, "existing_session_requires_explicit_replace"):
                run(args)
            login.assert_not_called()
            args.replace = True
            run(args)
            login.assert_called_once()

    def test_only_unified_login_is_available(self):
        import argparse
        commands = next(a for a in parser()._actions if isinstance(a, argparse._SubParsersAction)).choices
        self.assertNotIn("legacy-readonly", commands)
        provider = next(a for a in commands["login"]._actions if a.dest == "provider")
        self.assertEqual(provider.choices, ("unified",))

    def test_unified_requires_both_native_providers(self):
        self.config.unified_generation = str(uuid.uuid4())
        self.config.openchat_mode = "disabled"
        with self.assertRaises(BridgeError):
            self.config.validate()

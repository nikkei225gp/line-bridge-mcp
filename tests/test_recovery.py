"""Synthetic credentials/stdio only. Never stop the real broker or contact LINE."""
from io import BytesIO
import json
import queue
import unittest
from unittest.mock import patch

from line_bridge.authentication import unified_session_path
from line_bridge.bridge import Bridge
from line_bridge.errors import BridgeError
from line_bridge.providers import make_providers
from line_bridge.rpc import Rpc
from line_bridge.security import save_secret
from support import environment
from test_authentication import session


class FakeInput(BytesIO):
    def __init__(self, process):
        super().__init__()
        self.process = process

    def write(self, body):
        self.process.requests.append(json.loads(body))
        return super().write(body)

    def flush(self):
        value = self.process.reply
        if isinstance(value, BaseException):
            raise value
        if value is None:
            self.process.output.put(None)
        elif isinstance(value, bytes):
            self.process.output.put(value)
        else:
            result = {"id": self.process.requests[-1]["id"], **value}
            self.process.output.put(json.dumps(result).encode() + b"\n")


class FakeProcess:
    def __init__(self, reply):
        self.reply, self.requests = reply, []
        self.output = queue.Queue()
        self.stdin, self.stdout = FakeInput(self), BytesIO()
        self.alive, self.kill_calls, self.wait_calls = True, 0, 0

    def poll(self):
        return None if self.alive else 0

    def kill(self):
        self.kill_calls += 1
        self.alive = False

    def wait(self, timeout):
        self.wait_calls += 1
        return 0


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp, self.root, self.config, self.protector, self.store, _, self.old_bridge = environment()
        self.saved = session()
        self.config.unified_generation = self.saved["bridge_generation"]
        self.config.save(self.root)
        self.file = unified_session_path(self.root, self.config.unified_generation)
        save_secret(self.file, self.saved, self.protector)
        self.before_config = (self.root / "config.json").read_bytes()
        self.before_credentials = self.file.read_bytes()
        self.providers = make_providers(self.root, self.config, self.protector)
        self.desktop = self.providers["openchat"]
        self.bridge = Bridge(self.config, self.store, self.providers)

    def tearDown(self):
        self.bridge.close()
        self.old_bridge.close()
        self.assertEqual((self.root / "config.json").read_bytes(), self.before_config)
        self.assertEqual(self.file.read_bytes(), self.before_credentials)
        self.assertFalse(self.config.sending_enabled)
        self.assertFalse(self.config.monitor.enabled)
        self.temp.cleanup()

    def launch(self, replies):
        pending = iter(replies)
        processes = []

        def start():
            if self.desktop.process is not None and self.desktop.process.poll() is None:
                return
            process = FakeProcess(next(pending))
            self.desktop.process, self.desktop.output = process, process.output
            processes.append(process)

        return processes, patch.object(self.desktop, "_start", side_effect=start)

    def test_missing_unified_credentials_does_not_start_runtime_or_login(self):
        self.config.unified_generation = session()["bridge_generation"]
        with patch.object(self.desktop, "_start") as start:
            with self.assertRaisesRegex(BridgeError, "^unified_auth_required$"):
                self.desktop.read("list", {})
            start.assert_not_called()

    def test_auth_failure_reaches_rpc_for_both_services_without_secret_details(self):
        processes, launch = self.launch([{"state": "failed", "error_code": "auth_required",
            "message": "private upstream detail"}] * 2)
        with launch:
            for tool in ("personal_chats", "openchat_chats"):
                reply = Rpc(self.bridge).dispatch("local", "tools/call", {"name": tool})
                self.assertTrue(reply["isError"])
                self.assertEqual(reply["content"], [{"type": "text", "text": "unified_auth_required"}])
        self.assertEqual([len(p.requests) for p in processes], [1, 1])

    def test_transient_errors_are_distinct_and_only_static_codes_escape(self):
        codes = {"network_unavailable": "upstream_network_unavailable", "request_timeout": "upstream_request_timeout",
                 "upstream_unavailable": "upstream_temporarily_unavailable", "rate_limited": "upstream_rate_limited"}
        processes, launch = self.launch([{"state": "failed", "error_code": code} for code in codes])
        with launch:
            for code in codes.values():
                with self.assertRaisesRegex(BridgeError, "^" + code + "$"):
                    self.desktop.read("list", {})
        self.assertEqual([len(p.requests) for p in processes], [1] * 4)

    def test_network_recovery_uses_next_explicit_read_without_reauthentication(self):
        processes, launch = self.launch([{"state": "failed", "error_code": "network_unavailable"},
                                        {"squares": [], "chats": []}])
        with launch:
            with self.assertRaises(BridgeError):
                self.desktop.read("list", {})
            self.assertIsNone(self.desktop.process)
            self.assertEqual(len(processes), 1)  # No automatic retry.
            self.assertEqual(self.desktop.read("list", {})["chats"], [])
        self.assertEqual([len(p.requests) for p in processes], [1, 1])
        self.assertTrue(processes[0].stdin.closed and processes[0].stdout.closed)
        self.assertEqual(processes[1].requests[0]["session"], self.saved)

    def test_timeout_drops_retired_queue_and_ignores_late_output(self):
        processes, launch = self.launch([{}, {"squares": [], "chats": []}])
        with launch:
            self.desktop._start()
            retired = self.desktop.output
            with patch.object(retired, "get", side_effect=queue.Empty):
                with self.assertRaisesRegex(BridgeError, "^upstream_request_timeout$"):
                    self.desktop.read("list", {})
            self.assertIsNone(self.desktop.output)
            retired.put(b'{"id":"late-retired-request","chats":["private"]}\n')
            self.assertEqual(self.desktop.read("list", {})["chats"], [])
        self.assertEqual([len(p.requests) for p in processes], [1, 1])

    def test_runtime_exit_is_not_reported_as_auth_expiry(self):
        processes, launch = self.launch([None])
        with launch:
            with self.assertRaisesRegex(BridgeError, "^unified_bridge_unavailable$"):
                self.desktop.read("list", {})
        self.assertEqual(processes[0].kill_calls, 1)

    def test_mismatched_response_identity_is_rejected(self):
        _, launch = self.launch([{"id": "wrong-request", "chats": []}])
        with launch:
            with self.assertRaisesRegex(BridgeError, "^invalid_bridge_response$"):
                self.desktop.read("list", {})
        self.assertIsNone(self.desktop.process)

    def test_malformed_and_nonobject_responses_do_not_expose_raw_output(self):
        for body, code in ((b"private raw error", "unified_bridge_unavailable"),
                           (b"[]", "invalid_bridge_response")):
            with self.subTest(code=code):
                _, launch = self.launch([body])
                with launch:
                    with self.assertRaisesRegex(BridgeError, "^" + code + "$"):
                        self.desktop.read("list", {})

    def test_unknown_error_code_is_never_forwarded(self):
        _, launch = self.launch([{"state": "failed", "error_code": "private upstream string"}])
        with launch:
            with self.assertRaisesRegex(BridgeError, "^unified_read_failed$"):
                self.desktop.read("list", {})

    def test_pipe_failure_is_sanitized_and_does_not_retry(self):
        processes, launch = self.launch([BrokenPipeError("private upstream string")])
        with launch:
            with self.assertRaisesRegex(BridgeError, "^unified_bridge_unavailable$"):
                self.desktop.read("list", {})
        self.assertEqual(len(processes), 1)
        self.assertEqual(len(processes[0].requests), 1)

    def test_uncertain_send_stays_unknown_and_is_never_retried(self):
        replies = [{"state": "failed", "error_code": "auth_required"},
                   {"state": "failed", "error_code": "network_unavailable"},
                   BrokenPipeError("private error"), None]
        processes, launch = self.launch(replies)
        with launch:
            for _ in replies:
                self.assertEqual(self.desktop.send_once({}), {"state": "send_outcome_unknown_no_retry"})
        self.assertEqual([len(p.requests) for p in processes], [1] * 4)

    def test_successful_reads_share_one_runtime_and_close_is_idempotent(self):
        processes, launch = self.launch([{"result": []}])
        with launch:
            self.assertEqual(self.providers["personal"].read("chats", {"limit": 1}), [])
            processes[0].reply = {"squares": [], "chats": []}
            self.desktop.read("list", {})
        self.assertEqual(len(processes), 1)
        self.bridge.close()
        self.bridge.close()
        self.assertEqual(processes[0].kill_calls, 1)
        self.assertIsNone(self.desktop.process)
        self.assertIsNone(self.desktop.output)

    def test_dead_runtime_pipes_are_closed_before_starting_replacement(self):
        retired, replacement = FakeProcess({}), FakeProcess({})
        retired.alive = False
        self.desktop.process, self.desktop.output = retired, retired.output
        with patch("line_bridge.providers.subprocess.Popen", return_value=replacement) as spawn, \
             patch("line_bridge.providers.threading.Thread"):
            self.desktop._start()
        spawn.assert_called_once()
        self.assertTrue(retired.stdin.closed and retired.stdout.closed)
        self.assertIs(self.desktop.process, replacement)
        self.assertIsNot(self.desktop.output, retired.output)

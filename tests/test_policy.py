import json
import tempfile
import unittest
from pathlib import Path

from line_bridge.cli import parser
from line_bridge.config import Config
from line_bridge.errors import BridgeError


class MonitorPolicyTests(unittest.TestCase):
    def test_interval_seconds(self):
        args = parser().parse_args(["monitor", "enable", "--all", "--interval-seconds", "30"])
        self.assertEqual(args.interval_seconds, 30)
        self.assertEqual(Config().monitor.interval_seconds, 120)

    def test_existing_stdio_registrations_still_parse(self):
        self.assertTrue(parser().parse_args(["stdio", "--auto-start", "--role", "local"]).auto_start)
        self.assertTrue(parser().parse_args(["stdio", "--auto-start"]).auto_start)
        for role in ("notify", "read"):
            with self.assertRaises(SystemExit):
                parser().parse_args(["stdio", "--role", role])

    def test_interval_boundaries(self):
        config = Config()
        for interval in (1, 30, 120, 3600, 86400):
            config.monitor.interval_seconds = interval
            config.validate()
        for interval in (0, -1, 86401, True, 1.5):
            config.monitor.interval_seconds = interval
            with self.assertRaisesRegex(BridgeError, "invalid_monitor_limits"):
                config.validate()

    def test_removed_settings_in_old_config_are_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = json.loads(json.dumps(Config().__dict__, default=lambda o: o.__dict__))
            del raw["sending_enabled"]
            raw.update(tunnel_enabled=True, tunnel_role="notify", tunnel_profile_name="line-bridge-notify")
            raw["monitor"].update(text_mode="full", include_sender=True, retention_days=7, dedup_days=30)
            (root / "config.json").write_text(json.dumps(raw), encoding="utf-8")
            loaded = Config.load(root)
            self.assertTrue(loaded.sending_enabled)
            self.assertFalse(hasattr(loaded, "tunnel_enabled"))
            self.assertFalse(hasattr(loaded.monitor, "retention_days"))

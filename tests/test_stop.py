"""A stop result must include daemon exit, so immediate re-login can acquire its lock."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from line_bridge import cli
from line_bridge.config import Config
from line_bridge.errors import BridgeError


class StopTests(unittest.TestCase):
    def test_stop_waits_for_lock_release(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, 'load', return_value=(Config(), None)), \
             patch.object(cli, 'forward', return_value={'state': 'stopping'}), \
             patch.object(cli, 'time') as clock, patch.object(cli, 'ProcessLock') as lock:
            clock.monotonic.side_effect = [0, 0, 0.05]
            lock.return_value.__enter__.side_effect = [BridgeError('profile_already_running'), None]
            args = cli.parser().parse_args(['--data-dir', temp, 'stop'])
            self.assertEqual(cli.run(args), {'state': 'stopped'})
            self.assertEqual(lock.return_value.__enter__.call_count, 2)
            clock.sleep.assert_called_once_with(0.05)

    def test_unconfirmed_shutdown_is_not_reported_stopped(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, 'load', return_value=(Config(), None)), \
             patch.object(cli, 'forward', return_value={'state': 'unconfirmed'}):
            args = cli.parser().parse_args(['--data-dir', temp, 'stop'])
            with self.assertRaisesRegex(BridgeError, 'daemon_stop_not_confirmed'):
                cli.run(args)

    def test_shutdown_timeout_is_visible(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, 'load', return_value=(Config(), None)), \
             patch.object(cli, 'forward', return_value={'state': 'stopping'}), \
             patch.object(cli, 'time') as clock, patch.object(cli, 'ProcessLock') as lock:
            clock.monotonic.side_effect = [0, 0, 31]
            lock.return_value.__enter__.side_effect = BridgeError('profile_already_running')
            args = cli.parser().parse_args(['--data-dir', temp, 'stop'])
            with self.assertRaisesRegex(BridgeError, 'daemon_stop_timeout'):
                cli.run(args)

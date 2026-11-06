"""Real POSIX permissions, encryption binding, locks, and local terminal isolation."""
from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

from line_bridge.authentication import qr_terminal
from line_bridge.errors import BridgeError
from line_bridge.locking import ProcessLock
from line_bridge.security import (LinuxProtector, atomic_write, create_private_directory,
                                  load_secret, profile_protector, save_secret, verify_acl)
from line_bridge.storage import Store


class Terminal(StringIO):
    def isatty(self):
        return True


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux-specific')
class LinuxSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='linebridge-linux-security-')
        self.root = create_private_directory(Path(self.temp.name) / 'profile')
        self.identity = str(uuid.uuid4())
        self.protector = profile_protector(self.root, self.identity, create=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_cipher_is_randomized_authenticated_and_profile_bound(self):
        first = self.protector.seal(b'fixture private value')
        second = self.protector.seal(b'fixture private value')
        self.assertNotEqual(first, second)
        self.assertNotIn(b'fixture private value', first)
        self.assertEqual(profile_protector(self.root, self.identity).open(first), b'fixture private value')
        with self.assertRaises(BridgeError):
            profile_protector(self.root, str(uuid.uuid4())).open(first)
        with self.assertRaises(BridgeError):
            self.protector.open(first[:-1] + bytes([first[-1] ^ 1]))

    def test_foreign_profile_and_windows_cipher_refused(self):
        other_root = create_private_directory(Path(self.temp.name) / 'other')
        other = profile_protector(other_root, self.identity, create=True)
        with self.assertRaises(BridgeError):
            other.open(self.protector.seal(b'fixture'))
        with self.assertRaises(BridgeError):
            self.protector.open(b'not a Linux encrypted file')

    def test_key_and_secret_are_owner_only_even_under_permissive_umask(self):
        old_mask = os.umask(0)
        try:
            secret = self.root / 'fixture.dpapi'
            save_secret(secret, {'value': 'fixture'}, self.protector)
            self.assertEqual(load_secret(secret, self.protector), {'value': 'fixture'})
            store = Store(self.root, self.identity, self.protector)
            store.put_meta('fixture', 'value')
            self.assertEqual(store.get_meta('fixture'), 'value')
        finally:
            os.umask(old_mask)
        verify_acl(self.root)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        for path in self.root.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_missing_key_is_never_implicitly_replaced(self):
        key = self.root / 'profile-key.bin'
        key.unlink()
        with self.assertRaisesRegex(BridgeError, 'profile_key_missing'):
            profile_protector(self.root, self.identity)
        self.assertFalse(key.exists())

    def test_existing_key_is_never_overwritten(self):
        key = self.root / 'profile-key.bin'
        before = key.read_bytes()
        with self.assertRaises(BridgeError):
            profile_protector(self.root, self.identity, create=True)
        self.assertEqual(key.read_bytes(), before)

    def test_wide_key_permissions_refused_without_repair(self):
        key = self.root / 'profile-key.bin'
        key.chmod(0o644)
        with self.assertRaises(BridgeError):
            profile_protector(self.root, self.identity)
        self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o644)

    def test_symlink_secret_and_directory_refused(self):
        target = Path(self.temp.name) / 'external'
        target.mkdir(mode=0o700)
        linked = Path(self.temp.name) / 'linked'
        linked.symlink_to(target, target_is_directory=True)
        with self.assertRaises(BridgeError):
            create_private_directory(linked)
        secret = self.root / 'linked.dpapi'
        secret.symlink_to(self.root / 'profile-key.bin')
        with self.assertRaises(BridgeError):
            load_secret(secret, self.protector)

    def test_hardlinked_key_refused(self):
        os.link(self.root / 'profile-key.bin', self.root / 'copy.bin')
        with self.assertRaises(BridgeError):
            profile_protector(self.root, self.identity)

    def test_existing_public_directory_is_not_silently_chmodded(self):
        unsafe = Path(self.temp.name) / 'unsafe'
        unsafe.mkdir()
        unsafe.chmod(0o755)
        with self.assertRaises(BridgeError):
            create_private_directory(unsafe)
        self.assertEqual(stat.S_IMODE(unsafe.stat().st_mode), 0o755)

    def test_profile_lock_excludes_another_open_handle(self):
        with ProcessLock(self.root / 'profile.lock'):
            with self.assertRaises(BridgeError):
                with ProcessLock(self.root / 'profile.lock'):
                    self.fail('second lock was acquired')
        with ProcessLock(self.root / 'profile.lock'):
            pass


class TerminalLoginTests(unittest.TestCase):
    def test_noninteractive_login_never_starts_worker(self):
        def worker(*args):
            self.fail('login must not start without a local interactive terminal')
        with patch('sys.stdin', StringIO()), patch('sys.stderr', StringIO()):
            with self.assertRaisesRegex(BridgeError, 'interactive_terminal_required'):
                qr_terminal(worker)

    def test_synthetic_qr_and_pin_stay_off_stdout(self):
        def worker(messages, cancel):
            messages.put(('qr', 'https://example.invalid/fixture-qr'))
            messages.put(('pin', '123456'))
            return {'synthetic': True}
        terminal, output = Terminal(), StringIO()
        with patch('sys.stdin', Terminal()), patch('sys.stderr', terminal), redirect_stdout(output):
            self.assertEqual(qr_terminal(worker), {'synthetic': True})
        self.assertIn('123456', terminal.getvalue())
        self.assertEqual(output.getvalue(), '')

"""Verify recipient-specific config round trips and credential exclusion."""
import importlib.util
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from line_bridge import __version__
from line_bridge import cli
from line_bridge.config import Config

ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
    def test_codex_config_preserves_spaces_unicode_and_quotes(self):
        command = ['D:\\友人の PC\\"trial"\\python.exe', '-B', 'D:\\友人の PC\\linebridge.py']
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, 'load', return_value=(Config(), None)), \
             patch.object(cli, 'launcher', return_value=command):
            args = cli.parser().parse_args(['--data-dir', temp, 'clients', '--format', 'codex'])
            server = tomllib.loads(cli.run(args))['mcp_servers']['line']
            self.assertEqual(server['command'], command[0])
            self.assertEqual(server['args'], [*command[1:], '--data-dir', str(Path(temp).absolute()), 'stdio', '--auto-start', '--role', 'local'])
            self.assertEqual(server['tool_timeout_sec'], 190)

    def test_default_json_and_opencode_use_the_same_local_role(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, 'load', return_value=(Config(), None)), \
             patch.object(cli, 'launcher', return_value=['python.exe', '-B', 'linebridge.py']):
            both = cli.run(cli.parser().parse_args(['--data-dir', temp, 'clients']))
            opencode = cli.run(cli.parser().parse_args(['--data-dir', temp, 'clients', '--format', 'opencode']))
            self.assertEqual(opencode, both['opencode_v2'])
            self.assertEqual(opencode['mcp']['servers']['line']['command'][-2:], ['--role', 'local'])
            self.assertFalse(both['configuration_applied'])

    def test_version_and_default_dependency_scope(self):
        project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
        self.assertEqual(project['version'], __version__)
        names = [s.split('==')[0] for s in project['dependencies']]
        self.assertNotIn('okline', names)
        self.assertNotIn('line-personal-mcp', names)
        self.assertNotIn('mcp', names)

    def test_allowlist_excludes_profiles_runtimes_and_arbitrary_json(self):
        spec = importlib.util.spec_from_file_location('release_scan_test', ROOT / 'tools/release-scan.py')
        scanner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scanner)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = ['README.md', 'LineBridge.cmd', 'START-HERE.md', 'scripts/menu.ps1',
                     'src/line_bridge/bridge.py', 'docs/USER-GUIDE.md',
                     'profiles/default/session.dpapi', 'profiles/default/store.sqlite',
                     'runtime/node/node.exe', '.env', 'docs/session.json', 'src/line_bridge/endpoint.json',
                     'docs/unknown-private-note.md', 'src/line_bridge/__pycache__/cache.py']
            for name in files:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture', encoding='utf-8')
            with patch.object(scanner, 'ROOT', root):
                found = {p.relative_to(root).as_posix() for p in scanner.release_files()}
            self.assertEqual(found, set(files[:6]))

    def test_scanner_detects_embedded_credential_without_printing_it(self):
        spec = importlib.util.spec_from_file_location('release_scan_patterns', ROOT / 'tools/release-scan.py')
        scanner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scanner)
        value = 'accessToken = ' + json.dumps('A' * 40)
        result = scanner.findings(value, 'fixture.py')
        self.assertEqual(result, [{'file': 'fixture.py', 'rule': 'credential_assignment', 'line': 1}])
        self.assertNotIn('A' * 40, json.dumps(result))

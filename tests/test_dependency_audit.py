"""Exercise audit scripts on synthetic files and mocked public responses only."""
from contextlib import redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
import runpy
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "@csstools/css-calc@2.1.4_@csstools+css-parser-algorithms@3.0.5_@csstools+css-tokenizer@3.0.4": ("@csstools/css-calc", "2.1.4"),
    "@csstools/css-parser-algorithms@3.0.5_@csstools+css-tokenizer@3.0.4": ("@csstools/css-parser-algorithms", "3.0.5"),
    "isomorphic-ws@4.0.1_ws@5.2.7": ("isomorphic-ws", "4.0.1"),
    "node-bignumber@1.2.2": ("node-bignumber", "1.2.2"),
}


class DependencyAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="linebridge-audit-fixtures-")
        self.root = Path(self.temp.name)
        for folder in ("tools", "docs", "src/line_bridge/js"):
            (self.root / folder).mkdir(parents=True)
        (self.root / "src/line_bridge/js/deno.lock").write_text(json.dumps({"npm": {p: {} for p in PACKAGES}}))

    def tearDown(self):
        self.temp.cleanup()

    def script(self, name):
        destination = self.root / "tools" / name
        shutil.copyfile(ROOT / "tools" / name, destination)
        with redirect_stdout(StringIO()):
            runpy.run_path(str(destination), run_name="__main__")

    def test_osv_queries_use_base_name_and_version_not_peer_resolution_suffix(self):
        def response(request, timeout):
            queries = json.loads(request.data)["queries"]
            expected = [{"package": {"ecosystem": "npm", "name": PACKAGES[p][0]}, "version": PACKAGES[p][1]}
                        for p in sorted(PACKAGES)]
            self.assertEqual(queries, expected)
            return BytesIO(json.dumps({"results": [{} for _ in queries]}).encode())

        with patch("urllib.request.urlopen", side_effect=response) as fetch:
            self.script("audit-js.py")
        fetch.assert_called_once()
        report = json.loads((self.root / "docs/js-vulnerability-audit.json").read_text())
        self.assertEqual(len(report["packages"]), len(PACKAGES))

    def test_license_cache_inventory_handles_peer_suffixes_and_v_prefixed_version(self):
        for name, version in PACKAGES.values():
            directory = self.root / "runtime/deno-cache/npm/registry.npmjs.org" / name / version
            directory.mkdir(parents=True)
            (directory / "package.json").write_text(json.dumps({"name": name,
                "version": "v" + version if name == "node-bignumber" else version, "license": "MIT"}))
            (directory / "LICENSE").write_text("Synthetic fixture notice")
        with patch("importlib.metadata.distribution", return_value=SimpleNamespace(version="fixture", files=[])):
            self.script("review-license-assets.py")
        report = json.loads((self.root / "docs/LICENSE-ASSETS.json").read_text())
        self.assertTrue(all(p["manifest_present"] and p["manifest_matches_lock"] for p in report["npm"]))
        self.assertTrue(all(len(p["notice_files"]) == 1 for p in report["npm"]))
        self.assertFalse(report["license_review_complete"])

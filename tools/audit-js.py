"""Query OSV using PUBLIC dependency names/versions only. No LINE data or runtime execution."""
import datetime
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
lock = json.loads((ROOT / "src/line_bridge/js/deno.lock").read_text())
identifiers = sorted(lock.get("npm", {}))
queries = []
for identifier in identifiers:
    name, version = identifier.split("_", 1)[0].rsplit("@", 1)
    queries.append({"package": {"ecosystem": "npm", "name": name}, "version": version})
request = urllib.request.Request("https://api.osv.dev/v1/querybatch", data=json.dumps({"queries": queries}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "LineBridge-dependency-audit"})
try:
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.load(response)
    results = data["results"]
    if len(results) != len(identifiers):
        raise ValueError()
except Exception:
    raise SystemExit("public_dependency_audit_unavailable_details_withheld")
report = {"checked_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "service": "OSV",
          "scope": "Deno lock npm graph; JSR, embedded WASM, native binaries and unpublished source are not covered",
          "packages": [{"identifier": identifier, "vulnerabilities": [v["id"] for v in result.get("vulns", [])]}
                       for identifier, result in zip(identifiers, results)]}
target = ROOT / "docs/js-vulnerability-audit.json"
target.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps({"public_npm_versions_checked": len(identifiers), "affected_packages": sum(bool(r.get("vulns")) for r in results),
                  "vulnerability_ids": sorted({v["id"] for r in results for v in r.get("vulns", [])}), "line_data_accessed": False}, indent=2))

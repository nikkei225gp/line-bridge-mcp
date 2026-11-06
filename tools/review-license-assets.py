"""Inspect local dependency notices; inventory is not redistribution clearance."""
import hashlib
import datetime
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lock = json.loads((ROOT / "src/line_bridge/js/deno.lock").read_text(encoding="utf-8"))
cache = ROOT / "runtime/deno-cache/npm/registry.npmjs.org"


def asset(path, relative_to):
    return {"file": path.relative_to(relative_to).as_posix(), "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


npm = []
for identifier in sorted(lock.get("npm", {})):
    # Deno appends peer resolution IDs, which themselves contain @ signs.
    name, version = identifier.split("_", 1)[0].rsplit("@", 1)
    directory = cache / name / version
    manifest = directory / "package.json"
    package = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
    notices = []
    embedded = []
    if directory.is_dir():
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            if path.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")):
                notices.append(asset(path, directory))
            if path.suffix.lower() in (".wasm", ".node", ".dll", ".pyd"):
                embedded.append(asset(path, directory))
    npm.append({"identifier": identifier, "manifest_present": manifest.is_file(),
                "manifest_version": package.get("version"),
                "manifest_matches_lock": package.get("name") == name and str(package.get("version", "")).removeprefix("v") == version,
                "declared_license": package.get("license"), "notice_files": notices, "embedded_native_assets": embedded})

linejs = ROOT / "runtime/linejs"
generated = []
for name in ("packages/types/thrift.ts", "packages/types/line_types.ts", "packages/linejs/base/thrift/readwrite/struct.ts"):
    path = linejs / name
    if path.is_file():
        generated.append(asset(path, linejs))

blockers = [{"id": "generated_types_provenance", "component": "linejs",
             "state": "unresolved", "detail": "Root MIT notice found; generated schema provenance has not been independently cleared"},
            {"id": "embedded_opus_notices", "component": "opusscript",
             "state": "unresolved", "detail": "Package license declaration does not alone clear bundled codec assets"},
            {"id": "runtime_resources", "state": "unresolved",
             "detail": "Deno embedded notices, JSR packages require full review; not bundled in source candidate"}]
for package in npm:
    if not package["notice_files"]:
        blockers.append({"id": "missing_package_notice", "component": package["identifier"],
                         "state": "no_separate_notice_found_in_local_cache"})

result = {"schema": 1, "checked_at": datetime.date.today().isoformat(), "scope": "Locally installed/cached locked dependencies only",
          "license_review_complete": False, "distribution_approved": False, "npm": npm,
          "linejs_root_notice": asset(linejs / "LICENSE", linejs) if (linejs / "LICENSE").is_file() else None,
          "linejs_generated_assets": generated, "blockers": blockers,
          "limitations": "Not legal advice or a complete provenance/compatibility review. Missing files are not evidence of permission."}
(ROOT / "docs/LICENSE-ASSETS.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"npm_packages": len(npm), "missing_manifests": sum(not p["manifest_present"] for p in npm),
    "manifest_mismatches": sum(not p["manifest_matches_lock"] for p in npm),
    "packages_without_notice_files": [p["identifier"] for p in npm if not p["notice_files"]],
    "blockers": blockers, "license_review_complete": False}, indent=2))

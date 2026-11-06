"""Local metadata inventory, not a claim that embedded-resource licensing is complete."""
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
packages = []
for d in sorted(metadata.distributions(), key=lambda x: x.metadata.get("Name", "").casefold()):
    name = d.metadata.get("Name", "")
    if name == "line-bridge":
        continue
    paths = list(d.files or [])
    licenses = []
    for relative in paths:
        filename = str(relative).replace("\\", "/")
        if "dist-info/" in filename and ("/licenses/" in filename or Path(filename).name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))):
            path = d.locate_file(relative)
            if path.is_file():
                licenses.append({"file": Path(filename).name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    packages.append({"name": name, "version": d.version,
                     "declared_license": d.metadata.get("License-Expression") or d.metadata.get("License") or None,
                     "license_files": licenses,
                     "embedded_js_wasm_native_files": sum(Path(str(p)).suffix.lower() in (".js", ".wasm", ".dll", ".pyd") for p in paths)})
lock = json.loads((ROOT / "src/line_bridge/js/deno.lock").read_text())
js = []
for identifier in sorted(lock.get("npm", {})):
    js.append({"identifier": identifier, "integrity": lock["npm"][identifier]["integrity"]})
result = {"schema": 1, "license_review_complete": False,
          "scope": "Installed Python metadata and Deno lock graph; embedded resources need manual review",
          "python": packages, "deno_npm": js, "deno_jsr": lock.get("jsr", {}),
          "redistributed_binaries": False, "release_gate": "No public distribution until embedded resources are reviewed"}
target = ROOT / "docs/DEPENDENCIES.json"
target.parent.mkdir(exist_ok=True)
target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"python_packages": len(packages), "deno_npm_packages": len(js), "license_review_complete": False}))

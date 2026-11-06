"""Download only pinned public dependencies; never read LINE state or start login."""
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def unpack(archive, destination, strip_first=False, skip_documented_node_links=False):
    destination.mkdir(parents=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            for entry in source.infolist():
                if entry.is_dir():
                    continue
                parts = PurePosixPath(entry.filename).parts
                if not parts or any(p in ("..", ".") or ":" in p or "\\" in p for p in parts) or entry.filename.startswith("/"):
                    raise ValueError("unsafe_archive_path")
                target = destination.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as src, target.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive) as source:
            for entry in source.getmembers():
                if entry.isdir():
                    continue
                if entry.issym() and len(PurePosixPath(entry.name).parts) == 2 and PurePosixPath(entry.name).name == "README.md":
                    # Upstream's root README is a documentation symlink, not runtime code.
                    continue
                if entry.issym() and skip_documented_node_links:
                    # npm/npx/corepack links are not used by LineBridge's Node runtime.
                    continue
                if not entry.isfile():
                    raise ValueError("archive_links_refused")
                parts = PurePosixPath(entry.name).parts
                if not parts or any(p in ("..", ".") or ":" in p or "\\" in p for p in parts) or entry.name.startswith("/"):
                    raise ValueError("unsafe_archive_path")
                if strip_first:
                    parts = parts[1:]
                if not parts:
                    continue
                target = destination.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(entry) as src, target.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
                if entry.mode & 0o111:
                    target.chmod(0o755)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--resolve-lock", action="store_true", help="Maintainer only: derive a fresh Deno lock")
    p.add_argument("--uv-only", action="store_true", help="Linux installer: fetch the pinned uv binary first")
    args = p.parse_args()
    pins = json.loads((ROOT / "runtime-pins.json").read_text())
    linux = sys.platform.startswith("linux")
    if linux:
        if platform.machine() not in ("x86_64", "amd64"):
            raise ValueError("linux_x86_64_required")
        pins.update(pins["linux_x64"])
    if args.uv_only and not linux:
        raise ValueError("uv_bootstrap_linux_only")
    runtime = ROOT / "runtime"
    runtime.mkdir(exist_ok=True)
    for name in (("uv",) if args.uv_only else ("deno", "linejs")):
        target = runtime / name
        marker = target / ".linebridge-source-sha256"
        expected = pins[name]["sha256"]
        if target.exists():
            if not marker.exists() or marker.read_text() != expected:
                raise ValueError("existing_runtime_not_verified_" + name)
            continue
        with tempfile.TemporaryDirectory(prefix="bootstrap-", dir=runtime) as temp:
            archive = Path(temp) / "archive"
            with urllib.request.urlopen(pins[name]["url"], timeout=120) as src, archive.open("xb") as dst:
                shutil.copyfileobj(src, dst)
            if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
                raise ValueError("download_hash_mismatch_" + name)
            extracted = Path(temp) / "extracted"
            unpack(archive, extracted, name in ("linejs", "uv"), skip_documented_node_links=name == "node")
            if linux and name == "deno":
                (extracted / "deno").chmod(0o755)
            (extracted / ".linebridge-source-sha256").write_text(expected)
            os.replace(extracted, target)
    if args.uv_only:
        print(json.dumps({"state": "uv_ready", "sessions_accessed": False}))
        return
    deno = runtime / "deno" / ("deno" if linux else "deno.exe")
    source = runtime / "linejs"
    upstream_config = json.loads((source / "deno.json").read_text(encoding="utf-8"))
    package_config_path = source / "packages/linejs/deno.json"
    package_config = json.loads(package_config_path.read_text(encoding="utf-8"))
    for name, version in pins.get("npm_security_overrides", {}).items():
        upstream_config["imports"][name] = "npm:" + name + "@" + version
        package_config["imports"][name] = "npm:" + name + "@" + version
    package_config_path.write_text(json.dumps(package_config, indent=2), encoding="utf-8")
    narrow_config = {"workspace": ["./packages/linejs", "./packages/types"],
                     "imports": upstream_config["imports"], "nodeModulesDir": "auto",
                     "compilerOptions": upstream_config["compilerOptions"], "lock": "./deno.lock"}
    (source / "linebridge-deno.json").write_text(json.dumps(narrow_config, indent=2), encoding="utf-8")
    packaged_lock = ROOT / "src" / "line_bridge" / "js" / "deno.lock"
    if args.resolve_lock:
        (source / "deno.lock").unlink(missing_ok=True)
    if not args.resolve_lock:
        shutil.copyfile(packaged_lock, source / "deno.lock")
    environment = dict(os.environ)
    environment["DENO_DIR"] = str(runtime / "deno-cache")
    command = [str(deno), "cache", "--no-check", "--config", str(source / "linebridge-deno.json")]
    if not args.resolve_lock:
        command.append("--frozen")
    command += [str(source / "packages" / "linejs" / "base" / "mod.ts")]
    result = subprocess.run(command, cwd=source, env=environment, capture_output=True)
    if result.returncode:
        raise ValueError("deno_dependency_cache_failed")
    if args.resolve_lock:
        shutil.copyfile(source / "deno.lock", packaged_lock)
    print(json.dumps({"state": "runtime_ready", "deno": str(deno), "linejs": str(source),
                      "sessions_accessed": False, "workers_started": 0}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("runtime_bootstrap_failed_details_withheld")

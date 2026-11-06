"""Build a credential-free source ZIP locally. Never upload or message anyone."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import tomllib
import io
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(kind="friend-trial", platform="windows-x64"):
    spec = importlib.util.spec_from_file_location("release_scan", ROOT / "tools/release-scan.py")
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    result = scanner.scan()
    if result["findings"]:
        raise ValueError("source_content_scan_has_findings")
    files = scanner.release_files()
    manifest = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    extension = ".tar.gz" if platform == "linux-x64" else ".zip"
    out = ROOT / "dist" / f"LineBridge-{version}-{platform}-{kind}{extension}"
    out.parent.mkdir(exist_ok=True)
    extras = {"SOURCE-MANIFEST.json": json.dumps({"schema": 1,
            "files": [{"path": name, "sha256": digest} for name, digest in sorted(manifest.items())]}, indent=2).encode(),
              "RELEASE.json": json.dumps({"version": version, "kind": kind,
            "platform": "Ubuntu 24.04 LTS desktop x86_64" if platform == "linux-x64" else "Windows 11 x64",
            "contents": "First-party source and documentation only",
            "dependencies_downloaded_during_install": True,
            "credentials_or_chat_history_included": False, "third_party_binaries_included": False,
            "fresh_user_qr_login_verified": False, "signed_executable": False}, indent=2).encode()}
    if platform == "linux-x64":
        with tarfile.open(out, "w:gz") as archive:
            for path in files:
                info = archive.gettarinfo(str(path), "line-bridge/" + path.relative_to(ROOT).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o755 if path.suffix == ".sh" else 0o644
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
            for name, body in extras.items():
                info = tarfile.TarInfo("line-bridge/" + name)
                info.size, info.mode = len(body), 0o644
                archive.addfile(info, io.BytesIO(body))
        with tarfile.open(out) as archive:
            contents = {member.name: archive.extractfile(member).read() for member in archive.getmembers()}
    else:
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, "line-bridge/" + path.relative_to(ROOT).as_posix())
            for name, body in extras.items():
                archive.writestr("line-bridge/" + name, body)
        with zipfile.ZipFile(out) as archive:
            if archive.testzip() is not None:
                raise ValueError("release_zip_corrupt")
            contents = {name: archive.read(name) for name in archive.namelist()}
    for name, digest in manifest.items():
        if hashlib.sha256(contents["line-bridge/" + name]).hexdigest() != digest:
            raise ValueError("release_manifest_mismatch")
    if len(contents) != len(manifest) + 2:
        raise ValueError("unexpected_release_member")
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    out.with_name(out.name + ".sha256.txt").write_text(digest + "  " + out.name + "\n", encoding="ascii")
    return {"archive": out.name, "files": len(files), "bytes": out.stat().st_size,
            "sha256": digest, "manifest_verified": True, "uploaded": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("friend-trial", "candidate"), default="friend-trial")
    parser.add_argument("--platform", choices=("windows-x64", "linux-x64"), default="windows-x64")
    args = parser.parse_args()
    print(json.dumps(build(args.kind, args.platform), indent=2))

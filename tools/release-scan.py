"""Explicit release allowlist + content checks + every local Git revision, not .gitignore."""
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
TOP = {"README.md", "BOARD.md", "START-HERE.md", "START-HERE-LINUX.md", "LineBridge.cmd", "LineBridge.sh", "LICENSE", ".gitignore", "pyproject.toml", "uv.lock", "runtime-pins.json", "THIRD-PARTY-NOTICES.md"}
FOLDERS = {".github/workflows": {".yml", ".yaml"}, "src/line_bridge": {".py", ".ts", ".lock"}, "bin": {".py"}, "tests": {".py", ".ts"},
           "tools": {".py"}, "scripts": {".ps1", ".sh"}, "third_party": {".txt"}}
DOCUMENTS = {"USER-GUIDE.md", "SECURITY-AND-LIMITS.md", "TEST-STATUS.md", "FRIEND-TRIAL-CHECKS.md", "LINUX-TRIAL-CHECKS.md",
              "LIVE-CHECKS.md",
             "DEPENDENCIES.json", "LICENSE-ASSETS.json", "js-vulnerability-audit.json",
             "python-vulnerability-audit.json"}
PATTERNS = {
    "fixed_user_path": re.compile(r"C:[\\/]Users[\\/](?!<|YOUR_|%)[A-Za-z0-9_.-]+", re.I),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "provider_token": re.compile(r"\b(?:ghp_|github_pat_|xox[baprs]-|sk-(?:proj-)?)[A-Za-z0-9_-]{24,}\b"),
    "credential_assignment": re.compile(r'''(?:accessToken|access_token|refreshToken|refresh_token|api_key|password)\s*[=:]\s*["']([A-Za-z0-9_+/=-]{24,})["']'''),
    "whsec_literal": re.compile(r'''["']whsec_[A-Za-z0-9+/]{32,}={0,2}["']'''),
}


def release_files():
    files = [ROOT / name for name in TOP if (ROOT / name).is_file()]
    files += [ROOT / "docs" / name for name in DOCUMENTS if (ROOT / "docs" / name).is_file()]
    for folder, extensions in FOLDERS.items():
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and path.suffix in extensions and "__pycache__" not in path.parts:
                if path.is_symlink():
                    raise ValueError("release_symlink_refused")
                files.append(path)
    for path in files:
        if path.is_symlink() or any((ROOT / parent).is_symlink() for parent in path.relative_to(ROOT).parents):
            raise ValueError("release_symlink_refused")
    return sorted(set(files))


def findings(text, label):
    return [{"file": label, "rule": rule, "line": text.count("\n", 0, match.start()) + 1}
            for rule, pattern in PATTERNS.items() for match in pattern.finditer(text)]


def scan():
    found = []
    files = release_files()
    for path in files:
        found += findings(path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix())
    history = {"state": "no_git_history", "commits_checked": 0}
    if (ROOT / ".git").exists():
        run = lambda args: subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=True).stdout
        commits = run(["rev-list", "--all"]).decode().splitlines()
        checked = set()
        for commit in commits:
            for row in run(["ls-tree", "-rz", "--full-tree", commit]).split(b"\0"):
                if not row:
                    continue
                attributes, filename = row.split(b"\t", 1)
                _, kind, digest = attributes.split()
                if kind != b"blob" or digest in checked:
                    continue
                checked.add(digest)
                text = run(["cat-file", "blob", digest.decode()]).decode("utf-8", errors="replace")
                found += findings(text, "history:" + filename.decode("utf-8", errors="replace"))
        history = {"state": "all_local_refs_checked", "commits_checked": len(commits), "unique_blobs_checked": len(checked)}
    return {"files_checked": len(files), "findings": found, "history": history,
            "binary_credentials_and_profiles_included": False,
            "scope": "Explicit source release allowlist; no account/session/store files and no downloaded runtimes",
            "limitations": "Pattern detection is not complete. Independent secret scanning remains a release gate."}


if __name__ == "__main__":
    result = scan()
    print(json.dumps(result, indent=2))
    raise SystemExit(1 if result["findings"] else 0)

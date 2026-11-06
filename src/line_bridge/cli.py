from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

from .bridge import Bridge
from .config import Config, MonitorPolicy, NotifyPolicy, default_root
from .errors import BridgeError
from .locking import ProcessLock
from .providers import make_providers
from .platform_runtime import process_options
from .security import profile_protector, check_local_path, create_private_directory, load_secret, save_secret, verify_acl
from .storage import Store
from .transport import endpoint, forward, serve, stdio


def launcher():
    source_launcher = Path(__file__).resolve().parents[2] / "bin" / "linebridge.py"
    executable = sys.executable
    if os.name == "nt":
        # Prefer the windowless interpreter so MCP clients do not
        # pop a console window when they spawn the stdio frontend or daemon.
        windowless = Path(executable).with_name("pythonw.exe")
        if windowless.is_file():
            executable = str(windowless)
    return [executable, "-B", str(source_launcher)] if source_launcher.is_file() else [executable, "-B", "-m", "line_bridge"]


def load(root):
    check_local_path(root)
    verify_acl(root)
    config = Config.load(root)
    protector = profile_protector(root, config.profile_id)
    return config, protector


def build(root, config, protector):
    return Bridge(config, Store(root, config.profile_id, protector), make_providers(root, config, protector))


def initialize(root):
    create_private_directory(root)
    if (root / "config.json").exists():
        raise BridgeError("profile_already_exists")
    config = Config()
    protector = profile_protector(root, config.profile_id, create=True)
    for role in ("local", "admin"):
        save_secret(root / (role + "-key.dpapi"), secrets.token_urlsafe(48), protector)
    config.save(root)
    return {"state": "initialized", "sending_enabled": config.sending_enabled, "monitor_enabled": False,
            "login_device": config.login_device, "autostart_registered": False}


def call_status(root, protector):
    response = forward(root, protector, "local", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "bridge_status", "arguments": {}}})
    if not response or "error" in response or response["result"].get("isError"):
        raise BridgeError("daemon_status_unavailable")
    return response["result"]["structuredContent"]


def start_daemon(root, protector):
    try:
        call_status(root, protector)
        return {"state": "already_running"}
    except BridgeError:
        pass
    process = subprocess.Popen([*launcher(), "--data-dir", str(root), "daemon"], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **process_options(detached=True))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        # A simultaneous local MCP host may have won the daemon lock.
        try:
            call_status(root, protector)
            return {"state": "running", "autostart_registered": False}
        except BridgeError:
            if process.poll() is not None:
                try:
                    with ProcessLock(root / "daemon.lock"):
                        raise BridgeError("daemon_start_failed")
                except BridgeError as error:
                    if error.code != "profile_already_running":
                        raise
            time.sleep(0.2)
    raise BridgeError("daemon_start_timeout")


def parser():
    p = argparse.ArgumentParser(prog="linebridge")
    p.add_argument("--data-dir", type=Path, help="Private per-user profile directory; never a repository directory")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("daemon")
    commands.add_parser("start")
    commands.add_parser("stop")
    commands.add_parser("status")
    commands.add_parser("doctor")
    clients = commands.add_parser("clients")
    clients.add_argument("--format", choices=("json", "mcp", "codex", "opencode"), default="json")
    std = commands.add_parser("stdio")
    std.add_argument("--auto-start", action="store_true", help="Start the saved-profile daemon if needed; never logs in")
    # MCP host registrations pass "--role local"; it is the only role left.
    std.add_argument("--role", choices=("local",), default="local", help=argparse.SUPPRESS)
    runtime = commands.add_parser("runtime")
    runtime.add_argument("--deno", default="")
    runtime.add_argument("--linejs", default="")
    runtime.add_argument("--deno-cache", default="")
    auth = commands.add_parser("login")
    auth.add_argument("provider", nargs="?", default="unified", choices=("unified",))
    auth.add_argument("--replace", action="store_true", help="Explicit permission to replace this profile's session")
    auth.add_argument("--device", choices=("android", "ipad", "windows", "mac", "ANDROIDSECONDARY", "IOSIPAD", "DESKTOPWIN", "DESKTOPMAC"))
    auth.add_argument("--output", choices=("gui", "json", "text", "terminal"), default="gui",
                      help="json streams QR PNG Base64/URL and PIN; tokens and private keys are never exported")
    sending = commands.add_parser("sending")
    sending.add_argument("state", choices=("enable", "disable"))
    sending.add_argument("--ack-account-risk", action="store_true", help=argparse.SUPPRESS)
    monitor = commands.add_parser("monitor")
    monitor.add_argument("state", choices=("enable", "disable"))
    monitor.add_argument("--chat-ref", action="append", default=[])
    monitor.add_argument("--all", action="store_true")
    monitor.add_argument("--include-future-chats", action="store_true")
    monitor.add_argument("--interval-seconds", type=int, default=120)
    notify = commands.add_parser("notify", help="Wake an agent webhook with batched new-message summaries")
    notify.add_argument("state", choices=("enable", "disable", "test"))
    notify.add_argument("--url", default="", help="Webhook URL (e.g. a Grok Bot routine webhook)")
    notify.add_argument("--key-stdin", action="store_true", help="Read the webhook bearer key from stdin; or set LINEBRIDGE_NOTIFY_KEY")
    notify.add_argument("--instant-chat", action="append", default=[], help="chat_ref notified after the debounce instead of in the digest")
    notify.add_argument("--mute-chat", action="append", default=[], help="chat_ref never notified")
    notify.add_argument("--keyword", action="append", default=[], help="Text that escalates a message to urgent")
    notify.add_argument("--no-direct-instant", action="store_true", help="Put 1:1 chats in the digest too")
    notify.add_argument("--debounce-seconds", type=int, default=120)
    notify.add_argument("--digest-minutes", type=int, default=60)
    notify.add_argument("--quiet-hours", default="0-7", help="START-END local hours; equal values disable quiet hours")
    notify.add_argument("--utc-offset-minutes", type=int, default=540, help="Local time offset for quiet hours; 540 is Japan")
    notify.add_argument("--hourly-cap", type=int, default=6)
    return p


def notify_key(args):
    if args.key_stdin:
        return sys.stdin.readline().strip()
    return os.environ.get("LINEBRIDGE_NOTIFY_KEY", "").strip()


def run(args):
    root = check_local_path(args.data_dir or default_root())
    if args.command == "init":
        return initialize(root)
    config, protector = load(root)
    if args.command == "stdio":
        if args.auto_start:
            start_daemon(root, protector)
        stdio(root, protector)
        return None
    if args.command == "daemon":
        with ProcessLock(root / "daemon.lock"):
            bridge = build(root, config, protector)
            serve(root, protector, bridge)
        return None
    if args.command == "status":
        return call_status(root, protector)
    if args.command == "doctor":
        from .authentication import unified_session_path
        unified_present = bool(config.unified_generation and unified_session_path(root, config.unified_generation).is_file())
        return {"windows": os.name == "nt", "platform": sys.platform, "python": sys.version.split()[0], "acl_checked": True,
                "permissions_checked": True, "secret_storage": "windows-dpapi" if os.name == "nt" else "linux-aes256gcm-owner-only-key",
                "authentication_mode": "unified" if config.unified_generation else "not_authenticated",
                "login_device": config.login_device,
                "personal_session_present": unified_present,
                "openchat_session_present": unified_present,
                 "deno_configured": bool(config.deno_executable),
                "linejs_configured": bool(config.linejs_directory),
                "monitor_enabled": config.monitor.enabled, "sending_enabled": config.sending_enabled}
    if args.command == "start":
        return start_daemon(root, protector)
    if args.command == "stop":
        result = forward(root, protector, "admin", {"operation": "stop"})
        if not isinstance(result, dict) or result.get("state") != "stopping":
            raise BridgeError("daemon_stop_not_confirmed")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with ProcessLock(root / "daemon.lock"):
                    return {"state": "stopped"}
            except BridgeError as error:
                if error.code != "profile_already_running":
                    raise
                time.sleep(0.05)
        raise BridgeError("daemon_stop_timeout")
    if args.command == "clients":
        command = [*launcher(), "--data-dir", str(root), "stdio", "--auto-start", "--role", "local"]
        generic = {"mcpServers": {"line": {"command": command[0], "args": command[1:]}}}
        if args.format == "mcp":
            return generic
        if args.format == "codex":
            return "\n".join(["[mcp_servers.line]", "command = " + json.dumps(command[0], ensure_ascii=False),
                              "args = " + json.dumps(command[1:], ensure_ascii=False), "tool_timeout_sec = 190"])
        if args.format == "opencode":
            return {"$schema": "https://opencode.ai/config.json", "mcp": {"servers": {"line": {"type": "local", "command": command}}}}
        return {**generic, "opencode_v2": {"$schema": "https://opencode.ai/config.json", "mcp": {"servers": {"line": {"type": "local", "command": command}}}},
                "codex": {"mcp_servers": {"line": {"command": command[0], "args": command[1:], "tool_timeout_sec": 190}}},
                "configuration_applied": False}
    if args.command == "notify" and args.state == "test":
        import datetime as dt
        from .notify import TARGET_FILE, post_webhook
        target = load_secret(root / TARGET_FILE, protector)
        body = {"type": "line.notify_test", "sent_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "total": 0, "chats": [], "hint": "Connection test from LineBridge. No LINE data."}
        return {"state": "sent", "status": post_webhook(target["url"], target["key"], json.dumps(body).encode("utf-8"))}
    # All configuration/authentication changes require the profile daemon to be stopped.
    with ProcessLock(root / "daemon.lock"):
        if args.command == "runtime":
            for arg, attr in ((args.deno, "deno_executable"), (args.linejs, "linejs_directory"), (args.deno_cache, "deno_cache_directory")):
                if arg:
                    path = Path(arg).resolve()
                    if not path.exists():
                        raise BridgeError("runtime_path_missing")
                    setattr(config, attr, str(path))
            config.save(root)
            return {"state": "runtime_configured"}
        if args.command == "login":
            existing = bool(config.unified_generation) or config.personal_mode != "disabled" or config.openchat_mode != "disabled"
            if existing and not args.replace:
                raise BridgeError("existing_session_requires_explicit_replace")
            from .authentication import unified_login
            return unified_login(root, config, protector, args.device, args.output)
        if args.command == "sending":
            config.sending_enabled = args.state == "enable"
            config.save(root)
            return {"sending_enabled": config.sending_enabled}
        if args.command == "monitor":
            if args.state == "disable":
                config.monitor = MonitorPolicy()
            else:
                if not args.chat_ref and not args.all:
                    raise BridgeError("explicit_monitor_targets_required")
                if args.include_future_chats and not args.all:
                    raise BridgeError("future_chats_require_explicit_all")
                store = Store(root, config.profile_id, protector)
                targets = sorted(set(args.chat_ref))
                if args.all and not targets:
                    with store.db() as db:
                        targets = [r[0] for r in db.execute("SELECT ref FROM refs WHERE kind='chat' AND generation=?",
                                                          (config.unified_generation,))]
                if not targets and not args.include_future_chats:
                    raise BridgeError("list_chats_before_enabling_monitoring")
                for ref in targets:
                    source = store.reference_source(ref)
                    store.resolve(ref, source, config.unified_generation)
                config.monitor = MonitorPolicy(enabled=True, scope="all" if args.all else "selected", targets=targets,
                    include_future_chats=args.include_future_chats, interval_seconds=args.interval_seconds)
                config.validate()
            config.save(root)
            return {"monitor_enabled": config.monitor.enabled, "scope": config.monitor.scope,
                    "interval_seconds": config.monitor.interval_seconds}
        if args.command == "notify":
            if args.state == "disable":
                config.notify = NotifyPolicy()
                config.save(root)
                return {"notify_enabled": False}
            from .notify import TARGET_FILE, check_url, save_target
            if not config.monitor.enabled:
                raise BridgeError("enable_monitor_first")
            if args.url:
                check_url(args.url)
                key = notify_key(args)
                if not key:
                    raise BridgeError("notify_key_required")
            elif not (root / TARGET_FILE).is_file():
                raise BridgeError("notify_url_required")
            store = Store(root, config.profile_id, protector)
            for ref in args.instant_chat + args.mute_chat:
                store.resolve(ref, store.reference_source(ref), config.unified_generation)
            try:
                quiet_start, quiet_end = (int(x) for x in args.quiet_hours.split("-", 1))
            except ValueError:
                raise BridgeError("quiet_hours_must_be_start_end") from None
            config.notify = NotifyPolicy(enabled=True, direct_instant=not args.no_direct_instant,
                instant_chats=sorted(set(args.instant_chat)), muted_chats=sorted(set(args.mute_chat)),
                keywords=sorted(set(k for k in args.keyword if k.strip())), debounce_seconds=args.debounce_seconds,
                digest_minutes=args.digest_minutes, quiet_start=quiet_start, quiet_end=quiet_end,
                utc_offset_minutes=args.utc_offset_minutes, hourly_cap=args.hourly_cap)
            config.validate()
            if args.url:
                save_target(root, protector, args.url, key)
            config.save(root)
            return {"notify_enabled": True, **{k: v for k, v in vars(config.notify).items() if k != "enabled"}}
    raise BridgeError("unknown_command")


def main():
    if sys.platform.startswith("linux"):
        os.umask(0o077)
    # Stable JSON output even when the install path contains non-CP932 characters.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    try:
        result = run(args)
        if result is not None:
            print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except BridgeError as error:
        print(error.code, file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        print("operation_failed_details_withheld", file=sys.stderr)
        raise SystemExit(1)

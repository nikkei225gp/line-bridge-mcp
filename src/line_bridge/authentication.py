"""Explicit QR login. GUI, CLI and local MCP may export temporary QR/PIN only."""
from __future__ import annotations

import json
import base64
import io
from dataclasses import replace
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import uuid

from .errors import BridgeError
from .security import save_secret
from .platform_runtime import process_options, worker_environment
from .devices import DEVICES, normalize_device


def reset_permissions_after_login(root, config, protector):
    from .config import MonitorPolicy
    # Monitor targets of a previous account/generation do not carry over.
    config.monitor = MonitorPolicy()
    config.save(root)


def qr_terminal(worker):
    # Secrets are displayed only in a human-owned interactive terminal, never MCP/log output.
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise BridgeError("interactive_terminal_required_for_qr_login")
    import qrcode
    messages = queue.Queue()
    cancel = threading.Event()

    def run():
        try:
            value = worker(messages, cancel)
            messages.put(("done", value) if value is not None and not cancel.is_set() else ("failed", None))
        except Exception:
            messages.put(("failed", None))

    print("Scan locally with YOUR LINE phone. Never share this QR or PIN. Ctrl-C cancels.", file=sys.stderr)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        while True:
            try:
                kind, value = messages.get(timeout=0.25)
            except queue.Empty:
                continue
            if kind == "qr":
                qr = qrcode.QRCode(border=4)
                qr.add_data(value)
                qr.make(fit=True)
                qr.print_ascii(out=sys.stderr, invert=True)
                sys.stderr.flush()
            elif kind == "pin":
                print("Enter this PIN on YOUR phone: " + str(value), file=sys.stderr, flush=True)
            elif kind == "done":
                return value
            else:
                raise BridgeError("qr_login_cancelled_or_failed")
    finally:
        cancel.set()
        thread.join(timeout=12)


def qr_window(worker):
    if sys.platform.startswith("linux") and (os.environ.get("LINEBRIDGE_LOGIN_UI") == "terminal" or not os.environ.get("DISPLAY")):
        return qr_terminal(worker)
    try:
        import tkinter as tk
    except ImportError:
        if sys.platform.startswith("linux"):
            return qr_terminal(worker)
        raise
    from PIL import ImageTk, Image, ImageDraw, ImageFont
    from tkinter import font as tkfont
    import qrcode

    messages = queue.Queue()
    cancel = threading.Event()
    result = {"state": "cancelled"}
    try:
        root = tk.Tk()
    except tk.TclError:
        if sys.platform.startswith("linux"):
            return qr_terminal(worker)
        raise
    root.title("LineBridge: local secondary-device QR login")
    text = tk.Label(root, text="Scan with your own LINE phone account. This QR is a login secret.",
                    font=("Helvetica", 12), wraplength=560)
    text.pack(padx=16, pady=(12, 8))
    image = tk.Label(root)
    image.pack()
    passcode = tk.Label(root, name="passcode", font=("Helvetica", 40, "bold"),
                        foreground="#111827")

    def run():
        try:
            value = worker(messages, cancel)
            if value is not None and not cancel.is_set():
                messages.put(("done", value))
        except Exception:
            messages.put(("failed", None))

    def update():
        try:
            while True:
                kind, value = messages.get_nowait()
                if kind == "qr":
                    picture = ImageTk.PhotoImage(qrcode.make(value).get_image())
                    image.configure(image=picture)
                    image.image = picture
                elif kind == "pin":
                    text.configure(text="Enter this passcode on your LINE phone.")
                    passcode.configure(text=str(value))
                    # Some bundled Linux Tk builds only have the tiny X11 fixed
                    # font. Pillow's embedded scalable font needs no system fonts.
                    if tkfont.Font(root=root, font=passcode.cget("font")).actual("size") < 32:
                        face = ImageFont.load_default(size=64)
                        bounds = face.getbbox(str(value), stroke_width=2)
                        background = tuple(channel // 257 for channel in root.winfo_rgb(root.cget("background")))
                        canvas = Image.new("RGB", (bounds[2] - bounds[0] + 24, bounds[3] - bounds[1] + 24), background)
                        ImageDraw.Draw(canvas).text((12 - bounds[0], 12 - bounds[1]), str(value),
                                                    font=face, fill="#111827", stroke_width=2)
                        picture = ImageTk.PhotoImage(canvas)
                        passcode.configure(image=picture)
                        passcode.image = picture
                    passcode.pack(after=image, padx=20, pady=(8, 12))
                elif kind == "done":
                    result.update(state="done", value=value)
                    root.destroy()
                    return
                elif kind == "failed":
                    result["state"] = "failed"
                    root.destroy()
                    return
        except queue.Empty:
            pass
        root.after(100, update)

    def close():
        cancel.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    tk.Button(root, text="Cancel", command=close).pack(pady=(4, 12))
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    root.after(100, update)
    root.mainloop()
    cancel.set()
    # The desktop worker checks cancellation every 250ms and kills its child in
    # finally. Wait for that cleanup before the CLI can exit and drop daemon threads.
    thread.join(timeout=12)
    if result["state"] != "done":
        raise BridgeError("qr_login_cancelled_or_failed")
    return result["value"]


def qr_payload(value):
    import qrcode
    buffer = io.BytesIO()
    qrcode.make(value).save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return {"qr_url": value, "qr_base64": encoded, "qr_mime_type": "image/png",
            "qr_data_url": "data:image/png;base64," + encoded}


def run_qr_worker(config, script_name, device, messages, cancel):
    script = Path(__file__).parent / "js" / script_name
    source = Path(config.linejs_directory).resolve()
    if not config.deno_executable or not (source / "deno.json").is_file():
        raise BridgeError("openchat_runtime_not_configured")

    command = [config.deno_executable, "run", "--cached-only", "--frozen", "--no-check", "--no-prompt",
               "--config", str(source / "linebridge-deno.json"), f"--allow-read={source},{script.parent}",
               "--allow-net=legy.line-apps.com:443,gf.line.naver.jp:443",
               "--allow-env=NODE_DEBUG,NODE_OPTIONS,DEBUG,NODE_ENV,Q_DEBUG,Q_LONG_STACKS,WS_NO_BUFFER_UTIL,WS_NO_UTF_8_VALIDATE",
               str(script), str(source), normalize_device(device)]
    env = worker_environment()
    if config.deno_cache_directory:
        env["DENO_DIR"] = config.deno_cache_directory
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                               **process_options())
    lines = queue.Queue(maxsize=16)
    def reader():
        while True:
            line = process.stdout.readline(262145)
            if not line or len(line) > 262144:
                lines.put(None)
                return
            lines.put(line)
    threading.Thread(target=reader, daemon=True).start()
    try:
        while not cancel.is_set():
            try:
                line = lines.get(timeout=0.25)
            except queue.Empty:
                continue
            if line is None:
                raise BridgeError("openchat_qr_failed")
            value = json.loads(line)
            if value.get("kind") in ("qr", "pin"):
                messages.put((value["kind"], value["value"]))
            elif value.get("kind") == "session":
                saved = value["value"]
                saved["bridge_generation"] = str(uuid.uuid4())
                return saved
            else:
                raise BridgeError("openchat_qr_failed")
        return None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        process.stdout.close()


def desktop_qr(config, script_name, device=None, output="gui"):
    device = normalize_device(device or config.login_device)
    def worker(messages, cancel):
        return run_qr_worker(config, script_name, device, messages, cancel)
    if output == "gui":
        return qr_window(worker)
    if output == "terminal":
        return qr_terminal(worker)
    if output not in ("json", "text"):
        raise BridgeError("invalid_login_output")
    class Output:
        def put(self, item):
            kind, value = item
            payload = qr_payload(value) if kind == "qr" else {"pin": str(value)}
            if output == "json":
                print(json.dumps({"event": kind, "device": device, **payload}, ensure_ascii=False), flush=True)
            elif kind == "qr":
                print("QR URL: " + payload["qr_url"] + "\nQR PNG Base64: " + payload["qr_base64"], flush=True)
            else:
                print("PIN: " + str(value), flush=True)
    return worker(Output(), threading.Event())


def unified_session_path(root, generation):
    try:
        if str(uuid.UUID(generation)) != generation:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise BridgeError("invalid_unified_generation") from None
    return root / ("desktop-" + generation + ".dpapi")


def validate_unified_session(value):
    import base64
    try:
        if not isinstance(value, dict) or set(value) != {
            "schema", "device", "version", "accessToken", "mid", "e2eeStorage", "bridge_generation"
        }:
            raise ValueError()
        if value["schema"] not in ("desktop-shared-v1", "secondary-shared-v2") or value["device"] not in DEVICES or value["version"] != DEVICES[value["device"]]:
            raise ValueError()
        if value["schema"] == "desktop-shared-v1" and value["device"] != "DESKTOPWIN":
            raise ValueError()
        if not isinstance(value["accessToken"], str) or not 20 <= len(value["accessToken"]) <= 16384:
            raise ValueError()
        if not isinstance(value["mid"], str) or len(value["mid"]) != 33 or value["mid"][0] != "u":
            raise ValueError()
        int(value["mid"][1:], 16)
        keys = value["e2eeStorage"]
        if not isinstance(keys, dict) or not 1 <= len(keys) <= 100:
            raise ValueError()
        for name, encoded in keys.items():
            key_id = name.removeprefix("e2eeKeys:")
            if not name.startswith("e2eeKeys:") or not key_id.isdecimal() or len(key_id) > 10:
                raise ValueError()
            if not isinstance(encoded, str) or len(encoded) > 2048:
                raise ValueError()
            key = json.loads(encoded)
            if str(key["keyId"]) != key_id:
                raise ValueError()
            for part in ("privKey", "pubKey"):
                if len(base64.b64decode(key[part], validate=True)) != 32:
                    raise ValueError()
        unified_session_path(Path("."), value["bridge_generation"])
    except (ValueError, TypeError, KeyError, AttributeError):
        raise BridgeError("invalid_unified_session") from None


def commit_unified_session(root, config, protector, value):
    validate_unified_session(value)
    # Each login writes a new encrypted file. Only the final atomic config switch
    # activates it; failed/cancelled login never overwrites a previous credential.
    generation = value["bridge_generation"]
    save_secret(unified_session_path(root, generation), value, protector)
    pending = replace(config, personal_mode="native", openchat_mode="native", unified_generation=generation, login_device=value["device"])
    reset_permissions_after_login(root, pending, protector)
    config.__dict__.update(pending.__dict__)
    return {"state": "authenticated", "provider": "unified", "device": value["device"], "qr_scans": 1,
            "shared_session": True, "e2ee_ready": True, "monitor_enabled": False, "sending_enabled": config.sending_enabled}


def unified_login(root, config, protector, device=None, output="gui"):
    value = desktop_qr(config, "login-unified.ts", device or config.login_device, output)
    return commit_unified_session(root, config, protector, value)

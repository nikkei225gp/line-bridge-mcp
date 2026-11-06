"""Real local MCP stdio with synthetic LINE/QR data. No SDK or account required."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from line_bridge.bridge import Bridge
from line_bridge.config import Config
from line_bridge.platform_runtime import process_options
from line_bridge.security import create_private_directory, profile_protector, save_secret
from line_bridge.storage import Store
from line_bridge.transport import serve
from support import FakeProvider


class Client:
    def __init__(self, root, auto_start=False):
        args = [sys.executable, "-B", str(ROOT / "bin/linebridge.py"), "--data-dir", str(root), "stdio"]
        if auto_start:
            args.append("--auto-start")
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **process_options())
        self.messages = queue.Queue()
        self.counter = 0
        def reader():
            for row in iter(self.process.stdout.readline, b""):
                self.messages.put(json.loads(row))
        self.reader = threading.Thread(target=reader, daemon=True)
        self.reader.start()

    def call(self, method, params=None):
        self.counter += 1
        body = {"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params or {}}
        self.process.stdin.write(json.dumps(body).encode() + b"\n")
        self.process.stdin.flush()
        result = self.messages.get(timeout=40)
        assert result["id"] == self.counter and "error" not in result
        return result["result"]

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait(timeout=10)
        self.process.stdout.close()


def main():
    with tempfile.TemporaryDirectory(prefix="linebridge-agent-wire-") as directory:
        root = create_private_directory(Path(directory) / "profile")
        config = Config(personal_mode="native", openchat_mode="native")
        config.save(root)
        protector = profile_protector(root, config.profile_id, create=True)
        for role in ("local", "admin"):
            save_secret(root / (role + "-key.dpapi"), "synthetic-" + role + "-" * 48, protector)
        providers = {s: FakeProvider(s) for s in ("personal", "openchat")}
        bridge = Bridge(config, Store(root, config.profile_id, protector), providers)
        bridge.login.commit = lambda _: {"state": "authenticated", "device": "ANDROIDSECONDARY", "sending_enabled": True}
        stop, ready = threading.Event(), threading.Event()
        server = threading.Thread(target=serve, args=(root, protector, bridge, stop, lambda _: ready.set()), daemon=True)
        def worker(config, script, device, channel, cancel):
            channel.put(("qr", "https://example.invalid/synthetic-login"))
            channel.put(("pin", "123456"))
            cancel.wait(.2)
            return {"synthetic": True}
        clients = []
        try:
            with patch("line_bridge.login.run_qr_worker", side_effect=worker):
                server.start(); assert ready.wait(10)
                local, second_local = Client(root), Client(root)
                clients.extend([local, second_local])
                for client in clients:
                    initialized = client.call("initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "wire-test", "version": "1"}})
                    assert initialized["serverInfo"]["version"] == "0.3.0"
                names = {t["name"] for t in local.call("tools/list")["tools"]}
                assert {"send", "login_start", "login_status", "personal_chats"} <= names
                assert names == {t["name"] for t in second_local.call("tools/list")["tools"]}
                login = local.call("tools/call", {"name": "login_start", "arguments": {}})
                assert not login["isError"] and login["content"][1]["type"] == "image"
                assert login["structuredContent"]["qr_base64"]
                identity = login["structuredContent"]["login_id"]
                for _ in range(20):
                    status = local.call("tools/call", {"name": "login_status", "arguments": {"login_id": identity}})
                    if status["structuredContent"]["state"] == "authenticated":
                        break
                    time.sleep(.05)
                assert status["structuredContent"]["state"] == "authenticated"
                listing = local.call("tools/call", {"name": "openchat_chats", "arguments": {}})["structuredContent"]
                arguments = {"chat_ref": listing["chats"][0]["chat_ref"], "text": "synthetic text", "request_id": str(uuid.uuid4())}
                first = local.call("tools/call", {"name": "send", "arguments": arguments})
                second = local.call("tools/call", {"name": "send", "arguments": arguments})
                assert first["structuredContent"]["state"] == "sent_confirmed"
                assert second["structuredContent"]["state"] == "duplicate_request_not_sent"
                assert len(providers["openchat"].send_calls) == 1
                print(json.dumps({"state": "passed", "wire_stdio_clients": 2, "qr_image": True,
                                  "async_login": True, "sending_default": True, "duplicate_send_suppressed": True,
                                  "real_line_access": False, "real_post": False}))
        finally:
            for client in clients:
                client.close()
            stop.set(); server.join(10)


if __name__ == "__main__":
    main()

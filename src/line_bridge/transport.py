"""Local authenticated RPC behind separately scoped stdio MCP frontends."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from urllib.parse import urlsplit
import urllib.request

from .errors import BridgeError
from .monitor import Monitor
from .rpc import Rpc, VERSION, encoded
from .security import atomic_write, load_secret

MAX_REQUEST = 262144
MAX_RESPONSE = 12 * 1024 * 1024
_keys = {}


def endpoint(root):
    try:
        value = json.loads((root / "endpoint.json").read_text(encoding="utf-8"))
        u = urlsplit(value["url"])
        if u.scheme != "http" or u.hostname != "127.0.0.1" or u.username or u.password or u.path or u.query or u.fragment or not u.port:
            raise ValueError()
        return value
    except (KeyError, TypeError, ValueError, OSError):
        raise BridgeError("daemon_not_running") from None


def forward(root, protector, role, request, modern=False):
    if role not in ("local", "admin"):
        raise BridgeError("invalid_connection_role")
    value = endpoint(root)
    key_path = root / (role + "-key.dpapi")
    key = _keys.get(key_path)
    if key is None:
        key = _keys[key_path] = load_secret(key_path, protector)
    body = encoded(request)
    if len(body) > MAX_REQUEST:
        raise BridgeError("request_too_large")
    path = "/shutdown" if role == "admin" else "/rpc/" + role
    req = urllib.request.Request(value["url"] + path, data=body, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json",
        "X-LineBridge-Protocol": VERSION if modern else "legacy",
    })
    # Never let inherited proxy settings send loopback credentials outside the PC.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=190) as response:
            data = response.read(MAX_RESPONSE + 1)
            if len(data) > MAX_RESPONSE:
                raise BridgeError("response_too_large")
            return json.loads(data) if data else None
    except BridgeError:
        raise
    except Exception:
        # A re-initialized profile has new keys; reload on the next request.
        _keys.pop(key_path, None)
        raise BridgeError("local_daemon_unavailable") from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 16

    def __init__(self, address, handler):
        self.connections = threading.BoundedSemaphore(16)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.connections.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()

    def handle_error(self, request, client_address):
        pass


def serve(root, protector, bridge, stop=None, on_ready=None):
    stop = stop or threading.Event()
    rpc = Rpc(bridge)
    keys = {role: ("Bearer " + load_secret(root / (role + "-key.dpapi"), protector)).encode("ascii")
            for role in ("local", "admin")}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass

        def send_body(self, status, body):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            try:
                role = {"/rpc/local": "local", "/shutdown": "admin"}.get(self.path)
                if not role or self.headers.get("Origin") or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                    self.send_body(403, b'{"error":"refused"}')
                    return
                raw = self.headers.get("Authorization", "").encode("ascii", errors="replace")
                if not hmac.compare_digest(raw, keys[role]):
                    self.send_body(401, b'{"error":"unauthorized"}')
                    return
                if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
                    self.send_body(400, b'{"error":"invalid_request"}')
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_REQUEST:
                    self.send_body(413, b'{"error":"invalid_size"}')
                    return
                body = self.rfile.read(length)
                if len(body) != length:
                    self.send_body(400, b'{"error":"incomplete_request"}')
                    return
                request = json.loads(body)
                if role == "admin":
                    if request != {"operation": "stop"}:
                        self.send_body(400, b'{"error":"invalid_operation"}')
                        return
                    self.send_body(200, b'{"state":"stopping"}')
                    stop.set()
                    return
                modern = self.headers.get("X-LineBridge-Protocol") == VERSION
                response = rpc.handle(role, request, modern)
                payload = encoded(response) if response is not None else b""
                if len(payload) > MAX_RESPONSE:
                    payload = encoded({"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32603, "message": "Response too large"}})
                self.send_body(200, payload)
            except Exception:
                self.send_body(400, b'{"error":"invalid_request_details_withheld"}')

    server = LocalServer(("127.0.0.1", 0), Handler)
    server.timeout = 0.5
    monitor_thread = None
    try:
        atomic_write(root / "endpoint.json", encoded({"url": f"http://127.0.0.1:{server.server_port}", "pid": os.getpid(), "started": time.time()}))
        if bridge.config.monitor.enabled:
            monitor_thread = threading.Thread(target=Monitor(bridge).loop, args=(stop,), daemon=True)
            monitor_thread.start()
        if on_ready:
            on_ready(server)
        while not stop.is_set():
            server.handle_request()
    finally:
        stop.set()
        if monitor_thread:
            monitor_thread.join(timeout=5)
        server.server_close()
        (root / "endpoint.json").unlink(missing_ok=True)
        bridge.close()


def stdio(root, protector, role="local"):
    if role != "local":
        raise BridgeError("invalid_connection_role")
    output_lock = threading.Lock()
    modern = False
    capacity = threading.BoundedSemaphore(8)

    def relay(request, protocol):
        try:
            response = forward(root, protector, role, request, protocol)
        except Exception:
            response = {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32603, "message": "Local daemon unavailable; details withheld"}}
        try:
            if response is not None:
                with output_lock:
                    sys.stdout.buffer.write(encoded(response) + b"\n")
                    sys.stdout.buffer.flush()
        finally:
            capacity.release()

    with ThreadPoolExecutor(max_workers=4) as pool:
        while True:
            line = sys.stdin.buffer.readline(MAX_REQUEST + 1)
            if not line:
                break
            if len(line) > MAX_REQUEST:
                # Do not parse a truncated frame or retain secret-bearing malformed input.
                raise BridgeError("stdio_frame_too_large")
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError()
            except (ValueError, TypeError):
                with output_lock:
                    sys.stdout.buffer.write(encoded({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}) + b"\n")
                    sys.stdout.buffer.flush()
                continue
            if "id" not in request:
                continue
            if request.get("method") == "server/discover":
                modern = True
            capacity.acquire()
            pool.submit(relay, request, modern)

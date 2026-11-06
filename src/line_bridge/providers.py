"""Explicit read allowlists and scoped sends. No upstream sync threads."""
from __future__ import annotations

import json
from pathlib import Path
import queue
import subprocess
import threading
import uuid

from .config import Config
from .platform_runtime import process_options, worker_environment
from .errors import BridgeError
from .security import Protector, load_secret

PERSONAL_READS = {"chats", "chat", "messages", "find_contact", "groups", "contact_chats", "members"}


class DisabledProvider:
    generation = "disabled"
    mode = "disabled"

    def read(self, operation, arguments):
        raise BridgeError("provider_not_configured")

    def image(self, arguments):
        raise BridgeError("provider_not_configured")

    def close(self):
        pass


class NativeOpenChat:
    mode = "native"
    script_name = "openchat.ts"
    network_hosts = "legy.line-apps.com:443"
    max_response_bytes = 4 * 1024 * 1024
    auth_required_code = "openchat_auth_required"
    unavailable_code = "openchat_bridge_unavailable"
    read_failed_code = "openchat_read_failed"

    def __init__(self, root: Path, config: Config, protector: Protector):
        self.root, self.config, self.protector = root, config, protector
        self.lock = threading.RLock()
        self.process = None
        self.output = None

    def credentials(self):
        raise BridgeError("unified_auth_required")

    @property
    def generation(self):
        return self.credentials()["bridge_generation"]

    def _start(self):
        if self.process and self.process.poll() is None:
            return
        if self.process:
            self.close()
        root = Path(self.config.linejs_directory).resolve()
        script = Path(__file__).parent / "js" / self.script_name
        command = [self.config.deno_executable, "run", "--cached-only", "--frozen", "--no-check", "--no-prompt",
                   "--config", str(root / "linebridge-deno.json"), f"--allow-read={root},{script.parent}",
                   "--allow-net=" + self.network_hosts,
                   "--allow-env=NODE_DEBUG,NODE_OPTIONS,DEBUG,NODE_ENV,Q_DEBUG,Q_LONG_STACKS,WS_NO_BUFFER_UTIL,WS_NO_UTF_8_VALIDATE",
                   str(script), str(root)]
        env = worker_environment()
        if self.config.deno_cache_directory:
            env["DENO_DIR"] = self.config.deno_cache_directory
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, env=env, **process_options())
        self.output = queue.Queue(maxsize=4)
        process, output = self.process, self.output

        def reader():
            while True:
                line = process.stdout.readline(self.max_response_bytes + 1)
                if not line or len(line) > self.max_response_bytes:
                    output.put(None)
                    return
                output.put(line)
        threading.Thread(target=reader, daemon=True).start()

    def _call(self, action, arguments):
        with self.lock:
            attempted = action in ("send", "personal_send")
            try:
                # Missing/invalid credentials must not launch a runtime or a QR login.
                saved = self.credentials()
                if attempted and "expected_generation" in arguments and arguments["expected_generation"] != saved["bridge_generation"]:
                    return {"state": "not_sent_destination_changed"}
                self._start()
                identity = str(uuid.uuid4())
                request = {"id": identity, "action": action, "session": saved, **arguments}
                body = json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"
                if len(body) > 262144:
                    raise BridgeError("request_too_large")
                self.process.stdin.write(body)
                self.process.stdin.flush()
                line = self.output.get(timeout=150)
                if not line:
                    raise BridgeError(self.unavailable_code)
                result = json.loads(line)
                if not isinstance(result, dict) or result.get("id") != identity:
                    raise BridgeError("invalid_bridge_response")
                result.pop("id", None)
                if result.get("state") == "failed":
                    codes = {"auth_required": self.auth_required_code,
                             "network_unavailable": "upstream_network_unavailable",
                             "request_timeout": "upstream_request_timeout",
                             "upstream_unavailable": "upstream_temporarily_unavailable",
                             "rate_limited": "upstream_rate_limited",
                             "media_unavailable": "image_unavailable",
                             "media_too_large": "image_too_large"}
                    raise BridgeError(codes.get(result.get("error_code"), self.read_failed_code))
                return result
            except Exception as error:
                self.close()
                if attempted:
                    return {"state": "send_outcome_unknown_no_retry"}
                if isinstance(error, BridgeError):
                    raise
                if isinstance(error, queue.Empty):
                    raise BridgeError("upstream_request_timeout") from None
                raise BridgeError(self.unavailable_code) from None

    def read(self, operation, arguments):
        if operation not in ("list", "messages", "thread_messages", "members"):
            raise BridgeError("operation_not_allowed")
        return self._call(operation, arguments)

    def poll(self, arguments):
        return self._call("poll", arguments)

    def send_once(self, arguments):
        return self._call("send", arguments)

    def unsend(self, arguments):
        return self._call("unsend", arguments)

    def destroy(self, arguments):
        return self._call("destroy", arguments)

    def close(self):
        with self.lock:
            if self.process:
                if self.process.poll() is None:
                    self.process.kill()
                self.process.wait(timeout=10)
                for pipe in (self.process.stdin, self.process.stdout):
                    pipe.close()
                self.process = None
            # Late output belongs to the retired process, never the next request.
            self.output = None


class NativeDesktop(NativeOpenChat):
    """One process/session for Talk reads and scoped Square operations. No refresh."""
    script_name = "desktop.ts"
    network_hosts = "legy.line-apps.com:443,gf.line.naver.jp:443,obs.line-apps.com:443"
    max_response_bytes = 9 * 1024 * 1024
    auth_required_code = "unified_auth_required"
    unavailable_code = "unified_bridge_unavailable"
    read_failed_code = "unified_read_failed"

    cached = None

    def credentials(self):
        # The session file only changes through a login, which builds new providers.
        if self.cached is not None and self.cached["bridge_generation"] == self.config.unified_generation:
            return self.cached
        from .authentication import unified_session_path, validate_unified_session
        try:
            value = load_secret(unified_session_path(self.root, self.config.unified_generation), self.protector)
        except OSError:
            raise BridgeError("unified_auth_required") from None
        validate_unified_session(value)
        if value["bridge_generation"] != self.config.unified_generation:
            raise BridgeError("unified_session_generation_mismatch")
        self.cached = value
        return value

    def image(self, arguments):
        return self._call("image", arguments)


class NativeDesktopPersonal:
    mode = "native"

    def __init__(self, desktop):
        self.desktop = desktop

    @property
    def generation(self):
        return self.desktop.generation

    def read(self, operation, arguments):
        if operation not in PERSONAL_READS:
            raise BridgeError("operation_not_allowed")
        return self.desktop._call("personal", {"operation": operation, "arguments": arguments})["result"]

    def send_once(self, arguments):
        return self.desktop._call("personal_send", arguments)

    def unsend(self, arguments):
        return self.desktop._call("personal_unsend", arguments)

    def image(self, arguments):
        return self.desktop.image(arguments)

    def close(self):
        self.desktop.close()


def make_providers(root: Path, config: Config, protector: Protector):
    config.validate()
    if config.unified_generation:
        desktop = NativeDesktop(root, config, protector)
        return {"personal": NativeDesktopPersonal(desktop), "openchat": desktop}
    if config.personal_mode != "disabled" or config.openchat_mode != "disabled":
        raise BridgeError("unified_auth_required")
    return {"personal": DisabledProvider(), "openchat": DisabledProvider()}

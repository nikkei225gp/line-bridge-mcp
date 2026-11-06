"""Nonblocking, explicit login for a local AI host. Temporary QR/PIN stay in memory."""
from copy import deepcopy
import threading
import uuid

from .authentication import qr_payload, run_qr_worker
from .devices import normalize_device
from .errors import BridgeError


class Login:
    def __init__(self, config, commit):
        self.config, self.commit = config, commit
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.ready = threading.Event()
        self.thread = None
        self.value = None

    def start(self, device="ANDROIDSECONDARY", replace=False):
        device = normalize_device(device)
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise BridgeError("login_already_in_progress")
            if self.config.unified_generation and not replace:
                raise BridgeError("existing_session_requires_explicit_replace")
            self.cancel_event = threading.Event()
            self.ready = threading.Event()
            self.value = {"login_id": str(uuid.uuid4()), "state": "starting", "device": device}
            snapshot = deepcopy(self.config)
            self.thread = threading.Thread(target=self._run, args=(snapshot, device), daemon=True)
            self.thread.start()
        self.ready.wait(5)
        return self.status(self.value["login_id"])

    def _run(self, config, device):
        owner = self
        class Channel:
            def put(self, item):
                kind, value = item
                if owner.cancel_event.is_set():
                    return
                with owner.lock:
                    if kind == "qr":
                        owner.value.update(state="waiting_for_qr", **qr_payload(value))
                    elif kind == "pin":
                        owner.value.update(state="waiting_for_pin", pin=str(value))
                    owner.ready.set()
        try:
            saved = run_qr_worker(config, "login-unified.ts", device, Channel(), self.cancel_event)
            if saved is not None and not self.cancel_event.is_set():
                result = self.commit(saved)
                with self.lock:
                    self.value = {"login_id": self.value["login_id"], **result}
            else:
                with self.lock:
                    self.value = {"login_id": self.value["login_id"], "state": "cancelled", "device": device}
        except Exception:
            with self.lock:
                self.value = {"login_id": self.value["login_id"], "state": "cancelled" if self.cancel_event.is_set() else "failed", "device": device}
                if not self.cancel_event.is_set():
                    self.value["error_code"] = "login_failed_no_automatic_retry"
        finally:
            self.ready.set()

    def status(self, login_id):
        with self.lock:
            if not self.value or self.value["login_id"] != login_id:
                raise BridgeError("login_not_found")
            return deepcopy(self.value)

    def cancel(self, login_id):
        with self.lock:
            current = self.status(login_id)
            if current["state"] in ("authenticated", "failed", "cancelled") or not self.thread or not self.thread.is_alive():
                return current
            self.cancel_event.set()
            self.value = {"login_id": login_id, "state": "cancelling", "device": current["device"]}
            return deepcopy(self.value)

    def close(self):
        self.cancel_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=12)

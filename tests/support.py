import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile

from line_bridge.bridge import Bridge
from line_bridge.config import Config
from line_bridge.storage import Store


class TestProtector:
    """Ephemeral authenticated test cipher. Never imported by production code."""
    def __init__(self):
        self.key = os.urandom(32)

    def seal(self, value):
        nonce = os.urandom(16)
        stream = b"".join(hashlib.sha256(self.key + nonce + i.to_bytes(4, "big")).digest()
                          for i in range((len(value) + 31) // 32))
        body = bytes(a ^ b for a, b in zip(value, stream))
        return nonce + hmac.new(self.key, nonce + body, hashlib.sha256).digest() + body

    def open(self, value):
        nonce, tag, body = value[:16], value[16:48], value[48:]
        assert hmac.compare_digest(tag, hmac.new(self.key, nonce + body, hashlib.sha256).digest())
        stream = b"".join(hashlib.sha256(self.key + nonce + i.to_bytes(4, "big")).digest()
                          for i in range((len(body) + 31) // 32))
        return bytes(a ^ b for a, b in zip(body, stream))


class FakeProvider:
    mode = "native"

    def __init__(self, source):
        self.generation = source + "-generation"
        self.source = source
        self.calls = []
        self.send_calls = []
        self.rows = [{"id": "m1", "text": "fixture text", "created_ms": 1000, "type": 0, "from_me": False}]
        self.membership_truncated = False
        self.rename = False
        self.fail = False
        self.send_fail = False
        self.checkpoint = {"syncToken": "fixture-progress", "baseline": False, "stalled": False}
        self.send_result = {"state": "sent_confirmed"}
        self.image_bytes = b"\x89PNG\r\n\x1a\nfixture"
        self.members_result = None
        self.removals = []
        self.remove_result = None

    def read(self, operation, args):
        self.calls.append((operation, dict(args)))
        if self.fail:
            raise RuntimeError("Raw upstream error MUST NOT escape")
        if operation == "profile":
            return {"mid": "fixture-account", "displayName": "Fixture user", "e2ee_ready": True, "last_error": "private detail"}
        if operation == "chats":
            return [{"chat_id": "normal-room", "name": "Fixture normal room", "unread": 2}]
        if operation == "list":
            return {"squares": [{"id": "square1", "name": "Fixture community"}],
                    "chats": [{"id": "open-room", "squareId": "square1", "name": "Changed" if self.rename else "Fixture open room"}],
                    "truncated": self.membership_truncated}
        if operation == "messages":
            if self.source == "openchat":
                return {"messages": self.rows, "latestGuaranteed": False, "truncated": False}
            if args.get("before_message_id"):
                return []
            return self.rows
        if operation == "thread_messages":
            return {"thread_exists": True, "messages": self.rows, "truncated": False}
        if operation == "context":
            return {"target_index": 0, "messages": self.rows}
        if operation == "members":
            if self.members_result is not None:
                return self.members_result
            if self.source == "openchat":
                return {"members": [{"name": "Fixture admin", "role": "admin", "is_me": True, "mid": "private"}], "truncated": False}
            return {"members": [{"name": "Fixture user", "is_me": True, "is_creator": False, "mid": "private"},
                                {"name": "Fixture friend", "is_me": False, "is_creator": True}], "total": 2, "truncated": False}
        return {}

    def poll(self, args):
        self.calls.append(("poll", dict(args)))
        if self.fail:
            raise RuntimeError("poll failed")
        return {"messages": self.rows, "checkpoint": self.checkpoint}

    def image(self, args):
        import base64
        self.calls.append(("image", dict(args)))
        return {"image": base64.b64encode(self.image_bytes).decode()}

    def send_once(self, args):
        self.send_calls.append(dict(args))
        if self.rename:
            return {"state": "not_sent_destination_changed"}
        if self.fail or self.send_fail:
            raise TimeoutError("outcome unknown")
        return self.send_result

    def unsend(self, args):
        self.removals.append(("unsend", dict(args)))
        return self.remove_result or {"state": "unsent"}

    def destroy(self, args):
        self.removals.append(("destroy", dict(args)))
        return self.remove_result or {"state": "deleted"}

    def close(self):
        pass


def environment():
    temp = tempfile.TemporaryDirectory(prefix="linebridge-offline-")
    root = Path(temp.name)
    config = Config(personal_mode="native", openchat_mode="native", sending_enabled=False)
    protector = TestProtector()
    store = Store(root, config.profile_id, protector)
    providers = {source: FakeProvider(source) for source in ("personal", "openchat")}
    bridge = Bridge(config, store, providers)
    return temp, root, config, protector, store, providers, bridge

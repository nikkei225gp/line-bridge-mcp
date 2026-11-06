from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid

from .errors import BridgeError


def request_uuid(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise BridgeError("request_id_must_be_uuid") from None


class Sending:
    """Single-shot text sending with a client-supplied UUID for idempotency.

    There is no per-message approval step and no destination re-verification.
    A durable reservation commits before any network activity, so an unknown
    outcome is never retried with a new UUID.
    """

    def __init__(self, store, config, providers, clock=time.time):
        self.store, self.config, self.providers, self.clock = store, config, providers, clock
        self.lock = threading.Lock()

    def guard(self, source=None):
        if not self.config.sending_enabled:
            raise BridgeError("sending_disabled")
        if source is not None:
            provider = self.providers.get(source)
            if provider is None or provider.mode != "native" or not callable(getattr(provider, "send_once", None)):
                raise BridgeError("sending_disabled")
            return provider

    def next_sequence(self, db):
        raw = db.execute("SELECT value FROM meta WHERE key='send_sequence'").fetchone()
        # The counter only supplies LINE's increasing request sequence.
        sequence = self.store.open(raw[0])["next"] if raw else 1
        if not isinstance(sequence, int) or not 1 <= sequence <= 2147483647:
            raise BridgeError("send_sequence_exhausted")
        self.store.put_meta("send_sequence", {"next": sequence + 1}, db)
        return sequence

    def remove(self, message_ref, action):
        """Unsend our own saved message, or delete any OpenChat message as a co-admin. LINE decides; no retry."""
        self.guard()
        with self.store.db() as db:
            row = db.execute("SELECT source,chat_ref,payload FROM cache WHERE message_ref=?", (message_ref,)).fetchone()
        if not row:
            raise BridgeError("message_not_retrieved")
        source, chat_ref, payload = row
        message = self.store.open(payload)
        if action == "unsend" and not message.get("from_me"):
            raise BridgeError("not_own_message")
        if action == "destroy" and source != "openchat":
            raise BridgeError("delete_is_openchat_only")
        provider = self.guard(source)
        generation = provider.generation
        chat, _ = self.store.resolve(chat_ref, source, generation)

        def native(ref):
            value, _ = self.store.resolve(ref, source, generation, "message")
            parent, result = value.split("\0", 1)
            if parent != chat_ref:
                raise BridgeError("message_belongs_to_different_chat")
            return result
        arguments = {"chat_id": chat, "message_id": native(message_ref)}
        if message.get("thread_root_ref"):
            arguments["thread_message_id"] = native(message["thread_root_ref"])
        if source == "personal":
            with self.lock, self.store.db(immediate=True, durable=True) as db:
                arguments["sequence"] = self.next_sequence(db)
        result = provider.unsend(arguments) if action == "unsend" else provider.destroy(arguments)
        state = result.get("state")
        done = "unsent" if action == "unsend" else "deleted"
        if state != done:
            return {"message_ref": message_ref, "state": "not_" + done, "reason": str(result.get("reason") or "upstream_failed")}
        self.store.archive([{**message, "unsent": True}])
        return {"message_ref": message_ref, "state": done}

    def send(self, chat_ref, text, client_request_id, thread_message_ref="", reply_to_message_ref=""):
        self.guard()
        request_id = request_uuid(client_request_id)
        try:
            valid_text = isinstance(text, str) and bool(text) and len(text.encode("utf-16-le")) // 2 <= 5000
        except UnicodeError:
            valid_text = False
        if not valid_text:
            raise BridgeError("text_must_be_1_to_5000_utf16_units")
        source = self.store.reference_source(chat_ref)
        provider = self.guard(source)
        generation = provider.generation
        if source not in ("personal", "openchat"):
            raise BridgeError("invalid_reference_source")
        native, chat_name = self.store.resolve(chat_ref, source, generation)
        if source == "personal" and not native.startswith(("u", "c")):
            raise BridgeError("unsupported_send_destination")
        payload = {"source": source, "native": native, "chat_name": chat_name, "text": text}
        if thread_message_ref:
            # A thread reply is posted under one OpenChat message of this same chat.
            if source != "openchat":
                raise BridgeError("threads_are_openchat_only")
            value, _ = self.store.resolve(thread_message_ref, source, generation, "message")
            parent, payload["thread_native"] = value.split("\0", 1)
            if parent != chat_ref:
                raise BridgeError("message_belongs_to_different_chat")
        if reply_to_message_ref:
            # A quote reply answers one message of this same chat; it is not a thread reply.
            if thread_message_ref:
                raise BridgeError("reply_and_thread_are_exclusive")
            value, _ = self.store.resolve(reply_to_message_ref, source, generation, "message")
            parent, payload["reply_native"] = value.split("\0", 1)
            if parent != chat_ref:
                raise BridgeError("message_belongs_to_different_chat")
        digest = hashlib.sha256(json.dumps([chat_ref, generation, payload], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        with self.lock:
            now = self.clock()
            with self.store.db(immediate=True, durable=True) as db:
                old = db.execute("SELECT state,digest FROM sends WHERE request_id=?", (request_id,)).fetchone()
                if old:
                    if old["digest"] != digest:
                        raise BridgeError("uuid_already_bound_to_different_content")
                    return {"request_id": request_id, "state": "duplicate_request_not_sent", "previous_state": old["state"]}
                sequence = self.next_sequence(db)
                db.execute("INSERT INTO sends(request_id,chat_ref,body,digest,generation,expires,state,created,sequence) VALUES(?,?,?,?,?,?,?,?,?)",
                           (request_id, chat_ref, self.store.seal(payload), digest, generation, now, "reserved_outcome_unknown_no_retry", now, sequence))
            # The synchronous reservation commits before any network activity.
            result = {}
            try:
                arguments = {"chat_id": payload["native"], "text": payload["text"],
                             "expected_generation": generation, "sequence": sequence}
                if "thread_native" in payload:
                    arguments["thread_message_id"] = payload["thread_native"]
                if "reply_native" in payload:
                    arguments["reply_to"] = payload["reply_native"]
                result = provider.send_once(arguments)
                state = result.get("state")
                if state not in ("sent_confirmed", "not_sent_destination_changed", "not_sent_e2ee_unavailable",
                                 "not_sent_thread_unavailable", "send_outcome_unknown_no_retry"):
                    state = "send_outcome_unknown_no_retry"
            except Exception:
                state = "send_outcome_unknown_no_retry"
            archived = None
            message_id = result.get("message_id") if isinstance(result, dict) else None
            if (state == "sent_confirmed" and isinstance(message_id, str) and
                    re.fullmatch(r"[1-9][0-9]{0,18}", message_id) and int(message_id) <= 9223372036854775807):
                created_ms = result.get("created_ms")
                if type(created_ms) is not int or not 0 < created_ms <= 9007199254740991:
                    created_ms = 0
                message_ref = self.store.reference(source, chat_ref + "\0" + message_id, "", generation, "message")
                archived = {"source": source, "chat_ref": chat_ref, "chat_name": payload["chat_name"],
                            "message_ref": message_ref, "sender": "", "created_ms": created_ms,
                            "kind": "text", "native_type": "NONE", "availability": "complete", "reason": None,
                            "plain_text": payload["text"], "content": None,
                            "text_truncated": False, "from_me": True, "unsent": False}
                if thread_message_ref:
                    archived["thread_root_ref"] = thread_message_ref
            with self.store.db() as db:
                db.execute("UPDATE sends SET state=?,result=? WHERE request_id=?", (state, self.store.seal({"state": state}), request_id))
                if archived is not None:
                    self.store.archive([archived], db)
            answer = {"state": state, "request_id": request_id}
            if state == "sent_confirmed" and "reply_native" in payload:
                # Whether LINE kept the quote is reported as echoed, never assumed.
                answer["reply_confirmed"] = result.get("reply_confirmed") is True
            return answer

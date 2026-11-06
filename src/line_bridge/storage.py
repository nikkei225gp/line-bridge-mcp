"""Encrypted payloads, opaque references, atomic send reservations and archive."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

from .errors import BridgeError
from .security import Protector


class Store:
    def __init__(self, root: Path, profile_id: str, protector: Protector):
        self.path = root / "bridge.sqlite"
        self.profile_id = profile_id
        self.protector = protector
        self.checked = False
        with self.db() as db:
            # WAL with synchronous=NORMAL avoids an fsync per commit; send
            # reservations opt back into FULL through db(durable=True).
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS refs(
                  ref TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL,
                  native BLOB NOT NULL, identity TEXT NOT NULL UNIQUE, label BLOB NOT NULL,
                  generation TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sends(
                  request_id TEXT PRIMARY KEY, chat_ref TEXT NOT NULL, body BLOB NOT NULL,
                  digest TEXT NOT NULL, generation TEXT NOT NULL, expires REAL NOT NULL,
                  state TEXT NOT NULL, result BLOB, created REAL NOT NULL, sequence INTEGER);
                CREATE TABLE IF NOT EXISTS cache(
                  source TEXT NOT NULL, chat_ref TEXT NOT NULL, message_ref TEXT NOT NULL,
                  created_ms INTEGER NOT NULL, stored REAL NOT NULL, payload BLOB NOT NULL,
                  PRIMARY KEY(source,chat_ref,message_ref));
                CREATE TABLE IF NOT EXISTS notify_pending(
                  chat_ref TEXT NOT NULL, message_ref TEXT NOT NULL, observed REAL NOT NULL,
                  tier TEXT NOT NULL, payload BLOB NOT NULL, PRIMARY KEY(chat_ref,message_ref));
                CREATE INDEX IF NOT EXISTS cache_chat_time ON cache(chat_ref,created_ms DESC,message_ref DESC);
                CREATE INDEX IF NOT EXISTS cache_time ON cache(created_ms DESC,message_ref DESC);
            """)

    @contextmanager
    def db(self, immediate=False, durable=False):
        if os.name == "posix" and not self.checked:
            # SQLite inherits this file's owner-only mode for its journal files.
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.close(fd)
            except FileExistsError:
                from .security import verify_private_file
                verify_private_file(self.path)
        self.checked = True
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA synchronous=" + ("FULL" if durable else "NORMAL"))
        try:
            if immediate:
                db.execute("BEGIN IMMEDIATE")
            with db:
                yield db
        finally:
            db.close()

    def seal(self, value) -> bytes:
        return self.protector.seal(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def open(self, value):
        return json.loads(self.protector.open(bytes(value)))

    def get_meta(self, key, default=None):
        with self.db() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return self.open(row[0]) if row else default

    def put_meta(self, key, value, db=None):
        if db is None:
            with self.db() as connection:
                self.put_meta(key, value, connection)
            return
        db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, self.seal(value)))

    def reference(self, source: str, native: str, label: str, generation: str, kind="chat") -> str:
        return self.references([(source, native, label, kind)], generation)[0]

    def references(self, items, generation: str) -> list[str]:
        """Map (source, native, label, kind) items to opaque refs in one transaction."""
        import hashlib
        identities = []
        for source, native, _, kind in items:
            if source not in ("personal", "openchat") or kind not in ("chat", "message", "contact", "community"):
                raise BridgeError("invalid_reference_source")
            identities.append(hashlib.sha256(self.seal_identity(source, native, generation, kind)).hexdigest())
        if not items:
            return []
        with self.db(immediate=True) as db:
            known = {}
            unique = sorted(set(identities))
            for start in range(0, len(unique), 500):
                chunk = unique[start:start + 500]
                known.update((row[0], (row[1], row[2])) for row in db.execute(
                    "SELECT identity,ref,label FROM refs WHERE identity IN (" + ",".join("?" * len(chunk)) + ")", chunk))
            result = []
            for (source, native, label, kind), identity in zip(items, identities):
                if identity in known:
                    ref, sealed = known[identity]
                    # Labels are rewritten only on change (message refs keep "").
                    if self.open(sealed) != label:
                        sealed = self.seal(label)
                        db.execute("UPDATE refs SET label=? WHERE ref=?", (sealed, ref))
                        known[identity] = (ref, sealed)
                else:
                    ref = f"{kind}:v1:{self.profile_id}:{source}:" + secrets.token_urlsafe(18)
                    sealed = self.seal(label)
                    db.execute("INSERT INTO refs VALUES(?,?,?,?,?,?,?)", (ref, source, kind, self.seal(native), identity, sealed, generation))
                    known[identity] = (ref, sealed)
                result.append(ref)
            return result

    def is_own_message(self, source, chat_ref, native_id):
        """Whether the archived message with this LINE id in chat_ref was sent by the account."""
        import hashlib
        with self.db() as db:
            row = db.execute("SELECT generation FROM refs WHERE ref=?", (chat_ref,)).fetchone()
            if not row:
                return False
            identity = hashlib.sha256(self.seal_identity(source, chat_ref + "\0" + str(native_id), row[0], "message")).hexdigest()
            row = db.execute("SELECT c.payload FROM refs r JOIN cache c ON c.message_ref=r.ref WHERE r.identity=?", (identity,)).fetchone()
        return bool(row) and self.open(row[0]).get("from_me") is True

    def seal_identity(self, source, native, generation, kind):
        return json.dumps([self.profile_id, source, native, generation, kind], separators=(",", ":")).encode()

    def resolve(self, ref: str, source: str, generation: str, kind="chat") -> tuple[str, str]:
        if not isinstance(ref, str):
            raise BridgeError("invalid_reference")
        with self.db() as db:
            row = db.execute("SELECT * FROM refs WHERE ref=?", (ref,)).fetchone()
        if not row or row["source"] != source or row["kind"] != kind or row["generation"] != generation:
            raise BridgeError("unknown_or_stale_reference")
        return self.open(row["native"]), self.open(row["label"])

    def reference_source(self, ref: str) -> str:
        with self.db() as db:
            row = db.execute("SELECT source FROM refs WHERE ref=? AND kind='chat'", (ref,)).fetchone()
        if not row:
            raise BridgeError("unknown_chat_reference")
        return row[0]

    def counts(self):
        with self.db() as db:
            return {"references": db.execute("SELECT COUNT(*) FROM refs").fetchone()[0],
                    "archived_messages": db.execute("SELECT COUNT(*) FROM cache").fetchone()[0],
                    "latest_message_created_ms": db.execute("SELECT MAX(created_ms) FROM cache").fetchone()[0] or 0,
                    "send_states": {r[0]: r[1] for r in db.execute("SELECT state,COUNT(*) FROM sends GROUP BY state")}}

    def archive(self, messages, db=None):
        if db is None:
            with self.db() as connection:
                self.archive(messages, connection)
            return
        now = time.time()
        db.executemany("INSERT INTO cache VALUES(?,?,?,?,?,?) ON CONFLICT(source,chat_ref,message_ref) DO UPDATE SET "
                       "created_ms=excluded.created_ms,payload=excluded.payload,stored=excluded.stored",
                       [(m["source"], m["chat_ref"], m["message_ref"], m["created_ms"], now, self.seal(m)) for m in messages])

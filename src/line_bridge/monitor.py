"""Periodic collector into the permanent archive, feeding the notifier. No worker exists when disabled."""
from __future__ import annotations

import hashlib
import time

from .errors import BridgeError
from .notify import Notifier


class Monitor:
    def __init__(self, bridge):
        self.bridge = bridge
        self.store, self.config = bridge.store, bridge.config
        self.notifier = Notifier(self.store, self.config, self.store.path.parent, self.store.protector)

    def collect(self, source, ref, last_id=None):
        provider = self.bridge.providers[source]
        generation = provider.generation
        native, name = self.store.resolve(ref, source, generation)
        key = "checkpoint:" + hashlib.sha256(ref.encode()).hexdigest()
        saved = self.store.get_meta(key)
        if saved and saved.get("generation") != generation:
            saved = None
        if source == "openchat":
            baseline = not saved or saved.get("checkpoint", {}).get("baseline", True)
            result = provider.poll({"chat_id": native, "checkpoint": saved.get("checkpoint") if saved else None})
            raw = fresh = result["messages"]
            checkpoint = {"generation": generation, "checkpoint": result["checkpoint"]}
            stalled = bool(result["checkpoint"].get("stalled"))
            baselining = bool(result["checkpoint"].get("baseline"))
            possible_gap = stalled
        else:
            old_ids = set(saved.get("ids", [])) if saved else set()
            # The recent-chats listing already carries each chat's last message id.
            if saved and saved.get("archive_seeded") and last_id and last_id in old_ids:
                return {"baselining": False, "stalled": False, "archived": 0, "possible_gap": False, "new": 0, "skipped": True}
            baseline = saved is None
            count = 200
            raw = provider.read("messages", {"chat_id": native, "count": count})
            latest_ids = [str(m["id"]) for m in raw if m.get("id")]
            checkpoint = {"generation": generation, "ids": latest_ids, "archive_seeded": True}
            fresh = [m for m in raw if str(m.get("id")) not in old_ids]
            if saved and saved.get("archive_seeded"):
                raw = fresh
            stalled, baselining = False, False
            possible_gap = bool(saved) and len(latest_ids) >= count and not old_ids & set(latest_ids)
        archived = self.bridge.normalize_messages(source, ref, name, raw, generation)
        items = []
        if not baseline and self.config.notify.enabled:
            by_id = {str(r.get("id")): (r, m) for r, m in zip(raw, archived)}
            incoming = [by_id[str(r.get("id"))] for r in fresh if str(r.get("id")) in by_id
                        and not r.get("from_me") and not r.get("unsent")]
            items = self.notifier.prepare(ref, source == "personal" and str(native).startswith("u"), incoming)
        with self.store.db(immediate=True) as db:
            self.store.archive(archived, db)
            new = self.notifier.insert(items, db)
            # Archive, notification queue and checkpoint commit together. Failed pages never advance it.
            self.store.put_meta(key, checkpoint, db)
        return {"baselining": baselining or baseline, "stalled": stalled, "archived": len(archived),
                "possible_gap": possible_gap, "new": new, "skipped": False}

    def cycle(self):
        with self.bridge.lock:
            return self._cycle()

    def _cycle(self):
        if not self.config.monitor.enabled:
            raise BridgeError("monitoring_disabled")
        status = {"state": "running", "checked_at_unix": time.time(), "interval_seconds": self.config.monitor.interval_seconds,
                  "coverage_complete": False, "failed_chats": 0, "baselining_chats": 0, "stalled_chats": 0,
                  "possible_gap_chats": 0, "archived_this_cycle": 0, "unchanged_chats_skipped": 0, "new_for_notify": 0}
        targets = list(self.config.monitor.targets)
        last_ids = {}
        discover = self.config.monitor.scope == "all" and self.config.monitor.include_future_chats
        if self.config.personal_mode != "disabled" and (targets or discover):
            try:
                rows = self.bridge.personal_chat_rows(100)
                last_ids = {ref: str(raw.get("last_message_id") or "") for ref, raw in rows}
                if discover:
                    targets += [ref for ref, _ in rows]
            except Exception:
                status["discovery_failed"] = True
        if discover:
            try:
                if self.config.personal_mode != "disabled":
                    targets += [c["chat_ref"] for c in self.bridge._personal_related("groups", {})["result"]]
                if self.config.openchat_mode != "disabled":
                    listing = self.bridge.openchat_listing()
                    if listing["truncated"]:
                        raise BridgeError("membership_enumeration_incomplete")
                    targets += [c["chat_ref"] for c in listing["chats"]]
            except Exception:
                status["discovery_failed"] = True
        for ref in sorted(set(targets)):
            try:
                source = self.store.reference_source(ref)
                result = self.collect(source, ref, last_ids.get(ref))
                status["archived_this_cycle"] += result["archived"]
                status["baselining_chats"] += int(result["baselining"])
                status["stalled_chats"] += int(result["stalled"])
                status["possible_gap_chats"] += int(result["possible_gap"])
                status["unchanged_chats_skipped"] += int(result["skipped"])
                status["new_for_notify"] += result["new"]
            except Exception:
                status["failed_chats"] += 1
        status["target_count"] = len(set(targets))
        if status["failed_chats"] or status.get("discovery_failed"):
            status["state"] = "partial_failure"
        if self.config.notify.enabled:
            try:
                status["notify"] = self.notifier.evaluate()
            except Exception:
                status["notify"] = {"state": "failed"}
        self.store.put_meta("worker_status", status)
        return status

    def loop(self, stop):
        while not stop.is_set():
            started = time.monotonic()
            try:
                self.cycle()
            except Exception:
                self.store.put_meta("worker_status", {"state": "failed", "checked_at_unix": time.time(), "details_withheld": True})
            stop.wait(max(1, self.config.monitor.interval_seconds - (time.monotonic() - started)))

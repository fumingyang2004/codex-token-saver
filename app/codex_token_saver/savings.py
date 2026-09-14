"""Best-effort, session-scoped accounting. Never changes an optimization decision."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import hashlib
import json
import logging
import os
import re
import sqlite3
import time

from .state import safe_path

METHOD = "approximate: ceil(UTF-8 bytes / 4), RTK-compatible; not a model tokenizer"
COMPONENTS = ("rtk", "cce")


def count_tokens(text: str) -> int:
    return (len(text.encode("utf-8")) + 3) // 4


def session_id(explicit=None):
    value = explicit if explicit is not None else os.environ.get("CODEX_THREAD_ID")
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value) else None


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def best_effort(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except Exception as exc:
        # No source text, prompts or command arguments in diagnostics.
        logging.getLogger(__name__).debug("Savings telemetry skipped: %s", type(exc).__name__)
        return None


class SavingsLedger:
    def __init__(self, store, sid):
        self.store = store
        self.session_id = session_id(sid) if sid is not None else None
        self.path = store.directory / "savings.sqlite3"

    def _append(self, component, event_id, kind, reason, *, before=None, after=None,
                delta=None, source_id=None, metadata=None, method=None, historical=False):
        if not self.session_id or component not in COMPONENTS or (not historical and not self.store.active(component)):
            return False
        if not isinstance(event_id, str) or not event_id or not reason:
            return False
        event = dict(event_id=event_id, session_id=self.session_id, component=component,
                     kind=kind, before_tokens=before, after_tokens=after, delta_tokens=delta,
                     timestamp=time.time(), reason=reason, source_id=source_id,
                     counting_method=method, metadata=metadata or {})
        encoded = json.dumps(event, ensure_ascii=False, allow_nan=False)
        safe_path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=.05)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS savings_events "
                       "(session_id TEXT, component TEXT, event_id TEXT, event_json TEXT NOT NULL, "
                       "PRIMARY KEY(session_id,component,event_id))")
            return db.execute("INSERT OR IGNORE INTO savings_events VALUES (?,?,?,?)",
                              (self.session_id, component, event_id, encoded)).rowcount == 1

    def record_observed(self, component, event_id, before, after, reason, *, source_id=None, metadata=None):
        def record():
            if not self.store.active(component):
                return False
            a, b = count_tokens(before), count_tokens(after)
            info = {**(metadata or {}), "before_sha256": digest(before), "after_sha256": digest(after),
                    "before_utf8_bytes": len(before.encode("utf-8")), "after_utf8_bytes": len(after.encode("utf-8"))}
            return self._append(component, event_id, "observed", reason, before=a, after=b,
                                delta=a-b, source_id=source_id, metadata=info, method=METHOD)
        return best_effort(record)

    def record_invocation(self, component, event_id, reason, *, source_id=None, metadata=None):
        return best_effort(self._append, component, event_id, "invocation", reason,
                           source_id=source_id, metadata=metadata)

    def events(self):
        if not self.session_id or not self.path.is_file():
            return []
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.05)) as db:
            rows = db.execute("SELECT event_json FROM savings_events WHERE session_id=? ORDER BY component,event_id",
                              (self.session_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def summary(self):
        return summarize(self.events())


def summarize(events):
    unique = {(e["session_id"], e["component"], e["event_id"]): e for e in events}
    accepted = [e for e in unique.values() if e.get("kind") == "observed" and e.get("component") in COMPONENTS
        and type(e.get("before_tokens")) is int and type(e.get("after_tokens")) is int
        and e["before_tokens"] >= 0 and e["after_tokens"] >= 0
        and e.get("delta_tokens") == e["before_tokens"]-e["after_tokens"] and e.get("counting_method") == METHOD]
    groups = {c: [e for e in accepted if e["component"] == c] for c in COMPONENTS}
    calls = {c: [e for e in unique.values() if e["component"] == c and e.get("kind") == "invocation"] for c in COMPONENTS}
    def component(c, es):
        missing = [e for e in calls[c] if e.get("metadata", {}).get("observed_pair_recorded") is False]
        latest = max(missing, key=lambda e: e["timestamp"]) if missing else None
        return {"saved": sum(e["delta_tokens"] for e in es) if es else None, "events": len(es),
                "invocations": len(calls[c]), "unobserved": len(missing),
                "observation_status": latest["metadata"].get("observation_status") if latest else None}
    return {"observed_net_avoided_tokens": sum(e["delta_tokens"] for e in accepted) if accepted else None,
        "components": {c: component(c, es) for c, es in groups.items()},
        "events": accepted, "counting_method": METHOD}

"""Exact native session identity for a command invocation."""
import json
import os
from .savings import session_id
from .session_usage import find_rollout


def current_session(store):
    sid = session_id(store.session_id) if store.session_id else session_id()
    path = find_rollout(store, sid) if sid else None
    if not path:
        return None
    meta = json.loads(path.open(encoding="utf-8").readline())["payload"]
    # Explicit hook context is validated against the exact native transcript.
    if not store.session_id:
        root = os.environ.get("CODEX_SESSION_ID")
        if root and root != meta.get("session_id"):
            return None
    return sid

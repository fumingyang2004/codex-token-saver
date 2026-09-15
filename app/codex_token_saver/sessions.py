"""Native session registry and one shared CLI/API summary, backed by the ledger."""
import json
import os
from pathlib import Path
import time

import psutil
from .state import Store, write_json, read_json, safe_path
from .session_usage import session_path, validate_rollout, read_session
from .savings import SavingsLedger, best_effort, session_id


def codex_parent():
    try:
        for process in psutil.Process().parents():
            name = process.name().lower()
            if name == "codex" or name == "codex.exe" or name.startswith("codex-"):
                return {"pid": process.pid, "created": process.create_time()}
    except psutil.Error:
        pass
    return None


def register(store, payload):
    sid = session_id(payload.get("session_id"))
    if not sid or not payload.get("transcript_path"):
        return None
    project = Store(store.root, payload.get("cwd", store.project), store.codex_home)
    path = session_path(project, payload["transcript_path"])
    if not validate_rollout(path, sid, project.project):
        return None
    # Root CLI beta: a hook carrying a parent's ID must not register a child.
    meta = json.loads(path.open(encoding="utf-8").readline())["payload"]
    if isinstance(meta.get("source"), dict) and "subagent" in meta["source"]:
        return None
    location = store.root/"sessions"/(sid+".json")
    record = read_json(location)
    record.update(id=sid, project=str(project.project), codex_home=str(project.codex_home), rollout=str(path),
        last_activity=time.time(), ended=payload.get("hook_event_name") == "SessionEnd")
    parent = codex_parent()
    if parent:
        record["process"] = parent
    write_json(location, record)
    return record


def active(record):
    if record.get("ended") or not record.get("process"):
        return False
    try:
        process = psutil.Process(record["process"]["pid"])
        return process.is_running() and abs(process.create_time()-record["process"]["created"]) < .01
    except (psutil.Error, KeyError):
        return False


def listing(store):
    rows = []
    for path in (store.root/"sessions").glob("*.json"):
        record = best_effort(read_json, path)
        if not record or not session_id(record.get("id")):
            continue
        rows.append({**record, "active": active(record)})
    return sorted(rows, key=lambda r:r.get("last_activity",0), reverse=True)[:30]


def snapshot(store, sid=None):
    rows = listing(store)
    selected = next((r for r in rows if r["id"] == sid), None) if sid else next((r for r in rows if r["active"]), None)
    if selected is None:
        return {"session": None, "sessions": [{k:r[k] for k in ("id","active","last_activity")} for r in rows], "summary": None}
    project = Store(store.root, selected["project"], selected["codex_home"])
    from .rtk_spool import ingest
    ingest(project, selected["id"])
    native = read_session(project, selected["id"], selected["rollout"])
    ledger = SavingsLedger(project, selected["id"])
    # Ingest confirmed native CCE observations into the same ledger; browser JS
    # does no accounting. Historical disabled data can still be read.
    for e in native["events"]:
        if e["component"] == "cce" and e["kind"] in ("observed", "invocation"):
            best_effort(ledger._append, "cce", e["event_id"], e["kind"], e["reason"], before=e["before_tokens"],
                after=e["after_tokens"], delta=e["delta_tokens"], source_id=e["source_id"], metadata=e["metadata"], method=e["counting_method"], historical=True)
    from .rtk_spool import pending_status
    pending = pending_status(project, selected['id'], selected.get('ended', False))
    summary = ledger.summary()
    if pending:
        native['warnings'].append(f"RTK: {pending} rewrite(s) pending or execution unconfirmed; not counted as savings.")
    summary['components']['rtk']['pending'] = pending
    unconfirmed = summary['components']['rtk']['unconfirmed_rewrites']
    if unconfirmed:
        native['warnings'].append(f"RTK: {unconfirmed} emitted rewrite(s) have no confirmed execution; they may have been rejected or interrupted.")
    # No prompt, source contents, command output or native rollout payloads reach
    # the web API. Even Details only exposes sanitized accounting metadata.
    summary["events"] = [{k:e[k] for k in ("event_id","component","timestamp","before_tokens","after_tokens","delta_tokens","reason")} for e in summary["events"]]
    summary.update(actual_usage=native["usage"], warnings=native["warnings"])
    return {"session": {k:selected[k] for k in ("id","active","last_activity")},
        "sessions": [{k:r[k] for k in ("id","active","last_activity")} for r in rows], "summary": summary}

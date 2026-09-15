"""Sandbox-writable, per-command telemetry; host hooks own the durable ledger.

Only counts/hashes cross this boundary. Raw RTK buffers are removed by the
observer. No sandbox permissions, global writable roots or command replay.
"""
import copy
import os
from pathlib import Path
import tempfile
import time
import uuid

from .savings import METHOD, SavingsLedger, best_effort
from .state import read_json, safe_path, write_json


def allocate(store, sid):
    """Called by PreToolUse, outside the command sandbox."""
    nonce = uuid.uuid4().hex
    directory = Path(tempfile.gettempdir()) / ("codex-saver-rtk-" + nonce + "-" + uuid.uuid4().hex)
    # Python 3.13 mkdtemp(0700) installs a Windows DACL that excludes the
    # restricted-token command. Inherit the user's Temp ACL on Windows; keep
    # owner-only permissions on Unix. Do not grant access to the install root.
    directory.mkdir(mode=0o777 if os.name == "nt" else 0o700)
    record = {"session_id": sid, "project": str(store.project),
              "directory": str(directory), "created": time.time()}
    write_json(store.directory / "rtk-pending" / (nonce + ".json"), record)
    return nonce


def attach(store, nonce):
    """The command receives a nonce, never an arbitrary writable destination."""
    if not isinstance(nonce, str) or len(nonce) != 32 or any(c not in "0123456789abcdef" for c in nonce):
        raise ValueError("Invalid RTK observation nonce")
    path = store.directory / "rtk-pending" / (nonce + ".json")
    safe_path(path)
    record = read_json(path)
    if record.get("session_id") != store.session_id or record.get("project") != str(store.project):
        raise ValueError("RTK observation session/project mismatch")
    directory = Path(record["directory"])
    safe_path(directory)
    if (not directory.is_absolute() or not directory.name.startswith("codex-saver-rtk-" + nonce + "-")
            or not directory.is_dir()):
        raise ValueError("Invalid RTK observation directory")
    store.rtk_spool = directory


def measurement_store(store):
    if not getattr(store, "rtk_spool", None):
        return store
    target = copy.copy(store)
    target.directory = store.rtk_spool
    return target


def ingest(store, sid):
    """Idempotent host-side import; failures retain the spool for the next poll."""
    for path in (store.directory / "rtk-pending").glob("*.json"):
        best_effort(_ingest_one, store, sid, path)


def pending_status(store, sid, ended=False):
    remaining = 0
    for path in (store.directory / "rtk-pending").glob("*.json"):
        record = best_effort(read_json, path) or {}
        if record.get("session_id") != sid:
            continue
        directory = Path(record.get("directory", ""))
        # Reclaim only an empty, host-issued spool after session end. Never
        # remove an executing wrapper's data or infer execution from allocation.
        if ended and directory.name.startswith("codex-saver-rtk-" + path.stem + "-"):
            def reclaim():
                safe_path(directory); safe_path(path)
                if any(directory.iterdir()):
                    return False
                if not SavingsLedger(store, sid)._append('rtk', 'unconfirmed-rewrite:'+path.stem,
                    'diagnostic', 'Session ended without confirmed execution of the emitted rewrite', historical=True):
                    # Existing stable record also permits an idempotent cleanup.
                    if not any(e['event_id'] == 'unconfirmed-rewrite:'+path.stem for e in SavingsLedger(store, sid).events()):
                        return False
                directory.rmdir()
                path.unlink(missing_ok=True)
                return True
            if best_effort(reclaim):
                continue
        remaining += 1
    return remaining


def _ingest_one(store, sid, path):
    safe_path(path)
    record = read_json(path)
    if record.get("session_id") != sid:
        return
    target = copy.copy(store)
    target.session_id = sid
    attach(target, path.stem)
    spool = measurement_store(target)
    # A completion marker prevents a poll between the observed and invocation
    # inserts from deleting the database while the wrapper is still using it.
    done = spool.directory / "complete.json"
    safe_path(done)
    if read_json(done).get("session_id") != sid:
        return
    database = spool.directory / "savings.sqlite3"
    safe_path(database)
    events = SavingsLedger(spool, sid).events()
    if not events or len(events) > 2:
        return
    destination = SavingsLedger(store, sid)
    for event in events:
        if event["component"] != "rtk" or event["kind"] not in ("observed", "invocation"):
            raise ValueError("Invalid RTK spool event")
        if event["kind"] == "observed":
            a, b = event["before_tokens"], event["after_tokens"]
            if (type(a) is not int or type(b) is not int or min(a, b) < 0
                    or event["delta_tokens"] != a-b or event["counting_method"] != METHOD):
                raise ValueError("Invalid RTK spool counts")
        destination._append("rtk", event["event_id"], event["kind"], event["reason"],
            before=event["before_tokens"], after=event["after_tokens"], delta=event["delta_tokens"],
            source_id=event["source_id"], metadata=event["metadata"], method=event["counting_method"], historical=True)
    # Delete only known files and empty directories under this host-issued path.
    # Concurrent importers may lose this race; stable event IDs prevent doubles.
    database.unlink(missing_ok=True)
    done.unlink(missing_ok=True)
    (spool.directory / "started.json").unlink(missing_ok=True)
    observations = spool.directory / "rtk-observations"
    if observations.exists():
        safe_path(observations)
        observations.rmdir()
    spool.directory.rmdir()
    path.unlink(missing_ok=True)

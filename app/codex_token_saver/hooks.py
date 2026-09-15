"""Fail-open native hooks: safe single-command RTK rewriting and live identity."""
import base64
import json
import os
import re
import shlex
import sys
import time

from . import control, sessions
from .state import Store, write_json
from .savings import SavingsLedger, best_effort
from .rtk_spool import allocate

GUIDANCE = "For repository discovery, use codex_token_saver_cce context_search. If unavailable or insufficient, use native tools. Verify relevant source before editing."

# These commands request diagnostic/configuration details, not a test summary.
# RTK's pytest result filter would discard the very information being requested.
PYTEST_DIAGNOSTICS = {"-h", "--help", "-V", "--version", "--trace-config",
    "--collect-only", "--co", "--fixtures", "--fixtures-per-test", "--markers",
    "--debug", "--setup-only", "--setup-plan", "--setup-show", "--cache-show"}


def arguments(command):
    # No shell parser emulation: expressions, substitutions and pipelines pass
    # through untouched. Hooks never execute the command themselves.
    if not isinstance(command, str) or re.search(r"[\n\r;&|<>`${}()]", command):
        return None
    try:
        args = shlex.split(command, posix=os.name != "nt")
        args = [a[1:-1] if len(a)>1 and a[0] == a[-1] and a[0] in "\"'" else a for a in args]
    except ValueError:
        return None
    if not args:
        return None
    if args[0] == "pytest":
        if any(arg.split("=", 1)[0] in PYTEST_DIAGNOSTICS for arg in args[1:]):
            return None
        return args
    if args[0] == "git" and len(args)>1 and args[1] in ("status","diff","log","show"):
        return args
    return None


def handle(store, payload):
    if not store.enabled():
        return {}
    record = sessions.register(store, payload)
    if not record:
        return {}
    project = Store(store.root, record["project"], store.codex_home)
    event = payload.get("hook_event_name")
    if event == "SessionStart":
        anchor = str(payload.get("turn_id", "start"))+":"+str(payload.get("source", "startup"))
        SavingsLedger(project, record["id"]).record_observed("cce", "guidance:"+anchor, "", GUIDANCE,
            "CCE discovery guidance emitted by SessionStart (observable overhead)")
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": GUIDANCE}}
    if event == "PreToolUse":
        started = time.monotonic()
        data = payload.get("tool_input") or {}
        key = "command" if "command" in data else "cmd"
        args = arguments(data.get(key))
        def audit(decision, **extra):
            best_effort(write_json, project.directory / "hook-diagnostics" / (record["id"] + ".json"),
                {"session_id": record["id"], "updated": time.time(), "decision": decision,
                 "input_keys": sorted(data), "elapsed_ms": round((time.monotonic()-started)*1000), **extra})
        if not args:
            audit("passthrough: unsupported or diagnostic command")
            return {}
        # The host may normalize workdir out of the hook input. The wrapper
        # inherits the real command cwd; accounting always belongs to this session.
        nonce = best_effort(allocate, project, record["id"])
        context = base64.urlsafe_b64encode(json.dumps({"sid":record["id"], "project":str(project.project), "args":args,
                                                     "observation_nonce": nonce}).encode()).decode()
        updated = {**data, key: control.shell([*control.prefix(store), "_rtk", "--context", context])}
        if os.name == "nt":
            updated[key] += "; exit $LASTEXITCODE"
        audit("rewrite emitted; execution not yet confirmed", observation_nonce=nonce)
        return {"hookSpecificOutput": {"hookEventName":event, "permissionDecision":"allow", "updatedInput":updated}}
    if event == "PostToolUse":
        from .rtk_spool import ingest
        best_effort(ingest, project, record["id"])
    if event in ("Stop", "SessionEnd"):
        best_effort(sessions.snapshot, store, record["id"])
    return {}


def run(store):
    try:
        payload = json.loads(sys.stdin.buffer.read(1024*1024))
        result = best_effort(handle, store, payload) or {}
        print(json.dumps(result, ensure_ascii=False))
    except Exception:
        print("{}")
    return 0

"""Fail-open native hooks: safe single-command RTK rewriting and live identity."""
import base64
import json
import os
import re
import shlex
import sys

from . import control, sessions
from .state import Store
from .savings import SavingsLedger, best_effort

GUIDANCE = "For repository discovery, use codex_token_saver_cce context_search. If unavailable or insufficient, use native tools. Verify relevant source before editing."


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
        data = payload.get("tool_input") or {}
        key = "command" if "command" in data else "cmd"
        args = arguments(data.get(key))
        if not args:
            return {}
        context = base64.urlsafe_b64encode(json.dumps({"sid":record["id"], "cwd":str(data.get("workdir") or payload.get("cwd") or project.project), "args":args}).encode()).decode()
        updated = {**data, key: control.shell([*control.prefix(store), "_rtk", "--context", context])}
        return {"hookSpecificOutput": {"hookEventName":event, "permissionDecision":"allow", "updatedInput":updated}}
    if event in ("PostToolUse", "Stop", "SessionEnd"):
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

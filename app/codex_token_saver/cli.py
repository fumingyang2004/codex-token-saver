"""Small user CLI and internal hook/MCP entry points."""
import argparse
import base64
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

from . import VERSION, control, dependencies, sessions
from .state import Store, StackError, read_json


def parser():
    p = argparse.ArgumentParser(prog="codex-saver")
    p.add_argument("--home", type=Path)
    p.add_argument("--codex-home", type=Path)
    p.add_argument("--project", type=Path)
    sub = p.add_subparsers(dest="action",required=True)
    for name in ("status","doctor","enable","disable","uninstall","_setup"):
        sub.add_parser(name).add_argument("--json", action="store_true")
    sub.add_parser("version")
    s = sub.add_parser("savings")
    s.add_argument("--session-id")
    s.add_argument("--json", action="store_true")
    ui = sub.add_parser("ui")
    ui.add_argument("--no-browser", action="store_true")
    ui.add_argument("--stop", action="store_true")
    sub.add_parser("_ui-serve")
    sub.add_parser("_hook")
    sub.add_parser("_mcp")
    sub.add_parser("_rtk").add_argument("--context",required=True)
    sub.add_parser("run").add_argument("args",nargs=argparse.REMAINDER)
    return p


def status(store):
    deps = dependencies.manifest(store)
    return {"name":"Codex Token Saver", "version":VERSION, "status":"ON" if store.enabled() else "OFF",
        "rtk":"ready" if Path(deps.get("rtk", "missing")).is_file() else "unavailable",
        "cce":"ready" if Path(deps.get("cce", "missing")).is_file() else "unavailable",
        "telemetry":"Approximate", "codex_detected":bool(deps.get("codex") or dependencies.detect_codex())}


def doctor(store):
    checks = {}
    binary = dependencies.manifest(store).get("codex") or dependencies.detect_codex()
    checks["Codex detected"] = bool(binary)
    try:
        version = subprocess.check_output([binary,"--version"],text=True,timeout=10).strip()
        import re
        match = re.search(r"(\d+)\.(\d+)\.(\d+)",version)
        checks["Codex hooks version (0.154+)"] = bool(match and tuple(map(int,match.groups())) >= (0,154,0))
    except Exception:
        version = "unavailable"
        checks["Codex hooks version (0.154+)"] = False
    for label,fn in (
        ("RTK observer ready",lambda: dependencies.observer_ready(store)),
        ("CCE 0.4.26 available",lambda:importlib.metadata.version("code-context-engine") == dependencies.CCE_VERSION),
        ("Session directory readable",lambda:store.codex_home.is_dir()),
        ("Configuration active",lambda:store.enabled())):
        try: checks[label] = bool(fn())
        except Exception: checks[label] = False
    try:
        store.root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryFile(dir=store.root) as f: f.write(b"ok")
        checks["Savings store writable"] = True
    except OSError: checks["Savings store writable"] = False
    try:
        with socket.socket() as s: s.bind(("127.0.0.1",0))
        checks["UI port available"] = True
    except OSError: checks["UI port available"] = False
    return {"ready":all(checks.values()),"checks":checks,"codex_version":version,
        "note":"Review the installed hooks in Codex at first launch. Native hook trust is not bypassed by the installer."}


def format_savings(value):
    if not value["session"]:
        return "No active Codex session.\nStart Codex normally, or select a previous session with --session-id."
    def num(n): return "unavailable" if n is None else f"{n:,}"
    result=value["summary"]
    lines=["Session " + value["session"]["id"], "Observed tokens avoided  " + num(result["observed_net_avoided_tokens"])]
    for name,comp in result["components"].items(): lines.append(f"{name.upper():8} {num(comp['saved'])} ({comp['events']} events)")
    for key in ("input_tokens","cached_input_tokens","output_tokens","reasoning_output_tokens"):
        lines.append(f"{key:24} {num(result['actual_usage'][key])}")
    lines.append("Token counting: Approximate (UTF-8 bytes / 4, rounded up). Not a billing or ON/OFF estimate.")
    return "\n".join(lines)


def main(argv=None):
    args=parser().parse_args(argv)
    store=Store(args.home,args.project,args.codex_home)
    try:
        if args.action == "version":
            print("Codex Token Saver " + VERSION); return 0
        if args.action == "_hook":
            from .hooks import run
            return run(store)
        if args.action == "_mcp":
            from .runtime import mcp_proxy
            return mcp_proxy(store)
        if args.action == "_rtk":
            from .runtime import rtk
            context=json.loads(base64.urlsafe_b64decode(args.context))
            store=Store(args.home,context["cwd"],args.codex_home)
            store.command_cwd=Path(context["cwd"]).resolve()
            store.session_id=context["sid"]
            return rtk(store,context["args"])
        if args.action == "_setup":
            dependencies.setup(store)
            value=control.enable(store)
        elif args.action == "enable": value=control.enable(store)
        elif args.action == "disable": value=control.disable(store)
        elif args.action == "uninstall":
            from .installation import uninstall
            value=uninstall(store)
        elif args.action == "status": value=status(store)
        elif args.action == "doctor": value=doctor(store)
        elif args.action == "savings":
            sid=args.session_id or os.environ.get("CODEX_THREAD_ID")
            value=sessions.snapshot(store,sid)
            print(json.dumps(value,ensure_ascii=False,indent=2) if args.json else format_savings(value))
            return 0
        elif args.action in ("ui","_ui-serve"):
            from .webserver import start, serve, stop
            if args.action == "_ui-serve": return serve(store)
            if args.stop: return stop(store)
            print("Dashboard:\n" + start(store,not args.no_browser)); return 0
        elif args.action == "run":
            binary=dependencies.manifest(store).get("codex") or dependencies.detect_codex()
            if not binary: raise StackError("Codex CLI not found")
            return subprocess.call([binary,*args.args],cwd=store.project)
        if getattr(args,"json",False): print(json.dumps(value,ensure_ascii=False,indent=2))
        else:
            print("Codex Token Saver " + VERSION)
            for key,item in value.items():
                if key == "checks":
                    for label,ok in item.items(): print(("[OK] " if ok else "[!] ")+label)
                else: print(f"{key}: {item}")
        return 1 if args.action == "doctor" and not value["ready"] else 0
    except (StackError,OSError,ValueError,RuntimeError) as exc:
        print("Codex Token Saver: " + str(exc),file=sys.stderr)
        return 1

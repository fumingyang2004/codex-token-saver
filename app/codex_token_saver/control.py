"""Backup-first user configuration with exact ownership and conflict preservation."""
import base64
import copy
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time
import uuid

import tomlkit
from .dependencies import manifest
from .state import StackError, atomic_write, read_bytes, read_json, write_json

CCE_TOOLS = ("context_search", "expand_chunk", "related_context", "index_status")
MCP_NAME = "codex_token_saver_cce"
EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd")


def prefix(store):
    return [sys.executable, "-m", "codex_token_saver", "--home", str(store.root), "--codex-home", str(store.codex_home)]


def shell(args, windows=None):
    if windows is None:
        windows = sys.platform == "win32"
    if windows:
        return "& " + " ".join("'" + str(a).replace("'", "''") + "'" for a in args)
    return shlex.join(map(str, args))


def hook_command(store):
    # Hook commands are shell strings; Codex uses its Windows command runner.
    args = [*prefix(store), "_hook"]
    return subprocess.list2cmdline(args) if sys.platform == "win32" else shlex.join(args)


def enable(store):
    with store.lock():
        settings = store.read()
        if settings.get("enabled"):
            return {"status": "ON", "backup": settings.get("backup")}
        if settings.get("owned"):
            raise StackError("Resolve prior configuration conflicts before enabling again")
        paths = {"config": store.codex_home/"config.toml", "hooks": store.codex_home/"hooks.json"}
        before = {name: read_bytes(p) for name,p in paths.items()}
        config = tomlkit.parse((before["config"] or b"").decode("utf-8"))
        hooks = json.loads(before["hooks"] or b"{}")
        if MCP_NAME in config.get("mcp_servers", {}):
            raise StackError("Existing codex_token_saver_cce is not owned by this installation")
        if not isinstance(hooks, dict) or not isinstance(hooks.get("hooks", {}), dict):
            raise StackError("Invalid existing hooks; preserving configuration")
        backup = store.codex_home/"backups/codex-token-saver"/(time.strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:6])
        backup.mkdir(parents=True)
        for key,payload in before.items():
            if payload is not None:
                atomic_write(backup/paths[key].name, payload)
        write_json(backup/"original-files.json", {key: value is not None for key,value in before.items()})
        feature_before = config.get("features", {}).get("hooks")
        config.setdefault("features", {})["hooks"] = True
        mcp = {"command": sys.executable, "args": [*prefix(store)[1:], "_mcp"], "enabled": True,
            "required": False, "startup_timeout_sec": 60, "tool_timeout_sec": 60,
            "enabled_tools": list(CCE_TOOLS)}
        config.setdefault("mcp_servers", {})[MCP_NAME] = mcp
        added = {}
        for event in EVENTS:
            entry = {"hooks": [{"type": "command", "command": hook_command(store), "timeout": 3 if event == "SessionEnd" else 10}]}
            if event == "PreToolUse":
                entry["matcher"] = "^Bash$"
            hooks.setdefault("hooks", {}).setdefault(event, []).append(entry)
            added[event] = entry
        after = {"config": tomlkit.dumps(config).encode(), "hooks": (json.dumps(hooks,ensure_ascii=False,indent=2)+"\n").encode()}
        # Configuration is reviewed and concrete before writes; recover exact files
        # if any later write fails. The runtime remains OFF until all writes finish.
        try:
            for key,path in paths.items():
                if read_bytes(path) != before[key]:
                    raise StackError("Configuration changed during installation")
                atomic_write(path, after[key])
            write_json(store.state_path, {"enabled": True, "backup": str(backup), "codex_home": str(store.codex_home),
                "owned": {"mcp": mcp, "hooks": added, "feature_before": feature_before,
                    "before": {k: base64.b64encode(v).decode() if v is not None else None for k,v in before.items()},
                    "after": {k: base64.b64encode(v).decode() for k,v in after.items()}}})
        except Exception:
            for key,path in paths.items():
                if read_bytes(path) == after[key]:
                    atomic_write(path,before[key])
            raise
        return {"status": "ON", "backup": str(backup)}


def disable(store):
    with store.lock():
        settings = store.read()
        settings["enabled"] = False
        write_json(store.state_path, settings)  # guards first
        owned = settings.get("owned")
        if not owned:
            return {"status": "OFF", "conflicts": []}
        conflicts = []
        for key, name in (("config", "config.toml"), ("hooks", "hooks.json")):
            path = store.codex_home/name
            current = read_bytes(path)
            expected = base64.b64decode(owned["after"][key])
            original = owned["before"][key]
            if current == expected:
                atomic_write(path, base64.b64decode(original) if original is not None else None)
                continue
            if current is None:
                continue
            try:
                if key == "config":
                    doc = tomlkit.parse(current.decode("utf-8"))
                    servers = doc.get("mcp_servers", {})
                    if servers.get(MCP_NAME) == owned["mcp"]:
                        del servers[MCP_NAME]
                        if not servers:
                            doc.pop("mcp_servers", None)
                    elif MCP_NAME in servers:
                        conflicts.append("Edited owned MCP configuration retained")
                    if doc.get("features", {}).get("hooks") is True:
                        if owned["feature_before"] is None:
                            doc["features"].pop("hooks", None)
                        else:
                            doc["features"]["hooks"] = owned["feature_before"]
                    atomic_write(path, tomlkit.dumps(doc).encode())
                else:
                    doc = json.loads(current)
                    for event, entry in owned["hooks"].items():
                        values = doc.get("hooks", {}).get(event, [])
                        if entry in values:
                            values.remove(entry)
                        elif any("codex_token_saver" in str(v) for v in values):
                            conflicts.append("Edited owned hook retained: " + event)
                    atomic_write(path, (json.dumps(doc,ensure_ascii=False,indent=2)+"\n").encode())
            except Exception as exc:
                conflicts.append(f"{name}: {type(exc).__name__}; preserved")
        if not conflicts:
            settings.pop("owned", None)
        write_json(store.state_path, settings)
        return {"status": "OFF", "conflicts": conflicts}

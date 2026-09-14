"""State-guarded RTK and CCE transport extracted from validated integration."""
from __future__ import annotations
import json, queue, subprocess, sys, threading, os, time
from collections import deque
from pathlib import Path
from .dependencies import manifest, private_env
from .state import StackError, write_json, project_root

def rtk(store, args):
    if not args:
        raise StackError("rtk requires an executable and arguments after --")
    deps = manifest(store)
    from pathlib import Path
    binary = deps.get("rtk")
    use_rtk = store.active("rtk") and binary and Path(binary).is_file()
    command = [binary, *args] if use_rtk else args
    env = os.environ.copy()
    env.update(RTK_TELEMETRY_DISABLED="1", RTK_DB_PATH=str(store.directory / "rtk-history.db"),
               RTK_TEE_DIR=str(store.directory / "rtk-tee"))
    if use_rtk:
        try:
            from .rtk_observation import execute
        except Exception:
            execute = None  # Optional telemetry must not prevent native execution.
        observed_code = execute(store, args, binary, env) if execute else None
        if observed_code is not None:
            return observed_code
    try:
        # Inherit tool stdout and exit status. Never replay a command after nonzero exit.
        code = subprocess.call(command, cwd=getattr(store, "command_cwd", store.project), env=env)
        if use_rtk:
            pass
        return code
    except OSError:
        if use_rtk:  # Spawn failed; the native command has not run yet.
            return subprocess.call(args, cwd=getattr(store, "command_cwd", store.project), env=env)
        raise


def start_cce(store):
    # Existing CCE project settings may redirect storage or model services. This beta leaves
    # them untouched and reports a conflict instead of silently inheriting them.
    if (store.project / ".context-engine.yaml").exists():
        raise StackError("Existing .context-engine.yaml: This beta cannot guarantee isolated local CCE settings")
    binary = manifest(store).get("cce")
    if not binary:
        raise StackError("CCE not installed")
    env = private_env(store)
    command = None
    try:
        from .cce_observation import launch
        command = launch(store, binary, env)
    except Exception:
        pass
    proc = subprocess.Popen(command or [binary, "serve", "--project-dir", str(store.project)],
                            cwd=getattr(store, "command_cwd", store.project), env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", bufsize=1)
    capture_stderr(proc)
    return proc


def capture_stderr(proc):
    # Drain continuously so diagnostics cannot fill the pipe. Keep only a bounded
    # local tail; never log MCP requests or retrieved source in the proxy.
    proc.cce_stderr_tail = deque(maxlen=32)
    def drain():
        try:
            while line := proc.stderr.readline(1024):
                proc.cce_stderr_tail.append(line.rstrip())
        except (OSError, ValueError):
            pass
    threading.Thread(target=drain, daemon=True).start()


def failure_detail(reason, exc=None):
    if exc is None:
        return reason
    # UnicodeError.__str__ may include query text. Retain the codec/reason only.
    detail = f"{exc.encoding}: {exc.reason}" if isinstance(exc, UnicodeError) and hasattr(exc, "encoding") else str(exc)
    return f"{reason}: {type(exc).__name__}: {detail}"[:1000]


def record_cce_failure(store, reason, proc=None, exc=None):
    evidence = {"ok": False, "detail": failure_detail(reason, exc), "proxy_pid": os.getpid()}
    if proc is not None:
        evidence.update(child_pid=proc.pid, returncode=proc.poll(),
                        stderr_tail="\n".join(list(getattr(proc, "cce_stderr_tail", ())))[-8192:])
    try:
        write_json(store.directory / "cce-last-error.json", evidence)
        write_json(store.directory / "cce-health.json", evidence)
    except (OSError, StackError):
        # Diagnostic storage failure must not suppress the MCP fallback response.
        pass


def lines_to_queue(stream, out):
    try:
        for line in stream:
            try:
                out.put(json.loads(line))
            except ValueError:
                continue
    finally:
        out.put(None)


def stop_process(proc):
    # The disable monitor and client-disconnect path may arrive together.
    with proc.__dict__.setdefault("_cce_stop_lock", threading.Lock()):
        _stop_process(proc)


def _stop_process(proc):
    # EOF lets the real server and Windows launcher chain exit together.
    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
    except (OSError, ValueError):
        pass
    if proc.poll() is None:
        try:
            proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            pass
        if os.name == "nt":
            # Only the still-running child we own and its descendants; never
            # enumerate/kill other Codex sessions or shared CCE instances.
            try:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
        else:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def probe_cce(store, timeout=60, *, through_proxy=False):
    if through_proxy:
        proc = subprocess.Popen([sys.executable, "-m", "codex_token_saver",
                                 "--home", str(store.root), "--codex-home", str(store.codex_home),
                                 "--project", str(store.project), "_mcp"], cwd=store.project,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", bufsize=1)
        capture_stderr(proc)
    else:
        proc = start_cce(store)
    messages = queue.Queue()
    threading.Thread(target=lines_to_queue, args=(proc.stdout, messages), daemon=True).start()
    def request(message):
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            response = messages.get(timeout=max(0, deadline - time.monotonic()))
            if response is None:
                raise StackError("CCE exited before MCP response")
            if response.get("id") == message.get("id"):
                if "error" in response:
                    raise StackError(f"CCE MCP error: {response['error']}")
                return response["result"]
    try:
        request({"id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05",
                 "capabilities": {}, "clientInfo": {"name": "codex-efficiency-doctor", "version": "0.1"}}})
        proc.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        proc.stdin.flush()
        tools = request({"id": 2, "method": "tools/list", "params": {}})
        names = [tool["name"] for tool in tools.get("tools", [])]
        if "context_search" not in names or "index_status" not in names:
            raise StackError("CCE MCP missing required retrieval tools")
        if not through_proxy and "set_output_compression" in names:
            output = request({"id": 5, "method": "tools/call", "params": {
                "name": "set_output_compression", "arguments": {"level": "off"}}})
            if output.get("isError"):
                raise StackError("CCE output compression could not be disabled")
        status = request({"id": 3, "method": "tools/call", "params": {"name": "index_status", "arguments": {}}})
        if status.get("isError"):
            raise StackError("CCE index_status failed")
        search = request({"id": 4, "method": "tools/call", "params": {"name": "context_search",
                         "arguments": {"query": "中文查询与 emoji 🔍 project entry point", "top_k": 1, "max_tokens": 256}}})
        if search.get("isError") or not search.get("content"):
            raise StackError("CCE context_search failed")
        if "[Respond using" in json.dumps(search):
            raise StackError("CCE global output compression policy is not OFF")
        return {"ok": True, "tools": names, "index_status": status, "context_search": True,
                "output_compression": "off",
                "transport": "proxy" if through_proxy else "direct"}
    except (queue.Empty, OSError, ValueError, StackError) as exc:
        record_cce_failure(store, "CCE functional probe failed", proc, exc)
        raise StackError(failure_detail("CCE functional probe failed", exc)) from exc
    finally:
        stop_process(proc)


def mcp_proxy(store):
    """Transparent JSONL forwarding; deny new calls immediately after disable.

    Each message is checked, and an idle monitor stops the upstream on disable.
    No retrieval algorithms or MCP tool schemas are reimplemented here.
    """
    # MCP JSONL is UTF-8 regardless of Windows' console/locale encoding. Without
    # this, gbk/surrogateescape silently corrupts queries or raises during the
    # UTF-8 child write, misreported as a broken transport.
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="strict")
    proc = None
    project_matches = project_root(Path.cwd()) == store.project
    restricted = store.read().get("schema", 1) >= 2
    from .control import CCE_TOOLS
    output_lock = threading.Lock()
    pending = set()
    pending_lock = threading.Lock()
    failed = threading.Event()
    def emit(message):
        with output_lock:
            print(json.dumps(message), flush=True)
    def unavailable(message, reason):
        if "id" not in message:
            return
        method = message.get("method")
        if method == "initialize":
            result = {"protocolVersion": message.get("params", {}).get("protocolVersion", "2024-11-05"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "efficiency-disabled", "version": "0.1"}}
        elif method == "tools/list":
            result = {"tools": []}
        elif method == "tools/call":
            result = {"isError": True, "content": [{"type": "text", "text": reason + "; use native file tools"}]}
        else:
            result = {}
        emit({"jsonrpc": "2.0", "id": message["id"], "result": result})
    def relay(child):
        relay_error = None
        try:
            for line in child.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                with pending_lock:
                    if "id" in message and "method" not in message:
                        if message["id"] not in pending:
                            continue
                        pending.remove(message["id"])
                if restricted and isinstance(message.get("result"), dict):
                    result = message["result"]
                    if "tools" in result:
                        result["tools"] = [t for t in result["tools"] if t.get("name") in CCE_TOOLS]
                    if "capabilities" in result:
                        result["capabilities"].pop("prompts", None)
                emit(message)
                # Counts become attributable only when the native host records
                # this exact opaque return nonce; shared process IDs are ignored.
                try:
                    from .cce_observation import delivered
                    delivered(store, message)
                except Exception:
                    pass
        except (OSError, ValueError) as exc:
            relay_error = exc
        with pending_lock:
            was_failed = failed.is_set()
            failed.set()
            ids = list(pending)
            pending.clear()
        for request_id in ids:
            emit({"jsonrpc": "2.0", "id": request_id,
                  "error": {"code": -32000, "message": "CCE unavailable; continue with native file tools"}})
        if store.active("cce") and project_matches and not stopped.is_set() and not was_failed:
            record_cce_failure(store, "CCE output closed", child, relay_error)
    stopped = threading.Event()
    def monitor(child):
        while not stopped.wait(0.25):
            if failed.is_set() or not store.active("cce"):
                stop_process(child)
                return
    try:
        if store.active("cce") and project_matches:
            try:
                proc = start_cce(store)
                threading.Thread(target=relay, args=(proc,), daemon=True).start()
                threading.Thread(target=monitor, args=(proc,), daemon=True).start()
            except (OSError, StackError) as exc:
                record_cce_failure(store, "CCE startup failed", exc=exc)
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not project_matches or not store.active("cce") or failed.is_set() or proc is None or proc.poll() is not None:
                unavailable(message, "CCE disabled or unavailable")
                continue
            if restricted:
                method = message.get("method", "")
                if method.startswith("prompts/") or (method == "tools/call" and message.get("params", {}).get("name") not in CCE_TOOLS):
                    unavailable(message, "CCE method excluded by V1 ownership policy")
                    continue
            try:
                with pending_lock:
                    if failed.is_set():
                        unavailable(message, "CCE disabled or unavailable")
                        continue
                    if "id" in message and "method" in message:
                        pending.add(message["id"])
                proc.stdin.write(json.dumps(message) + "\n")
                proc.stdin.flush()
            except (OSError, ValueError) as exc:
                failed.set()
                record_cce_failure(store, "CCE transport failed", proc, exc)
                with pending_lock:
                    owns_reply = message.get("id") in pending
                    pending.discard(message.get("id"))
                if owns_reply:
                    unavailable(message, "CCE transport failed")
    finally:
        stopped.set()
        if proc:
            stop_process(proc)
    return 0

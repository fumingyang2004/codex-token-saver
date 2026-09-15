"""Same-invocation RTK observation; original executable stays the fallback.

The additive Rust observer exposes a selected, provenance-checked real buffer.
This wrapper independently captures and forwards final stdout/stderr bytes.
No command is executed again to obtain a baseline.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import uuid

from .savings import SavingsLedger, best_effort
from .savings_adapters import current_session
from .state import safe_path, write_json
from .rtk_spool import measurement_store

ASSETS = Path(__file__).parent / "assets/rtk-observer"
LIMIT = 16 * 1024 * 1024


class ObservationUnavailable(ValueError):
    pass


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(store, binary):
    if not store.active("rtk") or os.environ.get("CODEX_SAVER_RTK_OBSERVER") == "0":
        raise ObservationUnavailable("RTK observation disabled")
    # Redirecting a terminal could change upstream color/interactive behavior.
    # Codex tool invocations already use pipes; interactive terminals stay native.
    if sys.stdout.isatty() or sys.stderr.isatty():
        raise ObservationUnavailable("Interactive terminal capture unsupported")
    manifest = json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))
    if manifest["platform"] != sys.platform or Path(manifest["binary"]).name != manifest["binary"]:
        raise ObservationUnavailable("RTK observer platform mismatch")
    executable = ASSETS / manifest["binary"]
    if (manifest["protocol"] != "ces-rtk-pair-v1" or sha(binary) != manifest["base_binary_sha256"]
            or sha(executable) != manifest["binary_sha256"]):
        raise ObservationUnavailable("RTK observer integrity check failed")
    sid = current_session(store)
    if not sid:
        raise ObservationUnavailable("Exact native session unavailable")
    invocation = uuid.uuid4().hex
    directory = store.directory / "rtk-observations" / invocation
    safe_path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    return {"executable": executable, "session_id": sid, "invocation": invocation,
            "path": directory / "pair.jsonl", "binary_sha256": manifest["binary_sha256"]}


def cleanup(plan):
    path = plan["path"]
    safe_path(path)
    path.unlink(missing_ok=True)
    # Delete only the exact empty invocation directory, never recurse.
    path.parent.rmdir()


def collect(stream, destination, result):
    chunks, size, complete = [], 0, True
    try:
        while chunk := stream.read1(65536):
            size += len(chunk)
            if size <= LIMIT:
                chunks.append(chunk)
            else:
                complete = False
                chunks.clear()
            try:
                target = getattr(destination, "buffer", None)
                if target is not None:
                    target.write(chunk)
                    target.flush()
                else:  # Python test/capture streams; native CLI has byte streams.
                    destination.write(chunk.decode("utf-8", errors="replace"))
                    destination.flush()
            except BrokenPipeError:
                # Propagate downstream closure by closing our upstream read end.
                # Do not keep consuming output and hide a broken pipe from RTK.
                complete = False
                break
            except Exception:
                complete = False
                # Keep draining our child; never replay it after a forwarding error.
    except Exception:
        complete = False
    finally:
        result.update(data=b"".join(chunks), complete=complete)
        best_effort(stream.close)


def outcome(args, code, stderr=b""):
    options = args[2:args.index("--")] if "--" in args else args[2:]
    # Git also returns 1 for missing --no-index paths. Accept only empty stderr
    # or its known warning lines; unknown/localized diagnostics stay failures.
    warnings_only = all(not line.strip() or line.startswith(b"warning:") for line in stderr.splitlines())
    differences = (args[:2] == ["git", "diff"] and code == 1 and "--check" not in options and warnings_only)
    return {"exit_code": code, "failed": code != 0 and not differences,
            "exit_meaning": "differences" if differences else ("success" if code == 0 else "error")}


def finish(store, plan, pid, code, stdout, stderr):
    ledger = SavingsLedger(store, plan["session_id"])
    reason = "raw boundary unavailable (unregistered, unsupported, or observation failed)"
    recorded = False
    try:
        if not stdout["complete"] or not stderr["complete"]:
            raise ValueError("capture incomplete or over 16 MiB; output still forwarded")
        if not plan["path"].is_file() or plan["path"].stat().st_size > LIMIT * 3:
            raise ValueError(reason)
        records = [json.loads(line) for line in plan["path"].read_text(encoding="utf-8").splitlines()]
        if len(records) != 1:
            raise ValueError("ambiguous multiple boundaries; not added against a shared final output")
        event = records[0]
        if (event.get("protocol") != "ces-rtk-pair-v1" or event.get("pid") != pid
                or event.get("invocation_id") != plan["invocation"]):
            raise ValueError("observation identity mismatch")
        before, tracked = event["before"], event["tracked_after"]
        if not isinstance(before, str) or not isinstance(tracked, str):
            raise ValueError("invalid observed representations")
        out, err = stdout["data"].decode("utf-8"), stderr["data"].decode("utf-8")
        # RTK's git --stat passthrough prints stdout.trim(), while its tracker
        # retains the untrimmed buffer. Admit only this exact whitespace change;
        # token counts still use the complete, unmodified emitted bytes.
        if (tracked and tracked not in out and tracked not in err
                and tracked.strip() != out.strip() and tracked.strip() != err.strip()):
            raise ValueError("tracked representation not found in actual emitted output")
        recorded = bool(ledger.record_observed("rtk", "rtk-pair:" + plan["invocation"], before, out + err,
            "same RTK invocation: captured input representation -> complete emitted stdout + stderr",
            source_id=plan["invocation"], metadata={"boundary": event.get("source"), "pid": pid,
                "exit_code": code, "observer_binary_sha256": plan["binary_sha256"],
                "stdout_bytes": len(stdout["data"]), "stderr_bytes": len(stderr["data"]),
                "stdout_sha256": hashlib.sha256(stdout["data"]).hexdigest(),
                "stderr_sha256": hashlib.sha256(stderr["data"]).hexdigest(),
                "coverage": "RTK text boundary; decoded input, full UTF-8 emitted output; cross-stream order undefined"}))
        reason = "observed boundary recorded" if recorded else "duplicate event or ledger write unavailable"
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
    ledger.record_invocation("rtk", "rtk-call:" + plan["invocation"], "guarded RTK process completed",
        source_id=plan["invocation"], metadata={**outcome(plan.get("args", []), code, stderr.get("data", b"")), "observed_pair_recorded": recorded,
                                               "observation_status": reason})


def execute(store, args, binary, env):
    """None means no child was started; every started child returns its own code."""
    store = measurement_store(store)
    def unavailable(reason):
        if getattr(store, "rtk_spool", None):
            nonce = uuid.uuid4().hex
            SavingsLedger(store, store.session_id).record_invocation("rtk", "rtk-call:" + nonce,
                "RTK observation unavailable; native Git fallback" if args[:2] == ["git", "diff"] else "RTK observation unavailable; original RTK fallback",
                metadata={"observed_pair_recorded": False, "observation_status": reason})
            best_effort(write_json, store.directory / "complete.json", {"session_id": store.session_id})
    try:
        plan = prepare(store, binary)
        plan["args"] = args
    except Exception as exc:
        best_effort(unavailable, str(exc) if isinstance(exc, ObservationUnavailable) else type(exc).__name__)
        return None
    proc = None
    jobs, readers = [], []
    try:
        child_env = {**env, "CES_RTK_OBSERVATION_FILE": str(plan["path"]),
                     "CES_RTK_INVOCATION_ID": plan["invocation"]}
        # Allocate optional forwarding workers before starting the real command.
        # A thread-resource failure can then safely fall back without replay.
        def read_job(channel):
            job = channel.get()
            if job is not None:
                collect(*job)
        try:
            for _ in range(2):
                channel = queue.Queue()
                reader = threading.Thread(target=read_job, args=(channel,))
                reader.start()
                jobs.append(channel)
                readers.append(reader)
        except Exception:
            best_effort(unavailable, "Capture worker unavailable")
            return None
        try:
            proc = subprocess.Popen([str(plan["executable"]), *args], cwd=getattr(store, "command_cwd", store.project),
                env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError:
            best_effort(unavailable, "Observer process unavailable")
            return None  # original RTK fallback; command has not run
        out, err = {}, {}
        jobs[0].put((proc.stdout, sys.stdout, out))
        jobs[1].put((proc.stderr, sys.stderr, err))
        code = proc.wait()
        for reader in readers:
            reader.join()
        best_effort(finish, store, plan, proc.pid, code, out, err)
        return code
    finally:
        if proc is None:
            for channel in jobs:
                channel.put(None)
            for reader in readers:
                reader.join()
        best_effort(cleanup, plan)
        if proc is not None and getattr(store, "rtk_spool", None):
            best_effort(write_json, store.directory / "complete.json", {"session_id": plan["session_id"]})

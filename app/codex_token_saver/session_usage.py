"""Read native cumulative usage for an exact thread. No credits or counterfactuals."""
from __future__ import annotations

import json
from pathlib import Path

from .state import canonical, project_root, safe_path

FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
          "output_tokens", "reasoning_output_tokens", "total_tokens")


def session_path(store, path):
    path = Path(path)
    safe_path(path)
    resolved = canonical(path)
    if not any(resolved.is_relative_to(canonical(store.codex_home / root))
               for root in ("sessions", "archived_sessions")):
        raise ValueError("Rollout is outside this CODEX_HOME session roots")
    return path


def valid_usage(usage):
    if not isinstance(usage, dict) or type(usage.get("total_tokens")) is not int or usage["total_tokens"] < 0:
        return False
    if any(usage.get(k) is not None and (type(usage[k]) is not int or usage[k] < 0) for k in FIELDS):
        return False
    a, b = usage.get("input_tokens"), usage.get("output_tokens")
    if a is not None and b is not None and a+b != usage["total_tokens"]:
        return False
    return not ((a is not None and usage.get("cached_input_tokens", 0) is not None
                 and usage.get("cached_input_tokens", 0) > a)
                or (b is not None and usage.get("reasoning_output_tokens", 0) is not None
                    and usage.get("reasoning_output_tokens", 0) > b))


def validate_rollout(path, sid, project):
    with Path(path).open(encoding="utf-8") as handle:
        event = json.loads(handle.readline())
    payload = event.get("payload", {})
    return (event.get("type") == "session_meta" and payload.get("id") == sid
            and isinstance(payload.get("cwd"), str)
            and project_root(Path(payload["cwd"])) == project)


def find_rollout(store, sid, explicit=None):
    if not sid:
        return None
    if explicit is not None:
        path = session_path(store, explicit)
        if not validate_rollout(path, sid, store.project):
            raise ValueError("Rollout session/project mismatch")
        return path
    candidates = []
    for name in ("sessions", "archived_sessions"):
        root = store.codex_home / name
        if root.is_dir():
            for path in root.rglob(f"*{sid}.jsonl"):
                session_path(store, path)
                if validate_rollout(path, sid, store.project):
                    candidates.append(path)
    if len(candidates) > 1:
        raise ValueError("Ambiguous native rollout; provide --rollout")
    return candidates[0] if candidates else None


def read_session(store, sid, explicit=None):
    result = {"usage": dict.fromkeys(FIELDS), "usage_source": None, "rollout_path": None,
              "configured_model": None, "configured_effort": None, "configured_segments": [],
              "actual_model": None, "actual_effort": None, "as_of": None,
              "usage_integrity": "unavailable", "incomplete_tail": False, "warnings": [], "events": []}
    try:
        path = find_rollout(store, sid, explicit)
        if path is None:
            result["warnings"].append("Native usage unavailable: no exact session/project rollout.")
            return result
        result["rollout_path"] = str(path)
        previous_total = None
        native_usage = None
        native_total = None
        # Bound to the size at open, so an active session cannot keep a report running.
        with path.open("rb") as handle:
            remaining = path.stat().st_size
            while remaining > 0:
                line = handle.readline(remaining)
                remaining -= len(line)
                if not line.endswith(b"\n"):
                    result["incomplete_tail"] = True
                    break  # writer may be in the middle of its final record
                event = json.loads(line)
                payload = event.get("payload", {})
                if event.get("type") == "turn_context":
                    result["configured_model"] = payload.get("model")
                    result["configured_effort"] = payload.get("effort")
                    segment = {"turn_id": payload.get("turn_id"), "model": payload.get("model"),
                               "effort": payload.get("effort")}
                    if segment not in result["configured_segments"]:
                        result["configured_segments"].append(segment)
                if event.get("type") == "token_usage_record" and payload.get("thread_id") == sid:
                    candidate = payload.get("thread_token_usage")
                    if valid_usage(candidate):
                        total = candidate.get("total_tokens")
                        if type(total) is int:
                            if native_total is not None and total < native_total:
                                raise ValueError("Native thread cumulative usage decreased")
                            native_total = total
                        native_usage = candidate
                        result["as_of"] = {"timestamp": event.get("timestamp"), "ordinal": event.get("ordinal")}
                    else:
                        result["warnings"].append("Malformed thread cumulative snapshot skipped; usage may be stale.")
                if (event.get("type") == "event_msg" and payload.get("type") == "item_completed"
                        and payload.get("thread_id") == sid):
                    item = payload.get("item", {})
                    if (item.get("type") == "McpToolCall" and item.get("server") == "codex_token_saver_cce"
                            and isinstance(item.get("id"), str)):
                        # A read-only projection of already persisted native events.
                        # No prompt/code-text scanning and no shared MCP process identity.
                        result["events"].append(dict(session_id=sid, event_id="native-cce:" + item["id"],
                            component="cce", kind="invocation", before_tokens=None, after_tokens=None,
                            delta_tokens=None, timestamp=event.get("timestamp"), counting_method=None,
                            source_id=item["id"], reason="native terminal MCP attempt, including failures (not token savings)",
                            metadata={"source": "native_rollout", "tool": item.get("tool"),
                                      "status": item.get("status"),
                                      "is_error": (item.get("result") or {}).get("isError"),
                                      "rollout_path": str(path)}))
                        from .cce_observation import native_event
                        from .savings import best_effort
                        observed = best_effort(native_event, store, sid, item, event.get("timestamp"))
                        failed = item.get("status", "").lower() != "completed" or bool((item.get("result") or {}).get("isError"))
                        timed_out = "timed out" in str(item.get("error") or item.get("result", {})).lower()
                        approval = "approval" in str(item.get("error", {})).lower()
                        result["events"][-1]["metadata"].update(
                            observed_pair_recorded=bool(observed), failed=failed, timed_out=timed_out,
                            observation_status=("approval required or rejected" if approval else "timeout" if timed_out else "failed" if failed else
                                "measured" if observed else "completed without a measured retrieval result"))
                        if observed:
                            result["events"].append(observed)
                if event.get("type") != "event_msg" or payload.get("type") != "token_count":
                    continue
                usage = (payload.get("info") or {}).get("total_token_usage")
                if not isinstance(usage, dict):
                    continue
                if not valid_usage(usage):
                    result["warnings"].append("Malformed token_count snapshot skipped; usage may be stale.")
                    continue
                total = usage.get("total_tokens")
                if (type(total) is int and previous_total is not None and total < previous_total):
                    raise ValueError("Cumulative usage decreased; cannot infer a session total")
                if type(total) is int:
                    previous_total = total
                result["usage"] = {key: usage.get(key) if type(usage.get(key)) is int and usage[key] >= 0
                                   else None for key in FIELDS}
                result["usage_source"] = "native token_count.info.total_token_usage (latest snapshot)"
                if native_usage is None:
                    result["as_of"] = {"timestamp": event.get("timestamp"), "ordinal": event.get("ordinal")}
        if native_usage is not None:
            # Different snapshots can appear briefly while the native writer
            # appends. Report the authoritative record, but expose disagreement.
            matches = all(native_usage.get(k) == result["usage"][k] for k in FIELDS
                          if result["usage"][k] is not None and native_usage.get(k) is not None)
            result["usage_integrity"] = "consistent" if matches and result["usage_source"] else "not_cross_checked"
            if not matches:
                result["usage_integrity"] = "snapshot_mismatch"
                result["warnings"].append("Native cumulative snapshots disagree; showing thread_token_usage, no inferred total.")
            result["usage"] = {key: native_usage.get(key) if type(native_usage.get(key)) is int
                               and native_usage[key] >= 0 else None for key in FIELDS}
            result["usage_source"] = "native token_usage_record.thread_token_usage (latest snapshot)"
        elif result["usage_source"]:
            result["usage_integrity"] = "single_cumulative_source"
        if result["usage_source"] is None:
            result["warnings"].append("Native rollout contains no cumulative usage snapshot.")
    except Exception as exc:
        result["usage"] = dict.fromkeys(FIELDS)
        result["usage_source"] = None
        result["usage_integrity"] = "unavailable"
        result["events"] = []
        result["warnings"].append("Native usage unavailable: " + type(exc).__name__ + ": " + str(exc))
    return result


def hook_anchor(store, payload, event):
    """Bounded read before the hook runs; never use clock time as a retry identity."""
    sid, location = payload.get("session_id"), payload.get("transcript_path")
    if not sid or not location:
        return None
    path = session_path(store, location)
    if not validate_rollout(path, sid, store.project):
        return None
    turn = None
    with path.open("rb") as handle:
        size = path.stat().st_size
        offset = max(0, size - 262144)
        handle.seek(offset)
        if offset:
            handle.readline()  # discard incomplete first line
        remaining = size - handle.tell()
        while remaining > 0:
            line = handle.readline(remaining)
            remaining -= len(line)
            if not line.endswith(b"\n"):
                break
            item = json.loads(line)
            if item.get("type") == "turn_context":
                turn = item.get("payload", {}).get("turn_id")
    if event != "SessionStart" and not turn:
        return None
    # Multiple resumes in one turn cannot be distinguished reliably. Coalesce
    # them conservatively rather than making duplicate notifications into savings.
    return {"session_id": sid, "turn_id": turn, "event": event,
            "source": payload.get("source"), "rollout_path": str(canonical(path))}

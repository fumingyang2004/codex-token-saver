"""Pinned CCE in-process observer and native-result attribution.

Only result metadata carries an opaque nonce. Source text never leaves the CCE
process for telemetry; reports join local counts to the actual native tool item.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import sys
import textwrap
import uuid

from .savings import METHOD, SavingsLedger, best_effort, count_tokens, digest
from .state import safe_path, write_json

PIN = "7243290192ee809185c31019a64be6fe6616e533e2d10b529df7c87b9250158d"
META = "codex_token_saver/cce_observation"
LIMIT = 16 * 1024 * 1024


def launch(store, binary, env):
    """Use the installed CCE's Python; never mutate its installed source."""
    if os.environ.get("CODEX_SAVER_CCE_OBSERVER") == "0" or not store.active("cce"):
        return None
    python = Path(binary).with_name("python.exe" if os.name == "nt" else "python")
    if not python.is_file():
        return None
    directory = store.directory / "cce-observations"
    safe_path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    env["CES_CCE_OBSERVATION_DIR"] = str(directory)
    code = "import sys;sys.path.insert(0,sys.argv.pop(1));from codex_token_saver.cce_observation import main;main()"
    return [str(python), "-c", code, str(Path(__file__).resolve().parents[1]), "serve", "--project-dir", str(store.project)]


def snapshot(chunks):
    rows = {}
    size = 0
    for chunk in chunks:
        raw = chunk.content
        if not isinstance(raw, str):
            return None
        size += len(raw.encode("utf-8"))
        if size > LIMIT:
            return None
        rows[id(chunk)] = raw
    return rows


def select(rows, inline):
    if rows is None or any(id(c) not in rows for c in inline):
        return None
    return [(str(c.id), rows[id(c)], c.compressed_content or c.content) for c in inline]


def wrap_result(result, selected, directory):
    """Return the identical text on failure; only successful metadata is added."""
    def observe():
        if selected is None or len(result) != 1 or result[0].type != "text":
            return result
        before = "".join(raw for _, raw, _ in selected)
        compressed = "".join(served for _, _, served in selected)
        after = result[0].text
        if max(len(before.encode()), len(after.encode()), len(compressed.encode())) > LIMIT:
            return result
        nonce = uuid.uuid4().hex
        a, b, c = count_tokens(before), count_tokens(after), count_tokens(compressed)
        event = {"protocol": "ces-cce-pair-v1", "nonce": nonce, "source_sha256": PIN,
            "before_tokens": a, "after_tokens": b, "delta_tokens": a-b, "counting_method": METHOD,
            "metadata": {"before_sha256": digest(before), "after_sha256": digest(after),
                "before_utf8_bytes": len(before.encode()), "after_utf8_bytes": len(after.encode()),
                "inline_chunks": len(selected), "chunk_ids": [ident for ident, _, _ in selected],
                "compressed_inline_tokens": c, "chunk_compression_delta": a-c,
                "returned_formatting_overhead_tokens": b-c, "pid": os.getpid(),
                "coverage": "actual returned inline raw chunks -> full returned text including overflow refs and hints; no discarded-candidate baseline"}}
        path = directory / (nonce + ".pending.json")
        write_json(path, event)
        block = result[0].model_copy(update={"meta": {**(result[0].meta or {}), META: nonce}})
        return [block]
    return best_effort(observe) or result


def install(directory):
    """Exact-source admission, additive in-memory patch, no retrieval changes."""
    from context_engine.integration import mcp_server as module
    path = Path(module.__file__)
    if hashlib.sha256(path.read_bytes()).hexdigest() != PIN:
        return False
    original = module.ContextEngineMCP._handle_context_search
    source = textwrap.dedent(inspect.getsource(original))
    replacements = [
        ("    all_chunks = await self._compressor.compress(all_chunks, self._config.compression_level)",
         "    _ces_raw = _ces_snapshot(all_chunks)\n    all_chunks = await self._compressor.compress(all_chunks, self._config.compression_level)"),
        ("    inline_chunks, overflow_chunks = _split_inline_overflow(all_chunks, max_tokens)",
         "    inline_chunks, overflow_chunks = _split_inline_overflow(all_chunks, max_tokens)\n    _ces_selected = _ces_select(_ces_raw, inline_chunks)"),
        ('    return [TextContent(type="text", text=body)]',
         '    return _ces_wrap([TextContent(type="text", text=body)], _ces_selected)')]
    for old, new in replacements:
        if source.count(old) != 1:
            return False
        source = source.replace(old, new)
    namespace = {**module.__dict__, "_ces_snapshot": lambda chunks: best_effort(snapshot, chunks),
        "_ces_select": lambda rows, inline: best_effort(select, rows, inline),
        "_ces_wrap": lambda result, selected: wrap_result(result, selected, directory)}
    exec(compile(source, str(path) + " [CES observation]", "exec"), namespace)
    module.ContextEngineMCP._handle_context_search = namespace[original.__name__]
    return True


def main():
    directory = os.environ.pop("CES_CCE_OBSERVATION_DIR", None)
    if directory:
        best_effort(install, Path(directory))
    from context_engine.cli import main as cli
    cli()


def nonce_from(result):
    content = result.get("content", []) if isinstance(result, dict) else []
    if len(content) != 1 or content[0].get("type") != "text":
        return None
    nonce = (content[0].get("_meta") or {}).get(META)
    return nonce if isinstance(nonce, str) and re.fullmatch("[0-9a-f]{32}", nonce) else None


def delivered(store, response):
    """Called only after the proxy successfully forwards the upstream response."""
    result = response.get("result")
    nonce = nonce_from(result)
    if not nonce or result.get("isError"):
        return
    directory = store.directory / "cce-observations"
    pending, target = directory / (nonce + ".pending.json"), directory / (nonce + ".json")
    safe_path(pending)
    safe_path(target)
    event = json.loads(pending.read_text(encoding="utf-8"))
    if event["metadata"]["after_sha256"] != digest(result["content"][0]["text"]):
        return
    event["request_id"] = response.get("id")
    event["delivered"] = True
    write_json(target, event)
    pending.unlink(missing_ok=True)


def native_event(store, sid, item, timestamp):
    """Join by opaque native-return nonce, never shared MCP environment identity."""
    if item.get("tool") != "context_search":
        return None
    result = item.get("result") or {}
    if result.get("isError") or item.get("status") not in ("Completed", "completed"):
        return None
    nonce = nonce_from(result)
    if not nonce:
        return None
    path = store.directory / "cce-observations" / (nonce + ".json")
    safe_path(path)
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if (data.get("nonce") != nonce or data.get("protocol") != "ces-cce-pair-v1"
            or data.get("source_sha256") != PIN or data.get("counting_method") != METHOD or not data.get("delivered")):
        return None
    a, b = data.get("before_tokens"), data.get("after_tokens")
    text = result["content"][0]["text"]
    if (type(a) is not int or type(b) is not int or a < 0 or b < 0 or data.get("delta_tokens") != a-b
            or b != count_tokens(text) or data["metadata"]["after_sha256"] != digest(text)):
        return None
    return dict(session_id=sid, event_id="cce-pair:" + nonce, component="cce", kind="observed",
        before_tokens=a, after_tokens=b, delta_tokens=a-b, timestamp=timestamp, counting_method=METHOD,
        source_id=item["id"], reason="same CCE request: returned inline raw chunks -> complete returned text",
        metadata={**data["metadata"], "request_id": data.get("request_id"), "native_item_id": item["id"],
                  "source": "native result nonce matched to local delivered observation"})


def persist_native(store, sid, rollout=None):
    from .session_usage import read_session
    if not store.active("cce"):
        return
    events = read_session(store, sid, rollout)["events"]
    ledger = SavingsLedger(store, sid)
    for event in events:
        if event["kind"] == "observed" and event["component"] == "cce":
            best_effort(ledger._append, "cce", event["event_id"], "observed", event["reason"],
                before=event["before_tokens"], after=event["after_tokens"], delta=event["delta_tokens"],
                source_id=event["source_id"], metadata=event["metadata"], method=METHOD)

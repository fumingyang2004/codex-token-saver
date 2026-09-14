# Architecture and migration

```text
Codex native hooks ── RTK single-execution observer ─┐
Codex native MCP ──── CCE context observer ──────────┤
                                                   SavingsLedger
Native session identity + cumulative usage ────────┤
                                                   ├─ codex-saver savings
                                                   └─ localhost API → vanilla dashboard
```

Only selected source modules were extracted from the prior efficiency stack:

| Module | Retained production capability |
|---|---|
| `rtk_observation.py`, `assets/rtk_observer.rs` | Single invocation raw buffer capture, final stdout/stderr, identity checks, fail-open observation |
| `cce_observation.py` | Exact-source in-memory CCE patch, selected inline raw chunks, complete returned text, nonce attribution |
| `session_usage.py` | Exact session/project rollout matching; validated native cumulative usage |
| `savings.py` | Approximate counter and SQLite ledger with idempotent event keys |
| `runtime.py` | State-guarded RTK execution and CCE MCP forwarding only |
| `state.py` | Atomic writes and safe path primitives; product store implemented anew |

New modules implement product-owned configuration, hooks independent of TO,
active PID/session registry, sanitized shared summaries, CLI, local web server
and installers. Old routing, agents, credits and experimental frameworks were
not migrated. The old repository was read-only and its Git history was not copied.

RTK source and original binary hashes must match the packaged observer manifest.
CCE's installed source must match its exact SHA-256 before observation is enabled.
CCE observations only enter the ledger after delivery and a matching native
completed MCP result nonce/body. Before is the returned inline chunks' raw text;
after includes the complete returned body, references and formatting. Discarded
search candidates and whole-repository size never serve as baselines.

SessionStart guidance is a signed negative CCE event. MCP metadata and control
messages are not claimed to be model billing tokens. Native session totals remain
unchanged. Duplicate events do not count twice; late confirmed native events can
be recovered while the product is disabled.

The local API uses an unguessable per-process token, rejects foreign Host/Origin,
has no CORS, serves an explicit static allowlist, and binds only 127.0.0.1.
Raw prompts/code/outputs are neither returned by the API nor stored in its ledger.
RTK's private capture is deleted after measurement; CCE telemetry holds counts and
hashes. Upstream CCE maintains its local code index and Codex owns its native logs.

# RTK sandbox accounting fix — 2026-09-14

The installed beta could rewrite Git commands and compress their output while
recording no RTK measurements. A native `workspace-write` diagnostic reproduced
`PermissionError` when `prepare()` tried to create an observation directory under
the installation's `projects/` directory. The same probe succeeded with full
access. Both probes reported non-TTY stdout/stderr; terminal detection was not
the cause of this incident.

The fix allocates a separate temporary spool in PreToolUse. The sandboxed wrapper
writes counts/hashes into that spool, then a host hook or dashboard poll imports
completed events into the durable ledger. Exact session/project identity and
host-issued nonces bind the spool to the command. Import is idempotent; a complete
marker prevents partial imports; raw observer buffers are deleted before import.
No extra writable roots or sandbox bypasses were added.

On Windows, the spool inherits the user's Temp ACL: Python 3.13's `mkdtemp(0700)`
instead creates a private DACL that excluded the restricted-token command in the
first validation attempt. Atomic writes now use a single exclusive UUID file
create. This avoids `tempfile.mkstemp` repeatedly retrying a sandbox permission
denial on Windows. Failed measurements retain an invocation and reason when the
spool is writable, without inventing a zero saving.

RTK's `git diff --stat` printer trims its tracked buffer. That exact leading/
trailing whitespace difference is accepted; accounting still counts the complete
actual emitted bytes, and unrelated output remains rejected.

## Verification

- 25 local tests passed, including sandbox-write separation, session isolation,
  idempotent import, incomplete/foreign observations, fail-open reporting,
  whitespace handling, and immediate permission-error propagation.
- Real Windows Codex CLI `0.154.0-alpha.6.2`, `workspace-write`, no hook-trust
  override, using the updated installed package. CCE was disabled for this
  bounded RTK-only test, and no model was asked to provide a wrapper.
- Final session: `01a09fcc-cd23-7d70-88c1-217253cb3b83`.
- Four separate native commands each completed with exit code 0. The ledger
  records 4 calls, 3 measured events, and 1 unmeasured empty diff:

| Command | Before | After | Approximate reduction |
|---|---:|---:|---:|
| `git status --short` | 4 | 4 | 0 |
| `git diff --stat` | 15 | 14 | 1 |
| `git diff` | 93 | 89 | 4 |
| `git diff --cached` (empty) | unknown | unknown | unknown |

RTK total: **5 approximate tokens**. This is a small functional fixture, not a
benchmark or an estimate of whole-session billing savings. Previous failed and
diagnostic runs are excluded from these numbers. The earlier real-session smoke
used full access and therefore did not exercise this sandbox failure.

Changes were applied to the local checkout and the local installation with an
original-file backup. The published beta release/tag has not been replaced.
Linux native sandbox behavior and IDE integration were not validated in this fix.

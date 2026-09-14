Fixes for RTK measurement in sandboxed Codex sessions and preservation of pytest diagnostics.

- Fix missing RTK measurements under Windows workspace-write: commands collect in per-invocation temporary spools, and host hooks import completed records into the session ledger. No additional sandbox writable roots are required.
- Avoid repeated Windows permission-error retries during atomic writes.
- Distinguish RTK calls, measured events and unmeasured results in the dashboard. Missing observations remain unknown.
- Handle git diff --stat whitespace without dropping an otherwise valid measurement.
- Leave pytest diagnostic commands such as --trace-config, --collect-only, --fixtures, --help and --version untouched. Their requested diagnostic information must not be reduced to a test-result summary.
- Select the exact release version during relocated installer validation.

Validation: 43 local tests passed. The RTK sandbox fix was also checked in a real Windows Codex CLI workspace-write session: four Git calls, three measured events and one unmeasured empty diff; CLI and dashboard API agreed. Diagnostic bypass is covered by regression tests. Windows/Linux release jobs additionally build, test, package and check relocated installation.

Counts are approximate observed text reductions, not credits or whole-session billing savings. In beta.1, the large reduction from pytest --trace-config discarded diagnostic information and should not be interpreted as useful savings; existing historical measurements are not rewritten.

This remains a prerelease for root CLI sessions. IDE/subagent attribution is not promised. Compound commands still pass through. Codex 0.154+ and trusted native hooks are required.

Upgrade: download the archive for your platform, extract it, and rerun its installer using the existing install location. Restart existing Codex sessions and reopen the dashboard with codex-saver ui. Configuration ownership/backups are preserved. The previous v0.1.0-beta.1 release remains available.

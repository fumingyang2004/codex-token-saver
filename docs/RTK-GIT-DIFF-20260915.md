# RTK Git diff repair — 2026-09-15

The native session `01a0a39d-2fc2-7421-8869-050a97cd3a26` proved the hook executed
RTK in the correct `C:\Lab0908` directory. RTK 0.48.0 then discarded stdout when
Git returned 1 for differences. The dashboard also counted every nonzero exit as
a failure. This was an execution/output bug, not a missing hook.

## Changes

- The pinned RTK build now preserves stdout, stderr and the original exit code
  for `git diff --no-index`, implicit file comparisons, `--exit-code`, `--stat`,
  `--quiet`, and `--check`. Errors preserve diagnostic output as well.
- Compact diff accepts code 1 with no error diagnostics and continues filtering.
  Missing paths also return 1 on this Git version, so unknown/error stderr remains
  a failure. Known `warning:` lines alone do not make differences an error.
- `--stat`, quiet output and diagnostic output are measured as actual passthroughs,
  including empty captures. Before includes captured stdout and stderr; after
  includes everything emitted. No second execution is used as the savings baseline.
- The failure summary honors explicit command outcome metadata. `--check` remains
  an error when it finds whitespace defects. Exit code 1 is never changed to 0.
- Managed observer calls suppress RTK's unrelated Claude Hook installation hint.
- If the patched observer cannot start, Git diff falls back to native Git before
  execution, avoiding the output-dropping original RTK path. No command is retried
  after execution.

The reproducible changes live in `scripts/build_rtk_observer.py`,
`assets/rtk_git_diff.rs`, and `assets/rtk_observer.rs`. The upstream source remains
pinned to `fde0a8f185945556f51718de0f4c430bb62b3df6`; the generated patch and binary
hash manifest describe the modified build. The separately installed original
`engines/rtk.exe` is unchanged.

## Verification

`python -m pytest -q`: **63 passed**. New coverage includes nine real Git/RTK
hook-wrapper cases and outcome classification: ordinary folders, a repository,
identical files, missing paths, whitespace checks, quiet mode and native fallback.
Hook payloads/session headers are synthetic; this is not a fresh native Codex run.

Read-only comparison of the real `MATH500/report_ui` and `OlymMATH/report_ui`
directories, repeated using the installed Python package:

| Command | Before | After | Saved | Failed | Pending |
| --- | ---: | ---: | ---: | ---: | ---: |
| `git diff --no-index --stat -- MATH500/report_ui OlymMATH/report_ui` | 166 | 166 | 0 | 0 | 0 |
| `git diff --no-index -- MATH500/report_ui OlymMATH/report_ui` | 2565 | 2550 | 15 | 0 | 0 |

Both commands retain exit code 1. Stat stdout/stderr exactly match native Git.
Stat includes `2 files changed, 32 insertions(+), 6 deletions(-)`.
Counts use `ceil(UTF-8 bytes / 4)`, not model billing. The comparison is small, so
compression savings are small. Synthetic test data and observations were isolated
from the user's session ledger in `.work/rtk-lab0908-fixed` and
`.work/rtk-lab0908-installed`; each contains `result.json`.

## Local deployment

- Backup: `%LOCALAPPDATA%\CodexTokenSaver\backups\rtk-gitdiff-20260915\codex_token_saver`.
- Wheel: `.work/rtk-gitdiff-wheel/codex_token_saver-0.1.0b2-py3-none-any.whl`.
- Wheel SHA-256: `93a1bc5c24910fac097332a315b358c660b3276556bd29a630a605669884e673`.
- Observer SHA-256: `ed277b609825265f5c36d47acd31be5a8d0ca10f307a9daae84d866311c6c129`.
- Installed with dependencies unchanged; dashboard restarted. Fresh hook-launched
  RTK commands load the new package without restarting the Codex conversation.
- Existing historical events are not rewritten: previously lost outputs cannot
  be credited retrospectively, and legacy failure metadata remains historical.
  A fresh session gives clean counters for native acceptance testing.
- No GitHub release or commit was made. The version remains a locally repaired beta.2.

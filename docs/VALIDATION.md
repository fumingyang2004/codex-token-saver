# Beta acceptance

## Real Windows session

Installed wheel from a release archive in a relocated temporary path containing
spaces and Chinese characters. The installed runtime imports no development
checkout. The independently downloaded CCE model indexed a small acceptance
fixture (4 chunks / 3 files).

Session: `01a09f68-0e10-7051-baa8-2f350b5026bf`

| Observation | Before | After | Net avoided |
|---|---:|---:|---:|
| RTK, one ordinary pytest command | 3,809 | 5 | 3,804 |
| CCE, one native context_search | 1,280 | 208 | 1,072 |
| CCE session guidance | 0 | 39 | -39 |
| **Session total** | | | **4,837** |

CCE net: **1,033**. Native usage: input **64,337**, cached input **59,648**,
output **197**, reasoning output **0**, total **64,534**. Cached input is a subset
of input. These native values are separate from approximate savings.

The native task invoked plain `pytest test_long_output.py -vv`, without an agent-
supplied wrapper. The hook transformed it; 120 tests passed and the command
executed once in this run. One native CCE call completed with a matched nonce.
There were 50 samples while the native Codex process was alive. The UI updated
from RTK-only accounting to both components while that session remained active.
The screenshot is from that live UI. Final CLI and API summaries matched exactly.
Sanitized numbers: [real-smoke.json](real-smoke.json).

The initial attempt found a Windows quoted-path hook launch defect. That failed
run and short diagnostic probes were excluded from the successful session above.
Encoded PowerShell hook launch fixed it; no old repository files were changed.
The pytest execution marker had two entries across the failed initial task and
the successful task, confirming no replay within either task.

The bounded smoke used native `exec`, an isolated Codex home and a temporary copy
of existing authentication, removed afterwards. Its generated hooks were reviewed
by this implementation task and run with the native one-invocation hook-trust
override. Normal installers never grant hook trust; users review `/hooks` once.

## Product checks

- Windows and Ubuntu 22.04 CI: unit tests, CLI, localhost API, platform observer
  build, archive scan, relocated installation, doctor, enable/disable/uninstall.
- CI's Codex version executable is a fixture, explicitly not an authenticated
  Linux model conversation. The real authenticated smoke above ran on Windows.
- API checks reject missing tokens and foreign Host; repeated UI starts reuse
  the same server. No active session, old session and ended session are covered.
- Accounting checks cover negative overhead, duplicate events, session isolation,
  native CCE delivery and body matching, and unsupported commands passing through.
- Archives are scanned, including expanded wheel contents, for developer path
  markers. RTK's build uses source-path remapping. No session data or credentials
  are packaged in the install archives.

## Limits

This is a functional acceptance fixture, not a savings benchmark. First CCE model
download/indexing needs network/time. Linux native model usage and fresh machines
were not exercised locally. On Windows, removing Python from PATH exercised
the automatic Python 3.13.7 provisioning branch. The installer suppresses uv
global executable and registry installation. Managed policies,
IDE integration, subagents and unsupported CLI versions can prevent hooks.

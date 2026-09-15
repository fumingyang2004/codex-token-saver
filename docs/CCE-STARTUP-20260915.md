# CCE source-first startup repair (2026-09-15)

## Upstream investigation

Official repository: https://github.com/elara-labs/code-context-engine

- [Issue #67](https://github.com/elara-labs/code-context-engine/issues/67): first-run
  model download/cache problems and silent lazy indexing; closed through
  [PR #70](https://github.com/elara-labs/code-context-engine/pull/70).
- [Issue #66](https://github.com/elara-labs/code-context-engine/issues/66): orphaned
  embedding workers and watcher-triggered redundant work, primarily on WSL.
- [Issue #139](https://github.com/elara-labs/code-context-engine/issues/139): severe
  multi-instance load. [PR #142](https://github.com/elara-labs/code-context-engine/pull/142)
  added a resource governor. Its documented file lock is a no-op on Windows.

These reports establish related upstream problems, not proof of the same cause
on this machine. The installed version remains 0.4.26. Its resource governor sets
thread environment variables, but a real local FastEmbed session still reported
`intra_op_num_threads=0` (automatic) with those variables set to 2. Passing the
actual FastEmbed constructor parameter produced ORT intra/inter-op settings 2/2.

## Local findings

The upstream scanner selected 3,560 files under `C:/Lab0908`. Of these, 3,518 were
under the two benchmark `results` trees (397,907,027 bytes). This is candidate
volume, not a claim that all bytes were embedded: upstream separately skips files
over 2 MB. Many generated JSON/JavaScript artifacts were below that limit and
therefore still eligible. The raw-answer and report-data directories dominated
the candidate set.

The previous adapter had two additional defects: queued work overwrote the active
indexing phase with `waiting_for_index_lock`, and all searches were deferred while
indexing even when committed chunks were already available. Upstream's 50-file
batch also delayed the first queryable commit while an entire large batch embedded.

## Repair

- Exclude `**/report_data/**` and `**/results/**/raw/**` by default; also exclude
  `.work` and `.test-runtime` directories. Preserve `.cceignore` exclusions.
  These files are unchanged and remain accessible with native tools. This is an
  explicit source-oriented index scope, not lossless indexing of the whole disk.
- Process source/config files before result artifacts, and commit 8-file batches.
  This adapts only the exact pinned upstream pipeline in memory; it does not edit
  the installed CCE source or change its embedding/retrieval algorithms.
- Set FastEmbed's real thread parameter to 2. Serialize jobs before acquiring the
  cross-process index lock; queued work cannot overwrite active progress.
- Report processed files, embedding chunks, committed chunks and queued jobs.
  Allow searches against committed chunks while the remaining index builds.
- Reuse a warm index immediately and check offline edits incrementally in the
  background on first use. Use a policy-specific, project-private index namespace
  so old generated-artifact rows cannot silently persist. Old indexes are retained.

Optional product-owned `settings.json` settings (not project source files):

```json
{
  "cce_index_policy": {
    "threads": 2,
    "exclude_patterns": ["**/report_data/**", "**/results/**/raw/**"],
    "exclude_directories": [".work", ".test-runtime"]
  }
}
```

Empty exclusion lists opt those files back in. Changing the exclusion policy uses
a distinct index namespace and requires a one-time index. Upstream `.cceignore`
does not support negation; do not use `!pattern` as an inclusion override.

## Real-project validation

No benchmark source files were edited. Separate private index stores used copies
of already-downloaded weights. No paid model conversation was used for this test.

| Run | First returned code | Other evidence |
|---|---:|---|
| Source filter + original 50-file batches | First search after full index: 97.300 s from launch | Cold probe, no early-partial search attempted |
| Source filter + 8-file batches | **9.830 s from process launch** | Returned `OlymMATH/config.py` while indexing continued |
| Restart with completed index | **5.508 s from process launch** | Search itself: 3.701 s |

The final cold run selected **57 files**. Its pipeline completed in approximately
61 seconds, with **338 committed vector chunks** observed by index_status. The
probe's full sequence, including initialization, polling and final search, took
69.123 seconds. First-batch publication was observed at 5.7 seconds. These are
single local measurements, not medians, credit savings or cross-machine promises.

Evidence (ignored local files):

- `.work/cce-lab0908-source-v1/result.json`
- `.work/cce-lab0908-source-batch8/result.json`

53 tests passed, including policy opt-out, source preservation, real thread-argument
forwarding, queued-work phase correctness and partial-index searching. The real
ORT SessionOptions were additionally checked at 2/2.

The old Lab0908 CCE child tree was stopped to end wasted indexing; the Codex
conversation itself was retained. Restart its MCP connection or open a new Codex
session after installing this repair. Source, original indexes and telemetry are
preserved. A successful validated index can be staged in the installed product's
new namespace without deleting its old index.

Deployment completed locally: the installed package was backed up under
`%LOCALAPPDATA%/CodexTokenSaver/backups/cce-source-startup-20260915`, then updated.
Only index databases, the embedding cache and manifest were staged into the new
Lab0908 namespace; probe sessions, query logs, memory and savings statistics were
not copied. A fresh **installed-package** MCP proxy returned real project sources
in **5.217 seconds** from process launch, including both benchmark `config.py`
files. GitHub release artifacts were not changed.

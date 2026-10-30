# Public core mirror: 1134-entry snapshot

## Snapshot and inventory

Frozen source refs fetched for this publication:

- Fork tip: `3c5a9b706d469de90c8a876a7cdcb3f84b90a7ac`.
- Upstream exclusion tip: `088f657ecf77674c7b8e2ededf4e3576d82e042c`.
- Their merge-base is also `088f657ecf77674c7b8e2ededf4e3576d82e042c`.
- Inventory: 1134 non-merge source commits and 1134 uniquely numbered mail patches, `0001` through `1134`.

All source SHA headers were checked against the frozen source log using `verify_mirror.inventory`. The previous directory contained 1067 entries, but this was not an append-only backlog: 67 source commits were missing, including upstream-integration fixes inserted earlier in log order. Matching by source SHA renumbered 87 existing entries. Existing feature references were remapped simultaneously rather than shifting numeric strings repeatedly. Eighteen new feature documents cover the 67 previously absent source commits.

The old process-exit logging commit remains present as 0620, with `process-exit-log-noise.md`; it is not a new duplicate entry.

## Applying and integration boundary

Executed a fresh, single-command replay in a detached worktree at the frozen merge-base:

```sh
git am -3 --empty=keep /path/to/core/patches/[0-9][0-9][0-9][0-9]-*.patch
```

Result: exit 0 for all 1134 mail patches, with no manual resolution during that final verification run. Whitespace warnings and automatic three-way fallback are not failures. Empty historical entries are retained to preserve the exact source inventory.

This is a curated linearization, not 1134 untouched source diffs. The previous curated series targeted an older upstream snapshot. Preparatory replay on the current base found hundreds of context conflicts, starting at 0001. Upstream-side conflict content was retained while nonconflicting hunks were applied; rejected historical context was excluded from that intermediate entry. Those choices alone do not establish source equivalence.

Entry 1134 therefore includes an explicit final integration overlay restoring the frozen fork's complete tree after the historical replay. It contains the session-copy feature plus historical merge-context reconciliation. It is not a small independent session-copy patch, and intermediate trees are not claimed equivalent to their corresponding source commits. Consumers wanting the final build must apply the complete series. Feature documents describe the source implementation, not a promise that one adapted historical entry independently installs that feature.

## Final-tree audit

After replay, audited all committed paths, modes, object kinds and content against the frozen source tree:

- Source paths: 22787.
- Applied paths: 22787.
- Content normalization: 26 files with explicit public/example substitutions or scanner-safe test-fixture spelling.
- Path normalization: three contributor-marker filenames use public/example identities.
- Applied tree: `490fd10c7ade67824d022f736eb3161e60967b2a`.
- Result: exact match after those enumerated normalizations; no unexplained blob, mode or path difference.

The audit independently transformed each original source blob by its recorded per-file substitution list and compared it with the replayed blob. It also compared the complete path inventory under the three explicit filename mappings. Upstream-only files and upstream literals already present in fork-modified files were not broadly rewritten: standard network ranges, service endpoints and unrelated upstream fixtures retain their semantics.

The task-local normalization manifest contains private source literals and is deliberately not published. The public `verify_mirror.py` inventory checker was used directly. Its complete `verify` command was not claimed to pass: its current policy does not support normalized filenames, whereas this publication retains the existing public contributor-marker anonymization. This report records the separate full-tree audit rather than representing a partial verifier invocation as full verification.

## Executed tests

Ran the canonical runner on the freshly replayed public candidate with the existing test interpreter:

```sh
HERMES_PYTHON=/path/to/existing/test-python bash scripts/run_tests.sh \
  tests/hermes_cli/test_sessions_branch.py \
  tests/hermes_cli/test_branch_command.py \
  tests/agent/test_step_boundary_pause.py \
  tests/tui_gateway/test_interrupt_reason.py \
  tests/hermes_cli/test_kanban_origin_owner.py \
  tests/tui_gateway/test_durable_takeover_recovery.py \
  -j 2 --file-timeout 180
```

Result: 6 files, 62 passed, 0 failed. The new standalone session-copy file passed three tests; the existing interactive branch file passed nine. Four additional files exercise the scanner-normalized fixtures: step-boundary pause (4), interruption reasons (6), origin-owner routing (36), and durable takeover recovery (4). Those fixture normalizations shorten dummy credentials and preserve harmless parentheses; they do not remove assertions or change expected behavior. The runner used clean-environment per-file subprocesses. These tests cover the standalone command, its shared row builder's existing interactive regressions, and the four normalized fixture files, not a live model probe or a mounted Desktop sidebar.

Other feature docs identify tests from source patches and explicitly distinguish those references from fresh execution. No full repository suite, Desktop suite, typecheck, live gateway restart or GUI validation is claimed.

## Failure analysis and prevention

Observed problem: the initial replay stopped at 0001 with completion-sound conflicts; the fetched log also showed source SHA mismatches at 87 existing ordinals.

Why it happened: the prior mirror was a curated linearization against an older upstream base, while the refreshed fork incorporated newer upstream code and additional non-merge integration fixes. Evidence: the changed merge-base, source-header comparison and first-patch three-way conflicts. Raw non-merge exports do not encode source merge resolutions; existing curated context cannot simply be carried to a new upstream base.

Why a filename/count check alone would not catch it: an equal-sized or contiguous directory establishes neither source identity nor final runtime content. The coverage checker only checks documentation rows, and a successful replay can still retain an upstream conflict side that lacks a fork behavior. The earlier mirror reconciliation already documented this class of failure.

Countermeasures in this publication: frozen source IDs, SHA-based inventory/remapping, preparatory context reconciliation, explicit final source-tree restoration, a fresh full-series replay, complete normalized source-tree comparison, and targeted branch tests. The original apply failure was caught before publication. No broader runtime behavior claim is inferred from documentation coverage.

## Privacy and documentation checks

The source author identity is replaced with the public noreply identity. Public patches retain example app/user paths and fixture identities. The session-copy regression uses an example transcript and session identifier rather than a real conversation. The complete patch series and new documentation are checked with the private denylist, including context/removal lines, and the normal public-repository pre-commit secret/privacy/coverage hook is required without bypass.

`core/README.md` is the current inventory index. Older snapshot reports remain historical evidence, not the current base or patch count.

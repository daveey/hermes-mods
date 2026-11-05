# Mirror 2005 verification

This extends the [fully verified 2004 snapshot](mirror-2004-verification.md) by one source commit, `221510a25b32673c6f678f383ce15201b6f1c444`, whose parent is exactly the frozen 2004 tip. Patch 2005 preserves its complete diff under the existing public normalization and author-header rules. Its [feature doc](kanban-sync-timing-due-cohorts.md) describes component-evidenced due-report cohorts, not a latency acceptance verdict.

## Inventory, replay and tree

The 2005 policy/replay files change only their `source_tip` bindings from the 2004 files. The upstream exclusion base, all 21 bridge hashes, three ancestry-only entries, substitutions, renames and fingerprints are unchanged.

The README Applying Bash recipe was extracted and executed unchanged, including the full unchanged verifier command with `mirror-policy-2005.json`, `mirror-replay-2005.json`, source tip `221510a25b32673c6f678f383ce15201b6f1c444` and base `84a0301415edd6d1385207ec96e4d7f2b6c1fcd9`. It exited **0** and printed:

```json
{"ancestry_only": 3, "base": "84a0301415edd6d1385207ec96e4d7f2b6c1fcd9", "bridges": 21, "merge_base": "e26e0e4b67bfae31eddaaefee87c062efd67429b", "patches": 2005, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "221510a25b32673c6f678f383ce15201b6f1c444"}
```

The resulting README-applied tree at `193229c752e8ee59bf6964f80470269800b67397` separately passed `verify_mirror.compare` against the frozen source under the same policy. No published patch or checker was rewritten. The README's final slice is now `2000:5`, including the new patch without changing bridge boundaries.

## Executed tests

On both the exact source and README-applied trees, `scripts/run_tests.sh -j 1` with isolated HOME and retries disabled ran:

- `tests/hermes_cli/test_kanban_sync_timing.py`
- `tests/hermes_cli/test_kanban_sync_timing_loop.py`
- `tests/hermes_cli/test_kanban_sync_timing_report.py`

Result: **71 passed, 0 failed per tree**, including all six new cohort cases. The preceding 2004 snapshot's separate 20-file, 410-pass-per-tree result is preserved in its report; it is not represented as a full 2005 retest. Desktop TypeScript, native platforms, live GUI and a new live timing capture remain untested here.

## Publication scope

The 2004 independent review covered all new bridges and the existing comment-only fingerprint re-pin. Patch 2005 adds no new bridge, exemption or privacy rule. Final staged coverage, documentation whitespace and the full pre-commit gate exited **0**. The private denylist found zero hits in all **36** staged files; gitleaks passed with only the line-scoped upstream-fixture exceptions documented in the 2004 report.

Diagnostics remain off by default, with the existing 600-second cap. Latency acceptance is **unproven**, with no matched legacy baseline. This mirror neither enables diagnostics nor accepts the rollout.

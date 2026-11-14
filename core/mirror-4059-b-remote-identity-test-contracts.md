# Remote task and completion identity contracts

Patches: 3784, 3800.

## Behavior

These test-only patches protect two remote-reader contracts: this gateway's own cards are excluded when requested, and a legacy completion receipt cannot be borrowed from a different gateway just because the numeric run ID matches.

## How it works

The task-row fixture builds SQLite replicas with and without `seq`. Its indexed case deliberately makes planner order differ from rowid order, then compares `_task_rows` against the same identity query used by warm reads. The completion-sync fixture places a later receipt from another origin beside a pre-UID same-run replay; only the replay origin's receipt may bind.

Production entry points under test are `tui_gateway.kanban_remote_notices._task_rows`, `tui_gateway.kanban_remote_observations._snapshot` and the completion-ref sync reader. These mails add tests, not new filtering or receipt logic.

## Code

Test-only changes; production code is not modified by these mails. The targeted core contracts are described above.

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/tui_gateway/test_task_rows_own_gateway_and_order.py` (patch 3784).
- `tests/hermes_cli/test_kanban_completion_refs_sync.py` (patch 3800).

- **3784:** Direct `_task_rows` calls exclude the supplied own gateway but include all gateways when omitted; the no-seq snapshot keeps only the remote card; indexed cold reads equal planner order and the fixture proves that order differs from rowid order.
- **3800:** With equal legacy numeric run IDs and a later foreign receipt, completion replay still binds this gateway’s own receipt and verifies both completion flags.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

Planner order is not an explicit rowid sort. The no-`seq` observation path relies on the upstream row reader to exclude local cards. Numeric run IDs alone are not globally unique.

## Plugin?

No. These are regression fixtures around core replica SQL and completion reconciliation, not a new extension hook.

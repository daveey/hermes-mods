# Conservative sync repair, lease custody and UDF budgets

Patch coverage: 6186, 6187, 6205, 6244, 6245, 6246, 6336, 6346, 6347, 6372.

## Behavior

Replica pulls can repair historical containment-closure proof that an older cursor already passed, using fresh canonical evidence and the normal protected apply. A refused optional repair does not poison unrelated feed progress.

A refused metadata-only classification can be retired locally only when exact canonical absence and the still-current containment episode prove it was never applied. Foreign lease custody remains fenced until a canonical clear, even when its timestamp has elapsed.

## How it works

Closure backfill works in bounded batches, skips quarantine, retries certification-before-projection crashes and schedules individual refusals. It never treats the local row or retry ledger as disposition authority. Classification retirement preserves the original rejected operation in an immutable receipt, including evidence in its digest, and removes only the exact rejected queue entry; source event/task/hold bytes remain unchanged. Producers now refuse contained classification before creating such residue, after request validation.

Gone-board handle sweeps have an explicit cadence and react to board-list changes. Probe/CPU diagnostics observe executed work without reusing answers. SQLite progress-handler interruption inside a containment UDF is retained explicitly so apply can reduce its batch; unrelated UDF errors are not relabeled as budget expiry. Round warnings consume bounded string-only inner diagnostics and clear them between failures/boards.

## Code

Affected implementation and integration paths in the source series:

- `hermes_cli/kanban_containment_functions.py`
- `hermes_cli/kanban_human_request_migration.py`
- `hermes_cli/kanban_rejected_containment_event.py`
- `hermes_cli/kanban_remote_stop.py`
- `hermes_cli/kanban_sync_apply_budget.py`
- `hermes_cli/kanban_sync_board_paths.py`
- `hermes_cli/kanban_sync_board_watch.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_closure_backfill.py`
- `hermes_cli/kanban_sync_outbox_age.py`
- `hermes_cli/kanban_sync_probe_diag.py`
- `hermes_cli/kanban_sync_rejections.py`
- `hermes_cli/kanban_sync_sweep_cadence.py`
- `hermes_cli/machine_metrics.py`
- `plugins/kanban/dashboard/plugin_api.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/hermes_cli/test_kanban_sync_closure_backfill.py`
- `tests/hermes_cli/test_kanban_rejected_containment_event.py`
- `tests/hermes_cli/test_kanban_sync_sweep_cadence.py`
- `tests/hermes_cli/test_kanban_remote_lease_status_writes.py`
- `tests/hermes_cli/test_kanban_sync_udf_budget.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

Neither repair nor queue retirement is a release or replay of rejected intent. Uncertain absence, changed source bytes or a different episode retains the rejection. Timestamp expiry is not foreign-lease release. Diagnostics are nonadditive observations, not authority or cached query results.

## Why core, not a plugin?

Only core sync can atomically combine canonical proof, outbox identity, apply budgets, containment fences and feed cursor progress.

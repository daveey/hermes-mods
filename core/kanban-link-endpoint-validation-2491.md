# Internal link endpoint validation

Patches: **2440**.

## Behavior

Internal writes to the task graph (`kanban_db._link`) now refuse a link whose parent or child task does not exist, raising the unknown-task `ValueError` from `missing_dependency_error`. The refusal happens inside the caller's transaction, so no link row and no accompanying event are left behind. Endpoints created earlier in the same uncommitted transaction are accepted, and repeating an identical link is still a no-op.

## How it works

`_link` delegates to `kanban_dependency_validation.insert_link`, which calls `_missing_task_ids` for both ids, raises if any are missing, and otherwise runs `INSERT OR IGNORE INTO task_links`. Cross-board dependencies still use explicit waits, and sync imports keep their own replica-aware ingestion path, so they do not create local task rows through this check.

## Code

`hermes_cli/kanban_db.py`, `hermes_cli/kanban_dependency_validation.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_link_insert.py`. It covers the internal `_link` path inside a write transaction (missing parent, child or both, with atomic rollback; uncommitted endpoints; exact retry). Other callers of `_link` and sync import are untested by this file.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

Callers that previously linked ids not yet present now fail; create both tasks first (same transaction is fine). This check is separate from the cycle check (`would_cycle`).

## Plugin

Core change; no plugin required.

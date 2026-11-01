# Composable metadata-only ask classification

## Behavior

Patch **1474** (`e572a3832043fdfc39bd5e43685e666852f94989`, PR #487) lets an orchestrator compose parked-card human-request classification with an eligibility check and an audit comment in one transaction. It changes metadata and events, not status, run identity or block counters. An outer rollback undoes both classification and audit writes.

## How it works

`classify_parked_request` uses `db.write_txn(conn, allow_nested=True)`. Existing actor/reason, worker and execution-state checks remain; the metadata/event-only operation has no post-commit side effects, so the enclosing transaction owns commit or rollback.

## Code

- `hermes_cli/kanban_human_request_migration.py`
- `tests/hermes_cli/test_kanban_human_request_entries.py`

## Tests

`test_classification_and_comment_share_outer_transaction`, parametrized for commit and rollback, asserts comment/event counts and the complete task snapshot. Commit changes only `human_request`; rollback preserves the original snapshot and event/comment counts. The implementation handoff reports both new cases RED before the fix and 44 tests passing afterward, with exact-head required aggregate CI green before merge. This mirror inspected those assertions and executed full inventory/replay/tree verification; it did not rerun the source test suite.

## Applying

Use the full series and bridge-aware recipe in [README.md](README.md). This is patch 1474, not a standalone plugin installation.

## Gotchas / plugin?

This core seam supports plugin-owned reconciliation that must atomically record a metadata-only ask and its audit. It does not itself implement terminal expiry reconciliation or change plugin files. Plugin integration and live expiry behavior are outside this mirror's verification boundary.

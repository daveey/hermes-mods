# Real unknown-scope producer confirmation

## Behavior

Patch 2017 adds regression coverage, not production behavior changes:
- A running remote producer whose task scope is unknown is accepted by real `confirm_remote`, but its wait is never treated as completed.
- A done producer is rejected with `producer_done` by real confirmation logic.

## How it works

The tests exercise the confirmation implementation rather than replacing `confirm_remote` with a stub. They extend the unknown-scope coverage introduced in [patch 2016](mirror-2016-features.md).

## Code

Source commit `a9aa1aa4e4aeca6c6dba90b84f3446e825d999bb` changes only `tests/hermes_cli/test_kanban_wait_replica_unknown_scope.py` (+46 lines).

## Tests

On both the source tree and the replayed public tree, the canonical runner with `-j 1 --file-retries 0` passed:
- `test_kanban_wait_replica_unknown_scope.py`: 27 tests (including the real confirmation paths).
- `test_kanban_wait_replica.py`: 31 tests.
- `test_kanban_offline_backoff.py`: 17 tests.

All paths above are under `tests/hermes_cli/`. See [verification](mirror-2017-verification.md). No live gateway, Desktop, or full-suite behavior is claimed by this mirror pass.

## Applying

Use the complete [README Applying recipe](README.md#applying), including the 2017 policy and replay plan. Patch 2017 follows 2016 directly; no new merge bridge is required.

## Gotchas

Unknown scope is not proof of completion. These tests pin existing behavior; they do not repair or alter production confirmation logic.

## Plugin?

Not applicable: regression tests for core Kanban behavior, not a new feature or plugin.

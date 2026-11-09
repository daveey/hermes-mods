# WAL replica heartbeat test fixture

Patch **2060**. Companion to [replica read memoization](kanban-replica-read-memo.md).

## Behavior

Replica heartbeat tests model production WAL concurrency: a reader continues seeing the last committed heartbeat while another connection holds an uncommitted write, then sees the committed change. Production replica code is unchanged.

## How it works

The shared `hub` fixture sets `PRAGMA journal_mode = WAL` before creating its replica schema. A regression holds `BEGIN EXCLUSIVE`, updates the gateway heartbeat row, reads before commit, commits, and reads again. WAL permits the pre-commit read; rollback journaling would impose different exclusion behavior.

## Code

- `tests/hermes_cli/test_kanban_replica_read_memo.py`: shared fixture.
- `tests/hermes_cli/test_kanban_replica_read_memo_heartbeat.py`: deterministic held-writer test.

## Tests

**Not rerun this pass.** `tests/hermes_cli/test_kanban_replica_read_memo_heartbeat.py::test_reader_answers_exactly_while_heartbeat_writer_is_uncommitted` exercises the replica reader against a real temporary SQLite database and second writer connection. The existing `test_concurrent_reads_during_heartbeat_churn_answer_exactly` also uses the corrected fixture. No live publisher/replica transport is exercised by this change.

## Applying

Apply 2060 through [the core replay](README.md), with the existing read-memo/heartbeat test helpers.

## Gotchas

This does not migrate production databases or eliminate all SQLite contention. It removes a test fixture's journaling mismatch rather than relaxing the reader's result assertions.

## Plugin eligibility

Not applicable: test fixture and regression only.

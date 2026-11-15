# Per-task Kanban change cursors on boards and shadows

Mirror snapshot 4059; covered ordinals: 3911, 3912, 3913.

## Behavior

Readers can ask which task IDs changed since a cursor and refresh only those tasks. If coverage or generation evidence cannot vouch for an incremental answer, `changed_since` returns `FULL` instead. Shadow stores cover task links, runs, events, comments, and chat links as well as task rows; excluded heartbeat/bookkeeping changes remain excluded.

## How it works

`hermes_cli/kanban_activity_revision.py` installs epoch-2 triggers that bump the board revision and mark each affected task at that revision. Links mark both ends; moved foreign keys mark old and new tasks; deletes mark the disappearing task. Trigger installation adds the empty-string `FULL_MARK` to invalidate older cursors. Newer-epoch trigger sets are left untouched. A sync-generated initial event UID stamp is not treated as a new content change.

`hermes_cli/kanban_task_change.py:changed_since` checks trigger coverage, `MAX(rev) == revision`, nonregression, main-file and shared-memory identities, schema version, watched tables, and absence of a newer full marker in one read snapshot, then checks identity again. A valid store can supply a fresh cursor even when this call requires a full read.

Patch 3912 replaces `INSERT OR REPLACE` marks with `ON CONFLICT(task_id) DO UPDATE`, preventing an outer statement's conflict policy from suppressing marks or raising uniqueness failures. Patch 3913 makes newly created change tables ordinary rowid tables so generic board snapshots can order by rowid. The benchmark script measures committed-write overhead and single-task change-read scale; its historical numbers are not fresh results here.

## Code

- `hermes_cli/kanban_activity_revision.py`
- `hermes_cli/kanban_task_change.py`
- `scripts/benchmark_kanban_task_change.py`

### Mirrored commits

- 3911: `6778445097576cf662b6ab28eb899c6272c87685` — Adds per-task indexed change cursors with conservative full-read fallback.
- 3912: `d3bb6a5a61382b0dc28c48b4bd122421da848d4a` — Uses conflict-policy-safe UPSERT for task-change marks.
- 3913: `a40c42f452f9ded6b8b40214e1f29e23f8d17007` — Creates task-change tables with rowid for generic snapshots.

## Tests

- `tests/hermes_cli/test_kanban_task_change.py`: task/link/comment/run/event writes and deletes yield the precise affected IDs; moved keys mark both tasks. Heartbeats/bookkeeping do not move the cursor. Missing/old triggers, unmarked bumps, regressed counters, file/schema changes, unknown full marks, and mid-read identity swaps require `FULL`. Tests pin trigger repair/newer-epoch preservation, one-snapshot reads, visibility inside an existing write transaction, all outer conflict policies, repeated parent marks, and rowid availability on both boards and shadows.
- `tests/hermes_cli/test_kanban_schema_owner.py`: expected schema comparisons exclude the display-only revision/change tables from the canonical schema-owner reference.
- `scripts/benchmark_kanban_task_change.py`: a benchmark, not a unit test; compares bump-only versus marked writes and indexed cursor reads at multiple board sizes.

Tests were not rerun by this documentation pass. These descriptions report assertions inspected in the staged mail diffs, not fresh passing results. Whole-series replay and tree verification belong to the parent mirror verification; no deployment or live behavior is claimed here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including its pinned historical replay bridges, snapshot policy/replay plan, and mandatory post-replay hostname escape repair and repaired-tree verification. These ordinals are not independent cherry-picks: prior schema, helpers, and shared-file context are dependencies. Do not replay only this document's subset or rewrite historical mails to avoid the repair.

## Gotchas

A cursor is not a durable event log: missing task rows can mean deletion, and `FULL` is a required normal result. Do not compare `data_version` or revision numbers alone across file generations. The rowid follow-up changes creation DDL; it does not contain a migration rebuilding a previously created WITHOUT ROWID table. Apply the follow-ups with the whole series, not the initial trigger version alone.

## Plugin?

No. Writers must mark within the same SQLite content transaction; a post-write plugin cannot provide this guarantee.

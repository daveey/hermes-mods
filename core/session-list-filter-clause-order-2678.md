# Session list filter: plain columns first, indexed pinned back-fill

Patches: **2654**.

## Behavior

- Sidebar session list queries return the same rows, counts and order as before; only the cost changes (the commit message reports a differential dump of 85 list/count variants as byte-identical, and sidebar slice time dropping from about 24 ms to 16 ms on a synthetic 11,000-session fixture; not reproduced here).
- `_session_filter_where` now emits plain-column clauses (source, session key, excluded sources, cwd, workspace, minimum message count, archived) first and the child-lineage clauses (`_LISTABLE_CHILD_SQL`, the `_delegate_from` `json_extract`) last, so most rows are rejected before `json_valid` / `json_extract` run.
- The pinned back-fill (`SELECT id FROM sessions WHERE pinned = 1`) is index-driven rather than a full `sessions` scan per slice.
- An existing `state.db` gets the new index on its next writable open; no schema version bump.

## How it works

- Reordering is safe for parameters because the lineage clauses bind none.
- New partial index `idx_sessions_pinned ON sessions(pinned, id) WHERE pinned = 1`, declared in `DEFERRED_INDEX_SQL` next to `idx_sessions_effective_activity`. Leading with `pinned` makes the planner SEARCH rather than walk the index.
- The schema-drift test fixture `_drop_session_column` also drops `idx_sessions_pinned` before dropping the `pinned` column.

## Code

`hermes_state_sessions.py`, `hermes_state_common.py`, `tests/hermes_state/test_session_filter_clause_order.py` (new), `tests/hermes_cli/test_browse_schema_drift.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_state/test_session_filter_clause_order.py`, `tests/hermes_cli/test_browse_schema_drift.py` (fixture change only). Entry path: `_session_filter_where` directly (clause order, unchanged parameter list, query runs), and `SessionDB.list_sessions_rich` with `EXPLAIN QUERY PLAN` of the pin query on a fresh store and on an existing store missing the index. Row, count and order equivalence is **not** asserted by these tests (it rests on the commit message's differential dump); the Desktop sidebar HTTP path is **untested**.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Clause order is part of the SQL text contract; code or tests matching the old order as text need updating.
- The index build runs once on an existing large store at its next writable open.

## Plugin

Core change; no plugin required.

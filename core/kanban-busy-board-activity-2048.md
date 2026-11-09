# Busy-board activity fallback

Patch **2062**.

## Behavior

A busy local board with a usable previous read reuses its own last good activity counts while other boards can be read freshly. Session-task queries reuse the last successful list for that exact query, marking every reused row `stale=True`. A never-read busy board remains an error/unknown state, not an empty workload.

## How it works

`BoardLifecycleBusy` carries `SQLITE_BUSY`; shared `is_busy` recognizes native busy/locked codes too. A busy `board_memo_key` returns a fresh `BusyToken` carrying the error, so it cannot validate an old memo. The board reader reuses a compatible prior memo without spending a second busy budget, retains that memo, logs a warning without a traceback and marks the recomputed counts non-revalidatable beyond their TTL.

The process-local task-list memo keys by board path, session-ID tuple and status tuple and is bounded to 512 entries. Successful reads replace entries; fallback copies rows with the stale marker. Cache reset clears the memo.

## Code

- `hermes_cli/kanban_generation.py`: typed lifecycle busy error and retry predicate.
- `hermes_cli/kanban_session_activity_revalidate.py`: busy memo-key token.
- `hermes_cli/kanban_session_activity_busy.py`: count/list fallback and bounded memo.
- `hermes_cli/kanban_session_activity.py`: annotation recomputation, task listing and duplicate-filter wiring.

## Tests

**Not rerun this pass.** `tests/hermes_cli/test_kanban_busy_board_activity.py` holds a real lifecycle lock from another thread and covers typed busy errors, never-matching keys, `annotate_kanban_activity` preserving warm-board counts while updating a healthy board, non-revalidatable aggregate cache, `session_tasks` stale rows and cold-board error/unknown behavior. The Desktop rendering and duplicate-filter-specific entry path are not separately exercised in this patch.

## Applying

Apply 2062 within [the core replay](README.md), retaining prior board memoization and lifecycle-lock changes.

## Gotchas

Fallback requires a compatible successful memo; parked/stalled field requirements still apply. Corrupt or missing boards are not classified as busy. Counts are last-known data, not proof of current state. A cold busy board can still make the aggregate annotation unknown; the isolation guarantee is conditional on available prior evidence.

## Plugin eligibility

Not plugin-only: lifecycle errors, per-board caches and core sidebar annotation must agree on freshness semantics.

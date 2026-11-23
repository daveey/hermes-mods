# Kanban reader contention bounds and private-reader fallback (mirror 5448, 5449, 5472–5474, 5479–5481)

## Behavior
- Speculative session-task reads no longer wait indefinitely on Python-level locks: a contended `content_key` probe becomes "unknown", and reader-lock admission counts against the existing busy budget (`borrow_lock`). The probe lock first returned unknown immediately (5448), then was relaxed to wait at most 50 ms so healthy concurrent legacy probes still share their observer (5449); an unknown key is a fresh `object()` so it never certifies a cached answer.
- When the shared `cached_ro` read-only connection is borrowed, the reader waits at most 50 ms (or the remaining budget), then opens a private read-only connection with the same opener (busy timeout = remaining budget, capped at the shared reader's `_RO_BUSY_TIMEOUT_S`), yields it and always closes it. The shared cache entry is untouched. `ro_stats["private_fallbacks"]` counts fallbacks; the counter increments only after the private open succeeds and sits inside the close guard.
- `borrow_lock` keeps its raising contract on top of a new `try_borrow` helper.
- Session-outcome card reads (`kanban_session_outcome_tasks.py`) stop using the shared `cached_ro` reader: they open their own `_open_ro(path, timeout=0.5)` inside `contextlib.closing` and run the short execution transaction on it, so they do not queue behind sidebar enrichment.
- Net state of typed busy: 5479 made `try_borrow` raise `BoardLifecycleBusy`, but 5481 (adopting the landed private fallback) reverts that to `sqlite3.OperationalError("database is locked: cached reader is borrowed")`. The diff does not show `card_read_failed`, timeout changes or stale-data handling, so those are not claimed.

## How it works
`kanban_reader_lifecycle.py` (borrow/try_borrow/cached_ro), `kanban_activity_revision.py` (probe lock), `kanban_session_activity.py` (+ `kanban_session_activity_replica_resolution.py`, which `replica_task_resolution` moved into and is re-exported from; the moved body reads the facade helpers (`_hub_db_path`, `_board_sync_enabled`, `_meta_float`, `HUB_STALE_AFTER_S`) through the module at call time so patches on the facade still apply), `kanban_session_outcome_tasks.py`.

## Code
`hermes_cli/kanban_reader_lifecycle.py`, `kanban_activity_revision.py`, `kanban_session_activity.py`, `kanban_session_activity_replica_resolution.py`, `kanban_session_outcome_tasks.py`.

## Tests
Source: `test_kanban_content_key_busy_budget.py` (by test names: content key does not queue behind the probe lock or, at zero budget, a borrowed reader; a borrow-exhaustion test added in 5479 whose name says retryable busy, with this file touched again in 5481 whose diff adds no test names, so its final expectation is unverified here), `test_cached_ro_borrowed_fallback.py` (private fallback, zero-budget fallback, connection closed after the body and when it raises, shared entry kept and reused, uncontended reuse, lock released, read-only and counted, busy timeout equals remaining budget and never exceeds the shared reader's, holder release within the wait hands over the shared reader, missing board still raises, `borrow_lock` raising contract, fallback opens through the cache opener), `test_kanban_session_outcome_tasks.py` (outcome reads current cards while the sidebar reader is borrowed, parametrized by status; owned connection released on return and on error). Not executed in this mirror pass.

## Applying
Apply in ordinal order; 5481 depends on 5472.

## Gotchas
Private fallback readers add connections under contention; watch `private_fallbacks`.

## plugin?
No.

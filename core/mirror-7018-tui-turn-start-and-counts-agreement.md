# TUI turn-start outcome publishing and status counts agreement

Patch coverage: 6734, 6735, 6956, 6957, 6958, 6959, 6960.

## Behavior

- Turn-start outcomes (`refresh_turn_start_outcome`) are scheduled on a tracked background thread off the prompt thread, coalesced per (profile home, session id) with the latest snapshot rerun once if a newer start arrived mid-publication; an absent profile home keeps launch scope; a failure to start the thread is logged and never aborts the prompt. Turn-end publication keeps its synchronous ordering.
- The session status "counts agreement" (the check that the card list and counts agree before an empty list is shown) is bounded: the empty-list agreement uses a fresh published snapshot or a recompute on one passive background thread per origin scope that it waits on for at most 1 s (a stale snapshot is never agreement). On timeout the card source is unknown coverage (never done, never a human ask) and the refresh is queued to rerun once the recompute settles; this is a deferral, not an immediate envelope clear. A deferred refresh replays its *own* trigger and concluding-turn identity (so a held turn-end refresh ends `done`, not `running`); same-flight deferrals replay in order and the full dedup-key matrix is pinned.

## How it works

`tui_gateway/turn_outcome.py` (`refresh_turn_start_outcome`), `hermes_state_outcome.py` (`_agreement_counts`, `_deferred_refresh`), `hermes_cli/kanban_counts_agreement.py` (agreement logic, zero-argument recompute target for target-only thread doubles).

## Code

`tui_gateway/turn_outcome.py`, `hermes_state_outcome.py`, `hermes_cli/kanban_counts_agreement.py`.

## Tests

Read from source; **not executed here**. `test_turn_start_{outcome,nonblocking,scheduling,coalescing}.py`, `test_turn_outcome_refresh.py`, `test_session_status_counts_agreement.py` (RED/GREEN under real held flight/links locks and held turn leases in source). Live TUI sessions are **untested**.

## Applying

Apply in ordinal order; 6957-6960 refine 6956.

## Gotchas

- Do not wait on the counts flight from the controls-refresh thread.

## Why core, not a plugin?

TUI gateway and session status.

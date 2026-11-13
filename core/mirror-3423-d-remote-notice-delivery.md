# Remote kanban notices: late delivery and single-flight

Patches: **3143, 3153**.

## Behavior
- A remote notice this host actually observed is delivered late (grouped under "Late notices") instead of silently settled stale when it passes the fresh window; the late window is 72 hours from first observation, past which one warning is logged. States seeded by a source's first snapshot still settle silently.
- Concurrent pollers of one replica snapshot share a single persist pass instead of each re-running it over every terminal card.

## How it works
- 3153 makes `observe()` return the states with a recorded first-seen apart from seeded ones; observed states within `LATE_MAX_S` are claimed by `collect()` or the sweep.
- 3143 adds single-flight: one poller persists, waiters reuse it; a failed persist lets a waiter persist itself; a newer snapshot never waits for an older one; reset clears pending state.

## Code
- `tui_gateway/kanban_remote_notices.py`
- `tui_gateway/kanban_remote_observations.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_kanban_remote_late_observed.py`: `test_starved_observed_notice_is_delivered_once_late_by_collect`, `test_starved_observed_notice_is_delivered_once_late_by_the_sweep`, `test_observed_notice_dropped_past_the_late_window_logs_one_warning`, `test_seeded_state_older_than_fresh_still_settles_silently`.
- `tests/tui_gateway/test_kanban_remote_observations.py`: `test_fresh_notice_delivered_normally_yields_exactly_one_row`.
- `tests/tui_gateway/test_kanban_observation_singleflight.py`: `test_concurrent_polls_of_one_snapshot_persist_it_once`, `test_a_failed_persist_lets_a_waiting_poll_persist_itself`, `test_a_failed_persist_with_several_waiters_ends_and_answers_each`, `test_a_newer_snapshot_does_not_wait_for_an_older_one`, `test_reset_clears_pending_and_never_strands_a_waiter`.
- `tests/tui_gateway/test_kanban_remote_arrival.py`, `test_kanban_remote_idle_read.py`, `test_kanban_remote_observations_lock.py`: changed; no new test names read.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Without 3143, remote notice collection was a large share of gateway and Desktop backend CPU on a board with thousands of terminal remote rows.

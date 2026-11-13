# Kanban local status wake for parked cards

Patches: **3714, 3715**.

## Behavior

- A kanban card parked while awaiting another card (a `wait-check task X` probe, or an awaiting card reference) is re-probed soon after that card changes status on the same gateway, instead of waiting for its next scheduled `check_every` probe.
- Tests cover complete, block, request-review and archive of the awaited card; the owner's next probe is pulled to within 60 seconds and the owner is ready on the next tick. A file-probe park that only carries an awaiting card reference also moves.
- With no change of the awaited card (other cards changing, title edits), probe count is unchanged: 3 probes over 30 simulated minutes at a 900 s cadence.
- A park with `--from blocked|review|triage` that sees an unmet intermediate change costs one extra probe, then returns to its normal cadence.
- Best-effort: a rolled-back transition expedites nothing, a failing expedite never fails the transition, and a board without a `tasks` table records nothing.
- 3715: the flush step never raises into the commit, even when the connection is a non-sqlite stand-in whose `execute()` returns `None`.

## How it works

- 3714 makes every outer kanban `write_txn` arm a per-connection TEMP trigger that records `UPDATE OF status ON tasks` changes into a temp table; the rows commit or roll back with the write. After COMMIT, `kanban_local_status_wake.flush` passes `{card: new status}` to the existing `kanban_awaited_card_wake.expedite`, which only moves `next_at` earlier, re-checks the park inside its own write transaction and wakes the dispatcher. An indexed prefilter query (does any scheduled park's probe or park event mention a changed card?) runs first, so unrelated commits do not read every park. No polling.
- 3715 wraps flush's read of its recorder so any exception is swallowed, because a test stub connection broke the commit with an `AttributeError`.

## Code

- `hermes_cli/kanban_local_status_wake.py`
- `hermes_cli/kanban_db_connect.py`
- `tests/hermes_cli/test_kanban_local_status_wake.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_local_status_wake.py`: `test_awaited_card_moving_on_this_gateway_wakes_the_owner_within_a_minute`, `test_awaiting_card_ref_alone_wakes_the_owner`, `test_no_change_of_the_awaited_card_adds_no_probe_over_30_minutes`, `test_from_status_still_reads_not_yet_and_an_unmet_change_probes_at_most_once`, `test_a_rolled_back_transition_expedites_nothing`, `test_a_commit_no_park_mentions_never_reads_the_parks`, `test_a_failing_expedite_never_fails_the_transition`, `test_a_sweep_past_the_prefilter_cap_still_expedites`, `test_a_board_without_tasks_records_nothing`.
- 3715's non-sqlite stand-in behavior is untested in these patches (its commit message mentions a regression test, but the patch contains only the one-line source change).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas

- Only status changes written on the same gateway are caught; cross-gateway changes rely on the existing replica-pull expedite.
- If the wake fails, the park silently keeps its `check_every` cadence.

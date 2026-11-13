# Kanban wait-check: Board replica fallback and completion-origin pin

Patches: **3725–3726**.

## Behavior
- `hermes kanban wait-check task X --gateway G` can now report met from this gateway's Board replica when the owner gateway cannot be asked (ssh failure/timeout, rc 255, or forced-command refusal) and the registry replica's answer was not met. This lets cross-gateway parks wake on a change instead of waiting the full check interval.
- The Board replica only moves the answer toward met: it never downgrades a registry met, and never replaces the registry's rc 3 (stale or unknown scope) with not-yet.
- A Board answer is used only if the replica was pulled within the freshness window (600 s) with no future stamp; `--from`/`--until`/parked-agent conditions are judged by the same verdict as the local check. A live owner read stands.
- 3726: a replayed completion that records an origin gateway keeps binding its own receipt even when its run holds a foreign Board lease (pinned by a test).

## How it works
- 3725 changes `check_remote` in `kanban_wait_check.py`: after the registry read, if the owner was not reached and the result is rc 1, 2 or 3, it also reads the Board replica row and accepts only rc 0. No new polling is added.
- 3726 is test-only; it guards that the recorded origin outranks the lease holder in the batch completion read.

## Code
- `hermes_cli/kanban_wait_check.py`
- `tests/hermes_cli/test_kanban_awaited_card_wake.py`
- `tests/hermes_cli/test_kanban_wait_check_board_replica.py`
- `tests/hermes_cli/test_kanban_completion_refs_sync.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_awaited_card_wake.py`: `test_gateway_probe_wakes_on_the_board_change_while_ssh_fails_and_the_registry_lags`, `test_gateway_probe_keeps_the_registry_answer_when_the_board_replica_is_not_fresh`.
- `tests/hermes_cli/test_kanban_wait_check_board_replica.py`: `test_a_fresh_board_met_wins_over_a_lagging_registry`, `test_a_board_replica_never_downgrades_the_registry_met`, `test_a_fresh_board_met_wins_whatever_the_registry_scope_age`, `test_a_board_replica_not_pulled_recently_adds_nothing`, `test_a_board_replica_with_a_future_stamp_adds_nothing`, `test_stale_registry_and_no_board_row_stays_unknown`, `test_stale_registry_with_a_fresh_board_met_reads_the_board`, `test_stale_registry_with_a_fresh_board_not_yet_stays_unknown`, `test_from_applies_to_the_board_replica`, `test_unreadable_board_replica_keeps_the_registry_answer`, `test_a_live_owner_read_stands`.
- `tests/hermes_cli/test_kanban_completion_refs_sync.py`: `test_a_recorded_origin_outranks_its_runs_board_lease`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A Board replica can only turn a not-met answer into met; it cannot report a task as not-yet over a registry answer.
- Only task checks with `--gateway` take this path.

# Kanban CI-run progress sweep follow-ups

Patches: **3487**.

## Behavior
- A card woken by the CI sweep is the immediate probe for its "runners offline" blocker. A card woken a second time for the same blocker is the probe again, rather than being held for the probe interval. A card woken while others already wait behind the blocker is held with them, and its comment says for how long.
- When the repo's (then the org's) runner list is readable and an online runner carries the job's labels, the run is treated as busy (parking allowed, nothing woken). Otherwise messages say no runner picked the job up in 15 minutes. The org list is read only when there is no online repo match; a possible false "busy" from runner groups the repo cannot use is documented.
- The card scan runs off the dispatcher thread on the wake-check pool, at most about once a minute per board, and only when a scheduled card's latest park is of kind CI. A running sweep counts against the pool's concurrency limit.
- One sweep reads at most a fixed number of runs (least recently checked first) and starts no GitHub call after a time budget, including runner-list reads; a run cut off before its runner list is re-read first next sweep.
- Per-board bookkeeping is pruned to cards still waiting on CI.

## How it works
3487 makes `apply()` append a probe-released event naming the asserting run; `shared_blocker_guard` lets that card spawn until a later run of it starts (`_released_as_probe`). Releases without a run id keep the older probe-start rule. `submit_sweep` does one indexed query on the dispatcher thread and starts the sweep only when needed. Scoped blockers are filtered with `_task_applies`.

## Code
- `hermes_cli/kanban_ci_progress.py`
- `hermes_cli/kanban_shared_blockers.py`
- `hermes_cli/kanban_wake_check.py`
- `tests/hermes_cli/test_kanban_ci_progress.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_ci_progress.py`: `test_saturated_online_runners_are_not_offline`, `test_readable_runner_list_without_a_matching_online_runner_refuses`, `test_parked_card_wakes_when_its_run_stops_starting`, `test_second_card_woken_behind_the_same_blocker_is_held_and_told_why`, `test_probe_release_is_spent_by_one_run`, `test_probe_release_must_name_this_blocker_after_the_card_asserted_it`, `test_card_woken_again_for_the_same_blocker_is_the_probe_again`, `test_release_without_a_run_id_is_spent_by_any_probe_start`, `test_scoped_blocker_card_on_another_provider_does_not_hold_the_woken_card`, `test_scoped_blocker_card_on_the_same_provider_holds_the_woken_card`, `test_ci_sweep_reads_github_at_most_every_5_min_per_card`, `test_sweep_forgets_cards_that_stopped_waiting_on_ci`, `test_card_woken_after_the_blocker_cleared_is_the_new_probe`, `test_sweep_reads_at_most_max_cards_least_recently_checked_first`, `test_sweep_starts_no_read_past_its_time_budget`, `test_sweep_starts_no_gh_call_past_its_budget_and_rereads_that_run_first`, `test_runner_list_read_needs_budget_left`, `test_org_runner_list_is_read_only_without_an_online_repo_match`, `test_dispatcher_tick_scans_off_thread_and_at_most_once_a_minute`, `test_no_sweep_when_no_card_is_scheduled`, `test_sweep_starts_only_for_a_scheduled_card_whose_latest_park_is_ci`, `test_no_second_sweep_of_a_board_while_one_runs`, `test_no_sweep_without_a_free_pool_slot`, `test_a_running_sweep_counts_against_the_probe_budget`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The org-level runner list can report a false "busy" for runner groups the repo cannot use (documented in the patch).
- Each park-path confirmation may make up to three GitHub calls of 20 s each.

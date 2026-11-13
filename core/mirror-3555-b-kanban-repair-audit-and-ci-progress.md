# Kanban repair-unblock audit and CI-run progress refinements

Patches: **3539, 3543**.

## Behavior
- When a card's home gateway applies a forwarded operator unblock from a missing-parent repair card, it now writes the same `missing_parent_requeue` audit row (by, repair card, target) that the same-gateway unblock path writes, plus the operator request uid linking it to the call. Ordinary operator unblocks of an own-agent block write no row; a refused caller writes none.
- CI-run progress comments: when a runner list (repo or org) cannot be read, the message says the runner list is unreadable and runs may be offline or saturated, instead of claiming no online runner exists. The wake and park refusal are unchanged.
- Only online runners whose busy flag is not false count as saturated. If every matching online runner is idle while the job waits 15 minutes, the run is reported as not starting and the idle runner is named. Busy runners hold a wake back for at most a 2 hour cap, after which the run is reported as not starting and the cap is named.
- A shared-blocker release without a run id now also requires that the blocker was not re-asserted afterwards; otherwise the released card is held again.
- Two racing sweep ticks on one board start one sweep.

## How it works
- 3539 makes `_apply` in the operator-call module append the audit row after a successful missing-parent release (via the repair card) and rewords the `BLOCK_RELEASE_EVENTS` comment in the requeue module (fallback set for re-hold paths that skip the `blocked` event, not every exit from `blocked`).
- 3543 makes `_fetch_runners` return None on a partial read, adds idle/busy runner logic with `BUSY_LIMIT_SECONDS`, re-checks the running sweep and `SWEEP_EVERY_SECONDS` under the lock in `submit_sweep` (`_sweep_due`), changes `_released_as_probe` / `shared_blocker_guard`, and makes `task_applies` public in the shared-blockers module. A partial runner read falls back to the recent-jobs evidence; that fallback is not capped by the busy limit.

## Code
- `hermes_cli/kanban_operator_call.py`
- `hermes_cli/kanban_requeue.py`
- `hermes_cli/kanban_ci_progress.py`
- `hermes_cli/kanban_shared_blockers.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_operator_call_missing_parent.py`: `test_own_agent_block_released_by_operator_call_writes_no_repair_audit`. The patch also extends two existing tests there to assert the audit row on the home (direct and fleet end-to-end); their names are not added lines in the patch.
- `tests/hermes_cli/test_kanban_ci_progress.py`: `test_one_unreadable_runner_list_never_claims_no_online_runner`, `test_idle_online_runner_that_takes_no_job_wakes_the_card`, `test_any_busy_runner_reads_as_saturated`, `test_busy_runners_hold_a_wake_back_only_up_to_the_cap`, `test_release_without_a_run_id_needs_no_re_assertion_after_it`, `test_two_racing_ticks_on_one_board_start_one_sweep`, `test_org_list_unreadable_without_a_repo_match_falls_back_to_recent_jobs`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The guard change only ever holds more cards, restoring earlier behavior for a re-asserted blocker; one case remains (another card's probe run after the release that ended without settling the wall).
- Entry paths (CLI, tool, REST) beyond the listed tests are untested here.

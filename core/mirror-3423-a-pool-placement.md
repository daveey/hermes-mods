# Kanban pool placement and repin fixes

Patches: **2928, 2929, 2937**.

## Behavior
- A worker that pushed its branch by URL no longer gets its card pinned to that worker at park time: the park check asks the remote whether HEAD is already a remote branch tip, even if the branch has since moved.
- When hub admission refuses a card because its assignee is not a pool profile, the refusal names the assignee, the configured pool profiles (`kanban.placement.assignees`) and the pool route to use.
- Repinning a legacy card to the pool keeps its waiting status (ready, scheduled or todo) instead of forcing ready; an origin repin stays ready-only and creator, lease and revision fences are retained. A pending readback reports the precondition status.

## How it works
- 2928 makes the park pin in `kanban_move_pool` compare HEAD against the remote (not only local `refs/remotes/origin/*`) before declaring commits unpushed.
- 2929 extends `hub_stay_remedy` so the structural "assignee not allowlisted" keep produces an actionable remedy; the hub admission tool surfaces it.
- 2937 widens pool-repin eligibility in `kanban_worker_repin` to waiting states and reports the precondition status on pending readback.


## Code
- `hermes_cli/kanban_hub_admission_placement.py`
- `hermes_cli/kanban_move_pool.py`
- `hermes_cli/kanban_worker_repin.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_pool_queue.py`: `test_a_head_that_is_a_remote_branch_tip_counts_as_pushed`, `test_park_keeps_work_pushed_by_url_on_the_pool_even_after_the_remote_branch_moved`.
- `tests/hermes_cli/test_kanban_worker_repin.py`: `test_cli_waiting_pool_repin_keeps_authoritative_status`, `test_pool_repin_rejects_nonwaiting_status`, `test_waiting_http_success_with_failed_readback_retains_status`, `test_waiting_pool_repin_eligibility_preserves_state`, `test_waiting_pool_repin_keeps_ownership_fences`.
- `tests/test_kanban_hub_admission_placement.py`: `test_assignee_keep_remedy_tracks_structural_reason`.
- `tests/tools/test_kanban_hub_admission_tool.py`: `test_tool_non_pool_assignee_refusal_names_the_pool_route`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
2937 notes that the Board must support waiting-state repins before such migrations run (deployed separately). 2928/2929 coverage is via the listed unit tests only; CLI/tool entry paths beyond those tests are untested.

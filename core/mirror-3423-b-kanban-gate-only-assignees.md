# Kanban gate-only assignees

Patches: **2994, 2995**.

## Behavior
- With `kanban.gate_only_assignees` configured (a map from a profile only an admission gate may assign to the queue a card waits under), creating a card onto such an assignee is refused with an error naming the queue assignee to use, and nothing is inserted. The kanban_create tool and the CLI create path are both covered by tests.
- Without the config, creates are unchanged. Gate reassignment onto the gate-only profile still works.
- The resume requester and goal recovery file recovery cards under the queue, never the gate-only profile.
- The triage decomposer never offers a gate-only profile and maps any gate-only pick (fan-out and single-task promotion) to its queue.

## How it works
- 2994 adds `hermes_cli/kanban_gate_only.py` (config read like queue assignees, malformed entries skipped) and `validate_create_assignment`, which raises `GateOnlyAssigneeError` (a ValueError) from every create path.
- 2995 closes the triage bypass: the roster drops gate-only names and `decompose_triage_task` / `specify_triage_task` map a gate-only assignee to its queue via `create_assignee()`. The gate's own `assign_task` is unchanged.

## Code
- `hermes_cli/kanban_gate_only.py`
- `hermes_cli/kanban_db_creation.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_graph.py`
- `hermes_cli/kanban_decompose.py`
- `hermes_cli/kanban_goal_recovery.py`
- `hermes_cli/kanban_resume_requester.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_gate_only.py`: `test_create_task_refuses_a_gate_only_assignee_and_inserts_nothing`, `test_without_the_config_the_same_create_is_unchanged`, `test_worker_kanban_create_tool_is_refused_naming_the_queue`, `test_cli_create_is_refused`, `test_gate_reassignment_onto_the_gate_only_profile_still_works`, `test_resume_requester_files_a_gate_only_targets_recovery_under_the_queue`, `test_goal_recovery_never_picks_a_gate_only_profile`, `test_config_shapes`, `test_board_home_config_declares_it_too`, `test_triage_fanout_files_a_gate_only_pick_under_the_queue`, `test_triage_single_task_promotion_files_a_gate_only_pick_under_the_queue`, `test_decomposer_roster_never_offers_a_gate_only_profile`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
The feature is inert until `kanban.gate_only_assignees` is configured.

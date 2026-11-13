# Kanban worker scope: capability checks and scratch-home fence

Patches: **3239, 3243, 3271–3272**.

## Behavior
- A pool worker can `kanban_create(requires=[...])` again: the assignee capability check reads the assignee's policy inside a bound worker scope instead of failing with `assignee_capability_mismatch`.
- For the worker's own profile the check resolves `${VAR}` references injected by the launch environment, so a capability the profile has is no longer reported missing.
- When the policy cannot be read, the capsule-requirement warning carries the exception type and message on the same line.
- A throwaway `HERMES_HOME` under the worker's auto-pruned scratch directory (`<root>/cache/scratch`) is exempt from the Kanban path fence, so probe homes no longer leak. The scratch directory itself, the pinned board DB, the legacy marker, in-process delegate children, other paths under the root, and symlinked scratch dirs stay fenced.

## How it works
- 3239: `kanban_path_is_fenced` exempts targets that resolve strictly under `get_scratch_dir(marker root)`; the DB fence and tool fence consult it.
- 3243: `_profile_policy` binds the assignee's policy without the legacy setters that raised `ScopeSwitchRefused` in a bound scope.
- 3271: `capsule_requirement`'s warning includes the exception.
- 3272: the own profile is checked under the launch secret scope.

## Code
- `agent/delegation_context.py`
- `hermes_cli/kanban_db_fence.py`
- `tools/kanban_tools_fence.py`
- `hermes_cli/kanban_capabilities.py`
- `gateway/session_kanban_capsule.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_tools_scratch_home_fence.py`: `test_scratch_home_scope_covers_local_writes_only`, `test_scratch_dir_home_reaches_real_logic`, `test_exemption_is_the_scratch_dir_get_scratch_dir_gives_for_the_marker_root`, `test_homes_elsewhere_under_the_root_stay_refused`, `test_scratch_dir_home_with_board_elsewhere_under_root_stays_refused`, `test_symlink_in_scratch_dir_to_a_board_under_root_stays_refused`, `test_scratch_dir_symlinked_into_the_root_exempts_nothing`, `test_pinned_db_under_scratch_dir_stays_refused`, `test_legacy_marker_still_refuses_a_scratch_dir_home`, `test_in_process_delegate_child_still_refused_on_a_scratch_dir_home`, `test_real_worker_subprocess_drives_tools_on_scratch_dir_home`, `test_scratch_pruner_removes_an_idle_probe_home`.
- `tests/hermes_cli/test_kanban_capabilities.py`: `test_profile_policy_resolves_inside_a_bound_worker_scope`, `test_own_profile_policy_resolves_launch_injected_refs`.
- `tests/gateway/test_session_kanban_capsule_guard.py`: `test_unreadable_policy_warning_names_the_exception`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The scratch exemption deliberately does not resolve the scratch path itself: a scratch dir symlinked elsewhere under the root exempts nothing.

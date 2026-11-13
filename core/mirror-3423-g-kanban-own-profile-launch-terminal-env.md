# Own-profile capability check honors the launch TERMINAL_* env

Patches: **3307, 3328, 3360**.

## Behavior
- The kanban `requires` capability check for a process's own profile now counts a terminal backend set only by the launch environment (for example `TERMINAL_ENV` supplied by a service manager). Previously an own profile running over ssh/docker could be judged local and granted `host_repo`.
- `launch_terminal_env()` reads the live environment until multiplexing is active and only then freezes a snapshot; a single-profile process no longer freezes it early.
- Other profiles stay judged from their files only.

## How it works
- 3307 passes `env_overlay=launch_terminal_env()` for the own (unstamped) home in `_profile_policy`.
- 3328 stops `launch_terminal_env` from caching the launch snapshot on first call before multiplexing is active.
- 3360 makes `profile_config_scope` reuse `launch_terminal_env()` instead of an inlined filter and fixes stale "frozen" wording (refactor, no behavior change).

## Code
- `agent/runtime_scope.py`
- `hermes_cli/kanban_capabilities.py`
- `tui_gateway/launch_profile_policy.py`

## Tests
Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_capabilities.py`: `test_own_profile_policy_counts_launch_only_terminal_backend`, `test_own_profile_check_does_not_freeze_the_launch_env`.
- `tests/tui_gateway/test_profile_config_scope.py`: `test_launch_terminal_env_reads_the_live_env_until_multiplexing`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- After multiplexing activates the overlay is frozen; before, it follows the live env.

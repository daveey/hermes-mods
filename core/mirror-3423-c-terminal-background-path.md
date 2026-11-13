# Terminal background spawns get foreground PATH

Patches: **3112**.

## Behavior
- `terminal(background=true)` commands see the same Hermes-managed PATH directories as foreground runs.
- Exit-0 results that mask a missing command ("command not found", missing-file spawn errors) get the masked-success hint, including process poll/wait/log results.

## How it works
- 3112 applies `_user_tools_first(_append_missing_sane_path_entries(...))` in `_terminal_env`, extends `terminal_hints` failure shapes and adds the hint to exited-0 process results.

## Code
- `tools/environments/local.py`
- `tools/process_registry.py`
- `tools/process_registry_wait_policy.py`
- `tools/terminal_hints.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_background_spawn_path_parity.py`: `test_background_spawn_env_has_hermes_home_bin`, `test_background_path_is_superset_of_foreground_path`.
- `tests/tools/test_terminal_hints.py`: `test_missing_command_behind_pipe_flagged`, `test_ordinary_no_such_file_not_flagged`, `test_wait_result_gets_hint`, `test_clean_wait_result_has_no_hint`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.

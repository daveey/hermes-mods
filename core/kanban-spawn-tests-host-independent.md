# Host-independent Kanban spawn tests

Patch 0663 (`40aa7ba60d`), following a2c2b3eba8. Test-only; production behavior is unchanged.

## Behavior

`test_dispatcher_spawn_injects_kanban_paths_without_stale_session` and `test_default_spawn_pins_repo_root_on_module_worker_pythonpath` pin `kanban_db_dispatch._restart_safe_worker_argv` to the in-process (no systemd) outcome, so they assert env/PYTHONPATH injection on every host.

## How it works

On a systemd host the real wrapper refuses a claimed task with no current run id (`cannot create restart-safe systemd scope ...`). The tests' fake Task has none, so they failed on hermes-hub only. The production refusal is untouched and still covered near `test_kanban_db.py:574`.

## Tests

`tests/hermes_cli/test_kanban_db.py`: 54 passed, 1 skipped on hermes-hub (both targets failed before).

## Applying

Append after 0662.

## plugin?

No; repository regression tests only.

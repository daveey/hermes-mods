# Tests cannot open the production Kanban board

## Behavior

In a pytest-context process, Kanban's connection choke point refuses the account's real boards before any `mkdir` or open: `<account root>/kanban.db` and `<root>/kanban/boards/<slug>/kanban.db`. The account root comes from passwd, not `$HOME`, so redirecting `HOME`/`HERMES_HOME` does not bypass it. A board beside the production one (a tmp board) still opens. The guard covers test files outside `tests/` and runs with `--noconftest`.

In-tree tests also get a wider conftest guard: `_kanban_write_guard` denies the captured kanban root, the real Hermes root (even when a pre-set `HERMES_KANBAN_HOME` moved the kanban root) and the inherited `HERMES_KANBAN_DB` pin file.

## Why

A worker ran a copy of the resolved-check board fixture from a scratch directory, outside `tests/`, so `tests/conftest.py` never loaded. The fixture set `HERMES_HOME` and `Path.home` only, and the worker's inherited `HERMES_KANBAN_DB` pin outranks both. The run wrote three dummy cards to a live board. The commit message carries the full five-whys chain. The root cause: the live-board refusal lived only in conftest, not at `connect()`. This was the third recurrence; both earlier guards were conftest-only.

## How it works

- `hermes_state_guard.ensure_kanban_test_isolation` runs at the top of `kanban_db_connect.connect()` and `init_db()`. It is armed by pytest context, like `hermes_state._ensure_test_isolation` for `state.db`.
- The resolved-check board fixture clears every `HERMES_KANBAN_*` variable, sets `HERMES_KANBAN_HOME` to its tmp home, and asserts `kanban_db_path()` is inside that home before `init_db`.

## Code

- Patch **1481** (`606b4c1d1d`): `hermes_state_guard.py`, `hermes_cli/kanban_db_connect.py`, `tests/conftest.py`, the `test_kanban_ask_resolved_check.py` fixture, and the new detection test. Merged through PR #493.

## Tests

`tests/hermes_cli/test_kanban_production_board_guard.py` stands in a fake account root for the live one, so a failing guard writes only to tmp:

- `test_connect_and_init_refuse_a_production_board_before_touching_it`: connect, init_db and explicit-path refusal.
- `test_a_board_beside_the_production_one_still_opens`: the control case.
- `test_incident_shape_is_refused` and `test_pytest_file_outside_tests_without_conftest_is_refused`: the incident replayed as an out-of-tree pytest file with `--noconftest`.
- `test_conftest_guard_denies_real_root_and_pin_when_kanban_home_moved`: the multi-root conftest deny.

The commit reports RED with the `connect()` calls and multi-root match removed (4 failed, 1 control passed), then GREEN (5 passed). It also reports the board fixture passing 40/40 under `--noconftest` with an inherited fake pin, without creating the pin file. Exact PR head `b41abac0746f0506a62bfcc8baf88da2a6bee2d8` passed CI run `41294368192`.

## Applying

Full-series recipe in [README.md](README.md), snapshot 1484.

## Gotchas

- Worker descendant fences are a separate guard. Never clear them (`env -u HERMES_DELEGATED_CHILD_CONTEXT`) to make a fixture run. In the incident, clearing the fence is what let the write through.
- A real-DB test must isolate `HERMES_KANBAN_HOME` and `HERMES_KANBAN_DB` explicitly and assert the resolved path before writing. Setting `HERMES_HOME` alone is not enough.

## Plugin?

No. The guard sits at core's database connection choke point.

# Per-host test slots cap xdist workers; a full max_in_progress is idle, not stuck

Patches: 0607 (daveey/hermes `a8c26642a3`), 0702 (`175f5c0e57`, per-host `tests.xdist_workers`).

## Behavior

A direct `pytest -n 8` held one host test slot but ran 8 workers; 4 slots x 8 on a 16-core Mac put load past 100 while ready cards waited behind `max_in_progress=6` and the gateway logged "dispatcher stuck ... check profile health" with no reason. Now xdist workers are capped per slot, and a dispatch held only by a full `max_in_progress`/`max_spawn` is reported as at capacity, not stuck.

## How it works

`run_tests_host_slots.xdist_worker_budget()` returns `HERMES_TEST_XDIST_MAX`, else `cpu_count // host slots`; `tests/conftest.py` trims xdist's popen tx list to it before nodes start. `DispatchResult.at_capacity` records the full cap, `describe_suppression` names it, and the gateway and CLI daemon stuck detectors skip ticks held only by a full cap (`all_at_capacity`).

## Per-host `tests.xdist_workers` (0702)

`xdist_worker_budget()` also reads the host's `tests.xdist_workers` from the Hermes config (`HERMES_TEST_SLOT_CONFIG` overrides the path). The budget is `min(cores // slots, xdist_workers)`: a laptop can cap each slot holder below its share, never above it. `HERMES_TEST_XDIST_MAX` still wins over both. Unset keeps `cores // slots`. The key is read by a shared `_config_int(key)` helper that `tests.host_slots` now uses too; it parses without PyYAML through a regex fallback.

## Code

- `scripts/run_tests_host_slots.py`, `tests/conftest.py`, `hermes_cli/kanban_db_dispatch.py`, `hermes_cli/kanban_ops.py`, `gateway/kanban_watchers.py`.

## Tests

- `tests/scripts/test_run_tests_host_slots.py` (budget), `tests/hermes_cli/test_kanban_host_cap.py` (at_capacity, describe_suppression, stuck-detector skip). The conftest trim itself has no direct test (untested).
- 0702, same test file: `test_host_config_sets_the_xdist_worker_budget` (config caps below cores // slots, env wins, unset keeps the share, a larger value never raises it) and `test_host_config_xdist_workers_parses_without_pyyaml`. Re-run on the patched tree for this mirror: 18 passed, 1 skipped (with the footgun test file).

## Applying

Apply with `git am -3` in series order from the merge-base with upstream; the patch is self-contained apart from the earlier patches it extends. If it no longer applies, re-implement from Behavior and Code.

## Gotchas

- Set `HERMES_TEST_XDIST_MAX` to override on unusual hosts.
- `tests.xdist_workers` is per-host config; a value above `cores // slots` is ignored.

## Plugin?

No. It changes core code that runs before or outside plugin loading.

## Follow-up patch (1639): foreign-owned boards don't count toward capacity health

**1639** (`db7a16a8ec`, `fix(kanban): exclude foreign-owned boards from capacity health aggregation`).

- **Behavior.** `all_at_capacity` decides whether a tick that dispatched nothing was "idle at capacity" rather than
  "stuck". A board skipped because another dispatcher owns it (`DispatchResult.skipped_owned`) was counted as a ticked
  board that was not capped, so a gateway whose only local board was at `max_in_progress` while another board was owned
  elsewhere logged the "dispatcher stuck" health warning. Owned boards are now ignored like lock-skipped ones. If no
  board actually ticked, the result is still not "at capacity".
- **Code.** `hermes_cli/kanban_db_dispatch_stuck.py`.
- **Tests** (not rerun by this mirror pass): `tests/hermes_cli/test_kanban_host_cap.py::test_all_at_capacity_ignores_boards_owned_by_another_dispatcher`;
  `tests/gateway/test_kanban_external_drain_hold.py::test_capacity_with_foreign_owned_board_keeps_bad_ticks_zero`
  (10 gateway ticks with a capped board plus an owned board, health window 1: no stuck line, no ERROR).
- **Gotchas.** The CLI daemon's stuck detector uses the same helper; it has no separate test for this case.

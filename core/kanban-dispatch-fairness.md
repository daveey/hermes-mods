# Kanban board fairness and ready-age diagnostics

Patches 0643 (`42769fa2b4`) and 0644 (`a595876fe8`).

## Behavior

Spawning ticks rotate which board goes first so the default board cannot always consume every host-wide worker slot. Drain-hold ticks retain plain board order. A ready card's `stranded_in_ready` age starts from its latest claim, spawn, condition/continuation wake or continue-to-ready checkpoint, not an old first promotion.

## How it works

The dispatcher rotates its ordered board list once per spawning tick. The diagnostic computes a ready-since floor from later lifecycle events before applying its threshold.

## Code

`gateway/kanban_watchers_dispatcher.py`; `hermes_cli/kanban_diagnostics.py`.

## Tests

Upstream regression tests (execution results are recorded separately):
- `tests/gateway/test_kanban_dispatcher_health.py::test_spawning_ticks_rotate_board_order_so_default_cannot_starve_others`: six ticks rotate every board into first position; all boards still tick; drain hold preserves order.
- `tests/hermes_cli/test_kanban_diagnostics.py::test_stranded_in_ready_measures_from_last_wake_not_first_promotion`: recent wake is not stranded; later age is measured from that wake; continuation wake resets age too.
- `test_stranded_in_ready_counts_a_continue_that_resumed_straight_to_ready`: continue-to-ready resets age.

## Applying

Apply 0643 then 0644 after their earlier Kanban dependencies, using `git am -3`.

## Gotchas

Rotation is not a reservation or weighted scheduler: host-wide concurrency admission still applies. Ready-age diagnostics do not prove the scheduler is healthy.

## plugin?

No; both behaviors are core dispatcher/diagnostic logic.

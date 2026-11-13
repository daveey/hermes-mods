# Kanban pickup-stall alarm for a held machine's default-placement cards

Patches: **3534**.

## Behavior
- When a machine is on a rollout hold, its own ready, unclaimed, default-placement scratch-workspace cards that no other active, unheld, non-explicit machine would copy raise ONE host-wide alarm after the stall threshold (`kanban.sync.pickup_stall_seconds`, 600 s default, measured from both ready time and hold start).
- The alarm lists every such card, is reused on later sweeps, and is placed off the held machine.
- No alarm: under the threshold, when another machine can claim the card, after the hold ends, after the card is moved, or for a worktree/dir-workspace card.
- Moving a listed card with `move <id> --pool` sets `runs_on` to the worker pool, after which an explicit-pickup worker copies it.

## How it works
3534 adds `hermes_cli/kanban_pickup_stall_held.py` (`sweep`, run on each dispatcher sweep of a held machine), keyed `pickup-stall:<M>:held-default:`. `kanban_pickup_stall.py` creation helpers accept an optional how-to so the body names the right move; `kanban_pickup_stall_repin.py` lets the re-place scope accept a listed card still in default placement created on that machine.

## Code
- `hermes_cli/kanban_pickup_stall.py`
- `hermes_cli/kanban_pickup_stall_held.py`
- `hermes_cli/kanban_pickup_stall_repin.py`
- `tests/hermes_cli/test_kanban_pickup_stall_held.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_pickup_stall_held.py`: `test_t1_a_pool_move_of_the_held_creators_default_card_lets_an_explicit_worker_take_it`, `test_t3_one_alarm_after_the_threshold_for_a_default_card_only_the_held_creator_may_run`, `test_t3_no_alarm_when_another_machine_can_claim_the_card`, `test_no_alarm_once_the_card_left_default_placement_or_the_hold_ended`, `test_a_worktree_card_is_never_listed`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- This is the core half only; automatically moving such cards when a rollout holds a worker lives outside these patches.
- Cards held by a respawn guard are not listed.

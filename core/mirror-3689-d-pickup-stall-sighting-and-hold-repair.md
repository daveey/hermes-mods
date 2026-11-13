# Kanban pickup-stall: fresher sightings and no stale hold repairs

Patches: **3680, 3686**.

## Behavior
- Volatile-only queue-reason flaps still write an uncommented event every 300 s, so the pickup-stall sighting does not age past the 600 s stall threshold while a card is repeatedly reserved to the same worker; the comment cadence (every 600 s) is unchanged.
- The host-wide `hold` repair is not filed when the live registry entry is readable and no longer held; an unreadable registry still alerts.
- Each detector pass first closes open, unclaimed host-wide repairs whose cards have all left ready here, or whose hold has lifted; a close event lets a later stall open a fresh card. Claimed, running, blocked, in-review or leased repairs are left alone.

## How it works
3680 adds `SIGHTING_MS` to `kanban_queue_reason` and a `commented_at_ms` field (excluded from the signature) so events written without comments carry the comment clock. 3686 adds `_hold_lifted` to `file_repairs` and a new `kanban_pickup_stall_resolve.sweep` that archives resolved repairs with a comment naming each card's state and a `pickup_stall_resolved` event.

## Code
- `hermes_cli/kanban_queue_reason.py`
- `hermes_cli/kanban_pickup_stall.py`
- `hermes_cli/kanban_pickup_stall_resolve.py`
- `tests/hermes_cli/test_kanban_queue_reason_flaps.py`
- `tests/hermes_cli/test_kanban_queue_reason_sighting.py`
- `tests/hermes_cli/test_kanban_pickup_stall.py`
- `tests/hermes_cli/test_kanban_pickup_stall_lifted.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_queue_reason_sighting.py`: `test_the_sighting_never_ages_past_the_stall_threshold_while_flaps_continue`, `test_twenty_minutes_of_flaps_still_post_at_most_three_comments`, `test_the_reserved_worker_raises_no_alarm_at_any_tick`.
- `tests/hermes_cli/test_kanban_pickup_stall_lifted.py`: `test_hold_skips_from_a_lifted_hold_file_no_hold_repair`, `test_a_real_operator_hold_still_files_exactly_one_card_per_host`, `test_an_open_hold_repair_is_closed_once_every_card_it_names_left_ready`, `test_an_open_hold_repair_is_closed_when_the_hold_lifted_and_its_cards_still_wait`, `test_a_claimed_repair_is_left_to_its_run`.
- `test_kanban_queue_reason_flaps.py` and `test_kanban_pickup_stall.py` have edited assertions only (event bound raised to 5; hold burst now holds the registry).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Uncommented sighting events raise the event count for a flapping card (up to 5 in 20 min in the test) while comments stay at 3.

# Kanban dispatch signals: quieter queue reasons and faster awaited-card wakes

Patches: **3647–3648**.

## Behavior
- A ready card waiting for a worker no longer gets a placement comment and `worker_queue_reason` event every ~10 s when only candidate load flaps. Events and comments are written when the meaning changes: state code change, eligibility flip, structural reason gained or lost, return to origin.
- Volatile reasons (load/memory thresholds, max card runs, reserved capacity, last-seen invalid) write at most one event and comment per card per 10 minutes, and only for a different picture.
- A reservation handed to another worker is an event without a comment.
- A park waiting on a card on another gateway re-probes when that card's status change reaches the replica, instead of waiting up to the 900 s check interval. The probe still judges; no periodic polling is added.
- The wake-check tick logs one `awaited_card_done_park_lag` warning for a park still asleep 2 minutes after its awaited card finished.

## How it works
- 3647 reworks `_record` in `kanban_queue_reason.py` around a meaning-based signature plus `VOLATILE_REASONS` and `REFRESH_MS`. Volatile-only observations replace an origin-local row (`kanban_queue_latest`, created on first use, never synced) so `latest()` stays current; a replica without the table reads the last event.
- 3648 makes the replica pull report applied status changes; the sync round passes them to `kanban_awaited_card_wake.expedite`, which makes every scheduled park naming a changed card due now and wakes the dispatcher.

## Code
- `hermes_cli/kanban_queue_reason.py`
- `hermes_cli/kanban_awaited_card_wake.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_sync_backlog.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_wake_check.py`
- `tests/hermes_cli/test_kanban_queue_reason_flaps.py`
- `tests/hermes_cli/test_kanban_awaited_card_wake.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_queue_reason_flaps.py`: `test_volatile_flaps_every_10s_for_20_min_write_at_most_one_per_10_min`, `test_refresh_needs_a_different_volatile_picture`, `test_state_code_changes_still_comment_at_once`, `test_all_candidates_becoming_ineligible_comments_at_once`, `test_gaining_or_losing_a_structural_reason_comments_at_once`, `test_return_to_origin_comments_at_once`, `test_a_new_reserved_worker_is_an_event_without_a_comment`, `test_latest_without_the_local_table_reads_events`.
- `tests/hermes_cli/test_kanban_awaited_card_wake.py`: `test_review_done_on_another_gateway_wakes_the_owner_within_a_minute`, `test_awaiting_card_ref_alone_names_the_card`, `test_no_change_of_the_awaited_card_adds_no_probe_over_30_minutes`, `test_from_status_still_reads_not_yet_and_an_unmet_change_probes_at_most_once`, `test_lag_warning_fires_once_for_a_park_that_cannot_see_the_done_card_and_never_after_a_wake`, `test_no_lag_warning_once_the_park_woke`, `test_expedite_never_raises_and_ignores_cards_no_park_names`, `test_a_probe_already_due_is_left_alone_and_wakes_nothing`, `test_a_park_that_left_scheduled_meanwhile_is_not_touched`, `test_a_reopened_awaited_card_drops_its_lag_record`, `test_lag_records_are_bounded`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- An unmet status change still costs one probe.
- Wake-ups depend on the replica pull; a stale replica is not helped (see the replica-age alarm doc).

# Kanban: a stale wake never moves a done card out of done

Patches: **3427–3430**.

## Behavior
- A done or archived card leaves that status only through an explicit reopen (reopened, unblocked, review_reopened, promoted_manual, or a status edit to a live status) ordered after its latest terminal event in origin order. A wake (continuation_woken, condition_met, promoted, wake_check_*) is never a reopen, whatever order it was minted or delivered in.
- Wake producers (due continuations, the implied wake, wake checks, restored missing checks) do nothing for a held card even if the local row still reads `scheduled`, and record one `stale_wake_ignored` event per producer and terminal event.
- Sync push drops the held `status` field from a Board-acked task row (other fields still go). Feed apply puts a held card back to done/archived at the end of a page unless the Board's row is `running`; a reopen arriving in a later page gives the status back.
- Divergence repair does not push a run-less live status over the replica's done card, but still sends a genuine uncaptured local reopen.
- A push-strip note written between the held row's page and the reopen's page no longer makes the restore give up.

## How it works
3427 adds `kanban_terminal_guard.py` (`terminal_hold`) and enforces it in the continuation, wake-check, sync-client push, DB-sync apply and divergence paths. 3428 makes `_restore_held` select the newest note whose source is `apply`, requires both logs to hold the card terminal in `_stale_lifecycle`, and runs one query in `terminal_hold` instead of a per-call schema pragma. 3429 classifies the guard's restores as not-from-running in the source-receipt site test. 3430 joins a line wrap so `kanban_sync_client.py` stays under its 2000-line health cap (no behavior change).

## Code
- `hermes_cli/kanban_terminal_guard.py`
- `hermes_cli/kanban_continuation.py`
- `hermes_cli/kanban_wake_check.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_sync_divergence.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`: `test_a_wake_minted_before_the_completion_and_applied_after_it_leaves_the_card_done`, `test_an_explicit_reopen_after_the_completion_still_reopens`, `test_a_reopen_whose_event_is_in_the_next_page_still_reopens`, `test_a_wake_pushed_after_a_remote_completion_does_not_reopen_the_board_card`, `test_divergence_repair_does_not_send_a_stale_wake_over_the_replicas_done_card`, `test_a_restored_wake_for_an_already_completed_card_does_nothing`, `test_a_due_continuation_on_a_stale_copy_of_a_completed_card_does_nothing`, `test_a_passing_probe_on_a_stale_copy_of_a_completed_card_does_not_wake_it`, `test_an_implied_wake_on_a_stale_copy_of_a_completed_card_does_nothing`, `test_a_legitimate_wake_of_a_scheduled_card_still_lands_ready`, `test_terminal_hold_orders_by_origin_not_by_arrival`, `test_a_push_strip_note_between_the_pages_does_not_lose_the_reopen`, `test_divergence_repair_still_sends_an_uncaptured_local_reopen`.
- `tests/hermes_cli/test_kanban_source_receipt_sites.py`: modified (classification only); no new named tests.
- 3430 has no tests (style-only).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The Board side needs its own lease fence for full protection; this change only stops the local side from sending or applying the stale status.

# Remote-owner pass on its own watcher, and wake-probe pinning

Patches: **3556, 3566**.

## Behavior
- A gateway that owns remote kanban notices admits them on its own cadence, no longer inside the local notifier loop; a slow remote pass can no longer delay local chain-card wakes.
- Expired remote notices scan the chat transcript at most once. Rows already settled in the ledger cost no scan on later passes.
- The remote-owner watcher honours `kanban.notify_in_gateway` and stops between passes.
- A pass that exceeds 5 s logs collect-vs-admit seconds.
- 3566 adds no product change: it pins that a wake which does not land keeps a live blocked card's `ask_resolved` probe row, and that a landed wake drops the row.

## How it works
- 3556 makes the "expired" settle reason in `collect` an `_ExpiredReason` that `_claim_notice` resolves only on the first settle (no ledger row yet). It adds `_kanban_remote_owner_watcher`, supervised in `_PRE_RECONNECT_WATCHERS` with its own `INTERVAL_S`, as the only caller of `kanban_remote_owner.tick`.
- 3566 is tests only, covering `kanban_wake_check._wake` and its `task_wake_checks` delete.

## Code
- `gateway/kanban_remote_owner.py`
- `gateway/kanban_watchers.py`
- `gateway/run_startup.py`
- `tui_gateway/kanban_remote_notices.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_kanban_remote_owner_delivery.py`: `test_remote_notice_admitted_while_the_notifier_deliveries_are_blocked`, `test_remote_owner_watcher_honours_notify_in_gateway`, `test_the_notifier_loop_no_longer_runs_the_remote_owner_pass`.
- `tests/tui_gateway/test_kanban_remote_expired_scan.py`: `test_expired_state_scans_the_transcript_once_then_never_again`, `test_expired_state_already_in_the_transcript_settles_transcript_without_a_warning`, `test_many_settled_expired_cards_cost_no_scan_per_pass`.
- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`: `test_a_wake_that_does_not_land_on_a_live_blocked_card_keeps_its_ask_probe`, `test_a_wake_that_lands_drops_the_cards_probe_row`.

The slow-pass timing log is untested.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- With `kanban.notify_in_gateway` off the remote-owner watcher does not admit notices.

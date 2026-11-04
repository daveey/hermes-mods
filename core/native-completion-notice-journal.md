# Pending completion notices survive an orderly backend exit

Patches: **1746** (`9a560a6224`, `[daveey] Journal pending native completion notices across orderly backend exit`); **1747** (`56bef6c2a8`, `[daveey] Use bound notice helpers and cover journal-after-marker cancellation`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- Pending native completion notices are journaled across an orderly backend exit and delivered after restart (1746); bound notice helpers are used and journal-after-marker cancellation is covered (1747).

## How it works

`tui_gateway/exit_job_receipts.py`, `session_notifications`, `turn_marker`, `tools/process_registry_awaiting.py`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1746: (subject only)
- 1747: (subject only)

## Code

`tools/process_registry.py`, `tools/process_registry_awaiting.py`, `tui_gateway/exit_job_receipts.py`, `tui_gateway/prompt_turn.py`, `tui_gateway/session_notifications.py`, `tui_gateway/session_reaper.py`, `tui_gateway/turn_marker.py`, `tui_gateway/update_pause.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/tui_gateway/test_auto_continue.py`
- `tests/tui_gateway/test_shutdown_job_receipts.py`: `test_backend_exit_interrupt_is_not_a_user_stop`, `test_claim_snapshot_never_redacts_under_the_registry_lock`, `test_claimed_but_unpublished_completion_is_journaled_without_waiting`, `test_failed_marker_write_keeps_notice_custody`, `test_finished_notice_dedups_held_and_shared_queue`, `test_finished_notice_shutdown_controls`, `test_finished_notice_survives_orderly_restart`, `test_held_events_taken_for_routing_stay_in_custody`, `test_in_flight_event_is_journaled_from_registry_custody`, `test_late_notice_marker_and_exit_journal_keep_every_receipt`, `test_marker_then_exit_journal_then_cancelled_retire_keeps_journaled_receipt`, `test_natural_exit_between_running_snapshot_and_kill_reports_real_result` (+9 more)

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.

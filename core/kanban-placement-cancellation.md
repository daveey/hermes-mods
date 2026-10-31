# Kanban: closing a placed card durably cancels its remote copy

Patches: 1348 1349 1350 1362 1363

- `patches/1348-fix-kanban-cancel-placed-copy-on-manual-completion-d.patch`
- `patches/1349-refactor-kanban-extract-completion-evidence-gate-for.patch`
- `patches/1350-fix-kanban-reconcile-request-only-manual-completion-.patch`
- `patches/1362-fix-kanban-durable-placement-cancellation-across-cra.patch`
- `patches/1363-fix-kanban-placement-sweep-does-not-report-a-failed-.patch`

## Behavior

- **Manual completion cancels the placed copy (1348).** Completing a card that was placed on another gateway (e.g. `force=True`) sends an archive for the remote copy via the existing archive forwarder, instead of leaving it running. A copy already `done`/`archived` there is left alone. The forwarder's own completion (mirroring a successful remote result) passes `placement_settled=True` and cancels nothing. Refused completions and plain (unplaced) cards forward nothing.
- **No waiting (1350).** A request-only placement (target id not yet known) is not awaited on completion (`await_budget=0`); the archive is recorded `unresolved` and the dispatcher sweep, which now also covers `done` cards, sends it once the id reaches the hub replica.
- **Durable across crashes and send failures (1362).** Archive and manual completion append `placement_cancel_intent` in the same transaction as the status flip. The sweep replays an intent with no forward event after it (older than 60 s), resends a `failed` archive with backoff (300 s doubling, capped at 6 h), and gives up after 7 days with a comment and an `expired` event. A send that raised is recorded `failed`; a completion whose remote copy is already terminal records `skipped`. Settle completions record no intent. Resends reuse the deterministic hub request id. A crash-gap replay is always tried first, even after a long outage.
- **Honest sweep report (1363).** A late send that fails is no longer returned as sent; it is left to the `failed` backoff path.
- 1349 moves the empty-completion evidence gate to `kanban_completion_evidence.py` (facade budget) and logs a failed remote observation to stderr; no behavior change.

## Code

`hermes_cli/kanban_placement_forward.py` (`record_cancel_intent`, `forward_completion`, `sweep_unresolved_archives`), `kanban_db.py` (`complete_task`), `kanban_db_archive.py`, `kanban_completion_evidence.py`.

## Tests

`tests/hermes_cli/test_kanban_placement.py`: `test_manual_completion_archives_forwarded_copy_online_or_queued` (rc 0/3), `test_manual_completion_preserves_terminal_remote_copy`, `test_completion_queues_archive_when_observation_is_unknown`, `test_settle_completion_never_forwards_archive`, `test_refused_and_plain_completion_do_not_forward`, `test_request_only_completion_never_waits_and_sweep_cancels_late_copy`.
`tests/hermes_cli/test_kanban_placement_cancel.py`: `test_crash_after_archive_commit_is_replayed_by_the_sweep_once`, `test_archive_without_crash_is_not_resent_by_the_sweep`, `test_completion_then_archive_sends_one_request`, `test_crash_after_manual_completion_with_delayed_create_cancels_the_late_copy`, `test_crash_after_manual_completion_of_a_running_copy_cancels_it`, `test_completion_replay_leaves_a_terminal_remote_copy_alone`, `test_settle_completion_records_no_cancel_intent_and_sweep_never_cancels`, `test_a_broken_placement_record_never_blocks_the_archive`, `test_failed_archive_send_is_retried_with_backoff_until_it_is_queued`, `test_failed_retry_backoff_is_capped`, `test_completion_send_exception_is_recorded_failed_and_retried`, `test_failed_archive_send_tries_once_more_then_gives_up_after_seven_days`, `test_legacy_failed_without_intent_gives_up_on_its_first_event_clock`, `test_crash_gap_after_a_long_outage_still_sends_before_giving_up`, `test_crash_gap_gives_up_after_seven_days_when_every_try_fails`, `test_unresolved_archive_whose_late_send_fails_is_not_reported_sent`. Not run for this mirror. 1349's stderr message is untested.

## Applying

Apply in order 1348, 1349, 1350, 1362, 1363. No config.

## Gotchas

- Cancellation is best-effort courtesy after a committed local close; a failure never rolls back the archive/completion.
- Replays rely on the deterministic request id so a racing original forward is applied once.

# Notifications: retry the deferred remote index before the normal poll

Patches: **4662, 4663, 4664, 4665, 4666**

- 4662 (`2b85c24147`): fix(notifications): retry deferred remote index before normal poll [daveey]
- 4663 (`8170cec51e`): test(notifications): cover retry collection failure and rebind fixtures [daveey]
- 4664 (`a6c9df3079`): test(notifications): exercise full-row intervening key churn [daveey]
- 4665 (`a9ca946f3f`): test(notifications): prove mixed-result dispatch and refusal custody [daveey]
- 4666 (`8060e7d93a`): test(notifications): bind churn counterfactual before poller startup [daveey]

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- When the remote notification index is deferred, an early remote-only poll retries it before the normal poll without extending the four-second index bound.
- Collection custody is preserved; a stale retry is cancelled on collection failure or teardown, and a retry does not shift normal poll work.

## How it works

- New `tui_gateway/notification_remote_retry.py` holds the retry scheduling; `session_notifications_kanban.py` splits kanban-specific polling out of `session_notifications.py`; `kanban_remote_notices.py` returns the deferral signal.
- Follow-ups add tests for collector failure cancelling the callback retry, full-row intervening key churn (the churn counterfactual is bound before poller startup), and mixed local-notice / remote-defer dispatch with refusal custody.

## Code

- `tui_gateway/kanban_remote_notices.py`
- `tui_gateway/notification_remote_retry.py`
- `tui_gateway/session_notifications.py`
- `tui_gateway/session_notifications_kanban.py`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/gateway/test_kanban_tui_sibling_custody.py`: modified only
- `tests/tui_gateway/test_bot_live_owner_delivery.py`: modified only
- `tests/tui_gateway/test_j_nonrpc_rows.py`: `test_h4_intervening_key_churn`
- `tests/tui_gateway/test_notification_remote_retry.py`: `test_deferred_reader_consumes_publication_before_next_churn`, `test_poll_attempt_keeps_only_valid_deferral`, `test_scheduler_remote_retry_does_not_shift_normal_work`, `test_remote_collector_failure_cancels_callback_retry`, `test_mixed_local_notice_remote_defer_real_dispatch`

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- Tests 4663–4666 only change test fixtures and rebind existing ones; skipping them loses coverage, not behavior.
- The retry must remain bounded; do not widen the four-second index bound.

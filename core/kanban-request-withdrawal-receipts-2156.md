# Archiving a pending human request leaves a withdrawal receipt

Patches **2142, 2143, 2144, 2145, 2146**.

## Behavior

- Archiving a `blocked` card that has a `human_request` appends one `request_withdrawn` event in the same transaction as the `archived` event. Ordinary archives stay silent.
- Subscribed chats get an informational notice: "request withdrawn: the card was archived. No answer is needed for this request; this is not approval and not proof of completion." This applies to the gateway notifier, the TUI session path and unloaded-session delivery. `request_withdrawn` is a wake kind, and wake text reads "request withdrawn (not approval or proof of completion)" (16 peer locale catalogs got translated keys in 2146).
- The receipt survives failure: an archived subscription is removed only after the notice was durably acknowledged, a failure-cap drop never discards an undelivered withdrawal, and a drop of an older failed claim is fenced if a withdrawal committed meanwhile.
- Withdrawal never grants approval or establishes completion; there is no historical replay and no synthesized success.

## How it works

- 2142: `kanban_db_archive.archive_task` selects `human_request` and emits the event; the unloaded delivery path no longer removes archived subs before ack; formatters, `_KANBAN_NOTIFY_KINDS` and `_WAKE_KINDS` gain the kind; `tui_gateway/kanban_notice_text.py` splits the chat-wait suffix helper out of the session registry.
- 2143: `ack_notify_claim` deletes an archived subscription only if no later event exists past the acked cursor (narrowed to `request_withdrawn` in 2145); a dispatched wake leaves ack to the worker (`_deliver_sub` returns early); the blanket unsub after an archived task is removed from the notifier.
- 2144: failure-cap unsubscribe is skipped when the batch holds a withdrawal (`has_withdrawal`); `_fmt_request_withdrawn` returns both ping and wake text; `gateway.kanban.wake.request_withdrawn` added to `locales/en.yaml`.
- 2145: `remove_notify_sub(..., preserve_withdrawal_after=cursor)` deletes transactionally only when no withdrawal event is newer than the failed claim; the notifier calls `_kanban_drop_failed` and then always rewinds; silent trailing events no longer strand an archived subscription (the TUI poll acks instead of removing).
- 2146: catalog parity for the 16 non-English locale files.

## Code

- `hermes_cli/kanban_db_archive.py`, `hermes_cli/kanban_db_notify.py`.
- `gateway/kanban_watchers.py`, `gateway/kanban_watchers_notifier.py`.
- `tui_gateway/kanban_unloaded.py`, `tui_gateway/session_notifications.py`, `tui_gateway/kanban_notice_text.py` (new).
- `locales/*.yaml`.

## Tests

**Source regression coverage; not executed in this mirror documentation pass.** Names come from the source diff; no pass/fail result is claimed.

- `tests/gateway/test_kanban_request_withdrawal.py` and `tests/tui_gateway/test_kanban_request_withdrawal.py`: `test_withdrawal_send_failure_keeps_subscription_until_retry` (extended across 2142, 2144 and 2145), `test_archive_request_notice_is_atomic_and_idempotent`, `test_unloaded_withdrawal_retries_failed_persist`, `test_request_withdrawal_is_deliverable_on_both_surfaces`, `test_older_claim_ack_preserves_unclaimed_withdrawal`, `test_dispatched_withdrawal_keeps_claim_until_worker_persists`, `test_failed_drop_fences_only_pending_withdrawal`, `test_silent_tail_does_not_strand_archived_subscription`.
- 2146 adds no test; its message says the existing i18n catalog parity check (not named in the diff) caught the missing keys.

## Applying

Apply ordinals 2142, 2143, 2144, 2145, 2146 as part of the snapshot-aware full replay described in the [README](README.md) (full recipe in its Applying section), in published order, not as independent patches. 2143 to 2145 fix interleavings in 2142's design, and 2146 completes the locale catalogs 2144 started. No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

- Remote replica publication is unchanged: this covers local subscriptions only.
- Any new locale must define `gateway.kanban.wake.request_withdrawn` or catalog parity fails.
- Silent kinds (`archived`, `unblocked`) still drain without a message; only a withdrawal needs a delivered ack before the subscription is dropped.

## Could this be a plugin?

No. It spans the archive transaction, the notify-subscription tables and three delivery surfaces in core.

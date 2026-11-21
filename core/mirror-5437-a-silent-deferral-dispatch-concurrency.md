# Silent deferral of automatic turns, ownership-card evidence and per-schedule dispatch isolation

Mirror entries: 5413, 5414, 5415, 5416.

## Behavior

- Automatic Desktop turns defer without a takeover card when the canonical ledger reports forwardable work: `queued`, `started`, `registered` or `running`.
- Those gateway-busy automatic retries stay at 30 seconds rather than growing toward the foreign-owner backoff cap. Admission resumes when ownership becomes available.
- Unknown ledger state, legacy ownership refusals and coordination failures retain their existing error behavior. Typed submissions are not made silent by this change.
- Emitted `SESSION_NOT_OWNED` terminal cards produce a structured warning with refusal provenance and a holder PID, without guessing the cause from message text.
- State-contract workflow dispatches use separate concurrency groups for distinct cron schedules as well as distinct event types, so an hourly schedule does not cancel the daily schedule.

## How it works

Admission retains the refusal reason and ledger status on the session. A synthesized turn refused against a forwardable ledger state logs an informational deferral instead of calling the terminal-error publisher. The refusal still prevents turn startup; silence does not grant ownership or bypass admission.

Notification preflight passes ledger status to `_notif_note_owner_refusal`. The submit path carries the same status through admission and consumes it afterward. Forwardable states reset the delay to the fixed floor; other refusals retain exponential backoff capped at 300 seconds. Successful ownership admission clears refusal and backoff state.

Admission passes the refusal object, rather than only its string, to the terminal-error boundary. After publishing `message.complete`, that boundary calls `log_ownership_card` for `SESSION_NOT_OWNED`. The helper labels forwardable states as `ledger_<status>`, accepts `lease_foreign`, `lease_self_orphan` and `update_marker`, and otherwise records `unknown`. Missing or invalid holder PIDs become zero.

The independent workflow change appends `github.event.schedule` to the concurrency group already keyed by `github.event_name`. `cancel-in-progress: true` remains in force within each resulting group; this is schedule isolation, not removal of cancellation.

## Code

Paths refer to the mirrored core source tree.

- `tui_gateway/prompt_turn.py`
- `tui_gateway/session_notifications.py`
- `tui_gateway/session_auto_continue.py`
- `tui_gateway/ownership_card_log.py`
- `.github/workflows/state-contract-dispatch.yml`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

The automatic-turn tests cover silent admission refusal for each forwardable ledger state, retained refusal signaling, no turn startup, fixed repeated retries and resumption after refusal clears. Controls cover unknown or absent ledger status, coordination failures, typed ledger refusals and retained exponential preflight backoff.

The warning tests exercise the common terminal publisher with queued/started ledger provenance, recognized lease/marker causes and unknown provenance. They assert one completion frame and one matching warning; a coordination-error control asserts no ownership warning. Registered/running warning labels and invalid-PID normalization are untested by these added cases.

The real-ledger integration test covers completion and watch-match producers against SQLite admission: no emitted card or persisted conversation, unchanged queued event, no completed delivery, fixed ownership delay, bounded producer requeue yields, and later settlement after acceptance. It also retains a typed-submission refusal control.

The workflow test asserts the exact event-plus-schedule group string alongside cron/script wiring. Actual hosted workflow cancellation behavior is untested by this static assertion. No dispatch-script production change is included in entry 5416.

Source test paths:

- `tests/tui_gateway/test_automatic_ledger_defer.py`
- `tests/tui_gateway/test_ownership_card_warning.py`
- `tests/tui_gateway/test_notification_refusal_backoff.py`
- `tests/ci/test_state_contract_dispatch.py`

## Applying

Apply these entries only as part of the **whole ordered 5437 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches in isolation.

## Gotchas

A silent refusal remains a refusal: pending automatic work must retry rather than be treated as delivered. Producer requeue yields and ownership retry delays are separate timers. The warning records publication at the backend boundary, not proof that a client displayed the card. Unknown provenance is deliberately not upgraded to a foreign-owner diagnosis. Typed-prompt forwarding and synthesized-event routing are not redesigned here.

## Why core instead of a plugin

Turn admission, notification retry state and terminal message publication share core ownership invariants. Their refusal evidence must remain intact across those boundaries. Workflow concurrency is repository CI configuration, not an agent-plugin lifecycle hook.

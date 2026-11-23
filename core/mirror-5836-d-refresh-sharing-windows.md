# Bounded annotation refresh floor and shared notice probes

Source patch ordinals: 5825.

## Behavior

The native holder uses a default-on three-second refresh floor, and serve notice pollers share a two-second structural-probe window across chats/profiles. Delivery eligibility, transcript checks, claims and acknowledgements remain per-chat/live rather than cached as a delivery verdict.

## How it works

`holder_coalesce()` returns the urgent floor unless `HERMES_ANNOTATION_COALESCE` is `0`, `false`, `off` or `no`, which restores the previous coalescing interval. The holder applies the floor to control wakes as well as source/deadline reads.

`notification_shared_window.borrow` keys a bounded process-wide lane by absolute replica path and observation-ledger path, starts expiry before I/O and shares fingerprint/candidate computation. Unknown fingerprints and failures retry rather than certifying quiet. Only the serve poller opts into `shared_window=True`; other callers retain literal reads. `HERMES_NOTIFICATION_SHARED_WINDOW=0` clears shared entries and restores immediate structural probes.

## Code

- `hermes_cli/annotation_event_loop.py`
- `hermes_cli/kanban_annotation_host.py`
- `tui_gateway/notification_shared_window.py`
- `tui_gateway/kanban_remote_notices.py`
- `tui_gateway/session_notifications.py`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`tests/hermes_cli/test_annotation_coalesce_mitigation.py` covers flag parsing, a burst including control events and zero idle runs. `tests/tui_gateway/test_notification_shared_window.py` covers cross-chat reuse/expiry, concurrent cold polls, path/ledger scope, failures, unknown/backward clocks, bounded scopes and literal callers. `test_notification_shared_window_integration.py` covers arrivals, live clock eligibility and per-chat claim/ACK rechecks.

## Applying

Apply both holder wiring and poller opt-in. The two environment switches are reversible emergency controls, not new persistent store activation.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

The window can delay newly visible structural candidates. It does not cache a defer as an empty success, share claim ownership or freeze clock eligibility. Slow computation does not extend its nominal window.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.

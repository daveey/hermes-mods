# Gateway delivers legacy `tui` kanban notices through the local FIFO

Patches: **2475**.

## Behavior

A Desktop chat subscribes to its kanban cards as `platform='tui'`, which only the legacy Desktop backend claims. When the chat's turns have moved to the canonical gateway (LOCAL route), that backend is refused `SESSION_NOT_OWNED` and retries with backoff up to 300 s, while the gateway skipped `tui` rows, so notices waited for the whole busy chain (about an hour in the observed case). Now the gateway claims a `tui` row when the legacy backend would be refused, and delivers the notice as a kanban wake in the chat's session FIFO.

A row is claimed only when: the LOCAL adapter is connected; the chat has a live LOCAL session in this gateway (compression lineage aware); that session's ledger holds forwardable work (`started`, `queued`, `registered`, `running`) or this process holds its turn lease; the sub belongs to a profile this gateway serves; and no `local` sub for the same card and chat exists (that one delivers instead). Every refusal or error rewinds the claim with no failure count and no subscription drop; the cursor key stays `tui`. Silent kinds only advance the cursor.

## How it works

`gateway/kanban_tui_owner.py`: `owned_live_session` is the collector-thread prefilter (dict lookup plus read-only SQLite, nothing restored); `_gateway_holds_work` reads `_CANONICAL_PENDING_SQL` and the turn lease owner; `claim` calls `claim_unseen_events_for_sub` with the legacy poller's kinds; `deliver` re-checks the live route, formats the legacy text, and calls `admit_route_wake(..., "kanban-wake", ...)`, then `advance()` or `rewind()`. `gateway/kanban_watchers_notifier.py` adds `claim_platforms` (active platforms plus `tui` when LOCAL is connected) to the sub query, routes `tui` subs to `kanban_tui_owner.claim`, and sends claims flagged `tui_owner` to `kanban_tui_owner.deliver`. Related: [kanban-notice-refusal-rewind.md](kanban-notice-refusal-rewind.md).

## Code

`gateway/kanban_tui_owner.py`, `gateway/kanban_watchers_notifier.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/gateway/test_kanban_tui_owner_delivery.py` (collector claim, busy versus idle chats, turn lease, foreign lease or unknown admission, refused admission rewind, session gone at delivery, compression-tip chat id, local sibling row, other profile or no local adapter, live legacy holder and stale claim release). The legacy backend side is unchanged and untested here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- An `unknown` admission is deliberately not forwardable: waiting never resolves it, so the legacy refusal stands.
- The delivery re-check does not require work, so a turn that ended in the meantime still delivers.
- There is no `Platform('tui')` member; routing `tui` through the generic path would hit the `ValueError` branch and advance past the notice, hence the separate `tui_owner` flag.

## Plugin

Core change; no plugin required.

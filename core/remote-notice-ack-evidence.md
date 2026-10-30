# Remote notice acknowledgements require transcript evidence

Patches: 1145.

## Behavior

- A keyed remote notice is considered present only when its own key is in the transcript. A historical notice for the same task is not proof of delivery.
- `DELIVERED` (1) is recorded only after transcript-write acknowledgement or keyed transcript evidence. Obsolete/placeholder claims are `SETTLED` (2), not delivered.
- Swept rows include a reason (`delivered`, `transcript`, `stale` or `placeholder`), and dead-holder acknowledgement emits a log line.

## How it works

The legacy task-id text fallback remains only for unkeyed legacy reconciliation. Both poller and sweeper/waker paths use keyed evidence for current claims, preserving the distinction between closing a stale claim and actually notifying the chat.

## Code

- `tui_gateway/kanban_remote_notices.py`

## Tests

Source regression coverage:

- `tests/tui_gateway/test_kanban_remote_ack_evidence.py`
- `tests/tui_gateway/test_kanban_remote_notices.py`

See [mirror-1157-verification.md](mirror-1157-verification.md) for the exact files freshly executed for this publication and their results. Files listed here are source coverage references, not a claim that every file was rerun.

## Applying

Apply the complete numbered series at the frozen merge-base with `git am -3 --empty=keep`. Earlier patches include a curated integration overlay; individual entries are not promised to install independently. See [the inventory](README.md) and [verification report](mirror-1157-verification.md).

## Gotchas

Ledger settlement is not a user-visible delivery receipt. No live cross-gateway Desktop delivery was exercised for this mirror.

## Plugin?

No. This changes core lifecycle, admission, discovery, routing or its tests/workflows rather than a plugin-owned extension point.

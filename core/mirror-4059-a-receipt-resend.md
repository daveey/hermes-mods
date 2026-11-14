# Same-call worker persistence receipt resend

Mirror ordinals: 3766, 3767.

## Behavior

- A worker may resend an entry whose owner receipt outcome is unknown without immediately ending the run.
- The final form retries the exact same entry/sequence inside the same call, returning its real result before the caller proceeds; it never defers a resend to a later write.
- Exhausted unknown outcomes, owner refusals and outbox overflow retain a sticky failure.

## How it works

The first patch introduced deferred recovery state and owner-stall treatment in lease refresh. The follow-up replaces that design: `_persist_resending` makes at most two sends for one pending entry, retrying the exact receipt-budget timeout, connection errors or closed WebSocket. An exact resend can recover either a committed receipt or an entry not yet committed. The caller receives row annotations from the recovered result. Exhaustion raises `WorkerPersistenceError` and later writes send nothing. `_unknown` and lease-refresh special handling are removed; normal SQLite-lock-only tick retry semantics are restored. Child close/flush again respects the ordinary failure latch.

## Code

- `agent/runtime_session_child.py`
- `agent/runtime_session_store.py`
- `agent/turn_facade_lease.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/agent/test_runtime_session_store_receipt_timeout.py` — An owner that commits once per sequence simulates before/after-commit timeout and closed links. Assertions establish two sends but one commit, the next write uses the next sequence, transcript A/B are stored once with returned stored annotations, exhaustion prevents reflush duplication, close flush and recovered renewal remain usable, retries are bounded at two, and refusals/non-receipt timeouts are not resent.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

Read the two patches as an evolution, not two recovery modes to combine. A caller that saw a deferred exception might resend the transcript itself, so next-write recovery could duplicate rows. The bounded wait is a protocol contract, not measured owner responsiveness here.

## Plugin?

No. These changes belong to core lifecycle, persistence, or admission boundaries; a plugin cannot reliably replace those internal invariants.

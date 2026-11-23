# Async delegation: adopt owner-claimed units (mirror 5484)

## Behavior
- `adopt_unit` registers an already-claimed durable running delegation unit, owned by this exact process incarnation, under its original identity without replacing the ledger row.
- Custody is rechecked under the registry lock (`restore_owned_record` re-reads the row and compares it with the earlier read); a missing row, foreign process incarnation (PID plus start-time fingerprint), already-registered unit, non-`running` state, non-`pending` delivery state, completed row or row with an event are rejected. Ledger/delivery state is not reset; the row is retained on executor/lease/submit failure.
- Original routing keys, dispatch time and partial finished children are restored from the row; transcripts, session ids, task indexes and slot are passed through to the normal dispatch; remaining results are merged with the partial ones and finalized through the original one-shot finalizer (per the design note). New units persist `slot_key`; legacy rows use an independent conservative slot.
- Not included: parked state, ownership transfer, crash retry, new config, update-admission relaxation.

## How it works
`tools/async_delegation_adopt.py` plus a refactor of `tools/async_delegation.py` so the new-dispatch path and adoption share one admitted-scheduling function. Design note: `docs/designs/delegation-owned-adoption.md`.

## Code
`tools/async_delegation.py`, `tools/async_delegation_adopt.py`.

## Tests
Source: `tests/tools/test_async_delegation_adopt.py` (identity, state, delivery, stale/duplicate, partial children, slot, lease, reservation, capacity, closed session, monitor, ledger preservation). Not executed in this mirror pass.

## Applying
Single patch; this patch adds no caller of `adopt_unit`.

## Gotchas
Internal seam only. Per its design note, the caller must first establish resumable child custody and supply a runner for the unfinished children; the verification covers registration and finalization, not SIGKILL-after-park recovery.

## plugin?
No.

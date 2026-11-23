# Turn lease: self-orphan release recovery, contended retarget, refresh custody (mirror 5462, 5463, 5487, 5537)

## Behavior
- If an ended local turn's database lease release fails, the agent keeps a process-local receipt binding process, store, original session and exact per-turn holder. Preflight for the next turn retries only that conditional DELETE and re-reads ownership. The receipt is only created by the lease's own failed terminal release, matches only the same process, store and exact holder, and never exempts the holder from database admission (per its docstrings). A still-failing release raises `FailedTurnReleasePending`, which preflight turns into a coordination-unavailable refusal with cause `lease_self_orphan` and `holder_pid` from the receipt; a different holder keeps the refusal, which `_durable_owner_refusal` now labels `lease_foreign`.
- A turn admitted after a busy-store retry stays on the lease key it claimed. Admission redirects `agent.session_id` to the reloaded resume target only when the committed row for this holder is the one a flush to that target is checked against; compression continuations (shared key) are still followed. Admission keeps the old redirect for stores without the lease-expiry readback, and logs a warning when it declines the retarget. A test asserts non-owners still refuse.
- Lease custody events now include the refresh rowcount and bounded code-location chains for the invoking call sites. (Up to 12 frames, file basename:function:line, sanitized, never locals or arguments.) The refresh rowcount is observed in `hermes_state_compression.py`; the diff changes only logging there.

## How it works
`agent/turn_lease_recovery.py` holds the receipt/retry; `agent/turn_facade_lease.py` records failed releases and implements the retarget check; `tui_gateway/session_lifecycle.py` calls recovery in durable preflight; `hermes_state_lease_custody.py` and `hermes_state_compression.py` add refresh/provenance fields.

## Code
`agent/turn_lease_recovery.py`, `agent/turn_facade_lease.py`, `tui_gateway/session_lifecycle.py`, `hermes_state_lease_custody.py`, `hermes_state_compression.py`.

## Tests
Source: `tests/tui_gateway/test_self_orphan_turn_lease.py` (by test names: settling the exact failed release, still-failed store, different holder in the same process stays fenced, receipt cannot authorize a different custodian, racing successor re-read and fenced, live exact holder not settled, compressed tip settles the original root only, unreadable store, successful release creates no authority, unknown-store failure creates no receipt), `tests/agent/test_contended_admission_retarget.py` (uncontended admission, contended admission stays on its key, follows a compression child, non-owners refuse, a managed turn persists after contended admission), `tests/hermes_state/test_first_assistant_flush_custody.py` (one test parametrized by `state` valid/missing/mismatch and `tool_gate`: a valid holder persists once and a repeat flush adds no second message; missing or mismatched holders persist nothing and record a `turn_lease` persistence-error cause; custody log events (claim, then the refused op with store/key/holder/row_holder and a rolled-back phase) are checked). Not executed in this mirror pass.

## Applying
Four patches (5462 → 5463 tests; 5487 independent; 5537 touches the same facade file as 5462).

## Gotchas
The 5537 docstring states the failure it fixes (a first flush reading a missing lease row after a retarget to a plain child); no claim is made here about real-world incidence.

## plugin?
No.

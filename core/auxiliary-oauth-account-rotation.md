# Auxiliary class routing rotates OAuth accounts before excluding a route

Patches:

- `patches/1067-fix-routing-rotate-auxiliary-OAuth-accounts-before-e.patch`

Class auxiliary calls (sync, async, and streams before the first delivered output) now try the other accounts of the already-fenced OAuth pool before the provider/model route is excluded.

## Behavior

- 1067: an account-level 429 or billing 402 on a class auxiliary call rotates to another account inside the same OAuth pool on the same pinned provider/model, instead of treating one refused account as failure of the whole route (the old path hid it behind `No eligible fast routes`).
- Each provider/model/account is recovered at most once per logical call, so rotation is bounded.
- Paid API-key entries are never selected by rotation, even when one is first in the pool.
- A stream that has already delivered output is never replayed on another account.
- Non-account failures (overload, timeouts) do not bench the pool.
- 401 auth recovery is unchanged; this patch does not add it.

## How it works

`agent/auxiliary_class_routing.py` classifies the failure as account-scoped (429/402) and asks the pool for the next fenced OAuth entry on the same route before it falls back to excluding the route. The sync, async and pre-delivery stream paths share the same recovery bookkeeping.

## Code

`agent/auxiliary_class_routing.py`, `tests/agent/test_auxiliary_class_pool_recovery.py`, `tests/agent/test_auxiliary_class_routing.py`.

## Tests

- `tests/agent/test_auxiliary_class_pool_recovery.py`: bounded recovery, sync/async/stream paths, no replay after delivery, non-account failures leave the pool alone.
- `tests/agent/test_auxiliary_class_routing.py::test_account_recovery_keeps_exact_route_on_wire`: 6 cases (429 and 402 x sync, async, stream); account A fails, account B is on the wire, the exact model is kept, a paid first entry stays unused.
- The paid-key guard `tests/hermes_cli/test_model_routing_runtime.py::test_pool_rotation_cannot_spend_paid_key` is named in the commit's ship gate, but patch 1067 does not touch that file, so it is not part of this patch's tests.
- Author-reported ship gate: 80 tests across six affected files, lint and `scripts/check` green; a bootstrapped ask-gate probe failed on the old code and answered YES in 2.3 s with the fix. These were not re-run for this mirror.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

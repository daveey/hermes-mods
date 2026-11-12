# Bounded async delegation orphan replay and legacy local completion routes

Patches: **2360–2361**.

## Behavior

Async delegation completions handed back by a dead owner retain every offer lease and are replayed only within bounds: live claims are never stolen, attempt caps resolve even a queued copy, an unready admission defers without spending an attempt, and offer history prunes only settled rows for this home. Raw legacy Desktop completion keys are resolved to a canonical local route only when exactly one authority provably owns the local receipt (ambiguous or retiring owners fail closed), with the durable claim in the owner's own ledger.

## How it works

`tools/async_delegation.py` adds `_expire_pending_completions` and `_prune_offer_history`, with an independent `_offered_at` map. `gateway/completion_local_route.py` provides `resolve_legacy_local_event`, called from `run_notifications.py` before profile selection; explicit messaging/API routes are never rewritten. 2361 adjusts offer-lease and restart-custody tests.

## Code

`tools/async_delegation.py`, `gateway/completion_local_route.py`, `gateway/run_notifications.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tools/test_async_orphan_handback.py`, `tests/gateway/test_legacy_local_completion.py`, `tests/gateway/test_completion_delivery.py`, `tests/tools/test_async_delegation_orphan_sweep.py`, `tests/gateway/test_watch_notification_reset_pin.py`, `tests/tui_gateway/test_restored_delegation_transport.py`.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

A row in another ledger is never claimed through the receipt owner. Lost-copy warnings fire only for the first re-offer.

## Plugin

Core change; no plugin required.

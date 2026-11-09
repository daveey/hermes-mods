# Exact-pin credential recheck

Patch **2064**.

## Behavior

An exact pinned route that loses its credential through `has_available` or `select` gets one pool reload/recheck after a one-second pause, without moving to another model. Continued failure raises `PinnedRouteUnavailable` naming provider, model and reason, with a warning containing the session ID. Unpinned class routes retain their existing behavior.

## How it works

`resolve_class_runtime` records why candidates are dropped: provider disabled, no available credential, or `select()` returned no credential. When resolution fails for an exact pin without an available/prepared runtime, credential misses can trigger one retry: clear cached pools and dropped reasons, restore the original exclusions and resolve again. A remaining dropped route becomes the typed `ModelRoutingError` subclass with `provider`, `model`, `reason` and `rechecked` fields.

Existing subscription quota errors take precedence when applicable. Policy/store failures without a dropped route and errors after runtime preparation are not converted into credential retries. A disabled provider alone is not a credential miss and does not trigger the pause/reload.

## Code

`agent/model_routing_runtime.py`: `PinnedRouteUnavailable`, `PIN_RECHECK_DELAY_SECONDS`, dropped-route tracking and bounded retry.

## Tests

**Not rerun this pass.** `tests/agent/test_model_routing_runtime.py` calls `resolve_class_runtime` with an allocated exact pin and injects transient/persistent misses separately into **`CredentialPool.select` and `has_available`**. It checks recovery to the same route, typed reason and session warning, exactly one retry, and unpinned-class non-retry. Sleeps are mocked. Disabled-provider/quota precedence and live API-server ingress are not newly exercised by these tests.

## Applying

Apply 2064 in [core replay order](README.md), with the existing class routing, credential pool and exact-pin state machinery.

## Gotchas

This is a bounded recheck, not general provider failover, quota recovery or indefinite refresh retry. A permanent missing credential still refuses the turn, now with a specific typed reason.

## Plugin eligibility

Not plugin-only: exact-pin allocation and credential selection are core routing authority boundaries.

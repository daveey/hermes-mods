# App-owned native session display captures

Patches: **5907–5908**.

## Behavior

Linux serve startup defaults to in-process session display facts. Session list, sidebar and stamps HTTP routes read captured immutable JSON rather than running canonical readers on a warmed request. A cold or stale view returns retryable 503 and queues demand; it does not synchronously scan or silently fall back. Invalid list enums retain 400 and named profiles outside the authorized discovered roster return 404.

`HERMES_FACTS_SESSIONS=0` (off/false/no also accepted) selects legacy routing at the next natural startup. Other platforms remain legacy because this native watcher is Linux-only.

## How it works

One app-owned `SessionsRuntime` composes `ChangeFeed`, `SessionsSource`, native state/config watches, `FactStore`, `InProcessFacts`, lifecycle authority and an app-local HTTP binding. It publishes the default list view before switching to facts mode. Canonical serializers, SQL ordering/filters/counts/pins/deep offsets and annotation readers are reused on the feed owner, not inside an HTTP request or pure reducer.

Demand is bounded to 32 retained selectors per endpoint with read-touch LRU, sharing a 12 MiB retained-body cap. A capture has a 4 MiB/five-second envelope; the elapsed check does not interrupt a reader already running. Fair dirty ordering, five-second per-selector failure backoff and stale prior bytes isolate a bad view. Pacing allows at most five capture passes per second and no more than half of serial wall time in captures.

Quiet annotations/time/roster reconcile every 30 seconds plus queue/capture latency. Active rows schedule their captured activity boundary. Newly discovered profiles get native watches at the next boot; until then their updates rely on reconciliation. Facts expire after 60 seconds if publication stalls.

The sessions lifespan is inside the annotation-owner lifespan. Close drains admission and joins capture/watch before releasing binding/handle/workers. Failed closure retains app custody and blocks replacement ownership.

## Code

- `hermes_cli/sessions_native_source.py`: fair selector inventory, capture, expiry and byte accounting.
- `hermes_cli/sessions_native_startup.py`: runtime, middleware and startup/teardown.
- `hermes_cli/sessions_fact_routes.py`: in-memory read-touch hint.
- `hermes_cli/web_server.py`, `web_server_app.py`: app composition.
- `docs/designs/sessions-display-facts.md`: source design and disclosed limits.

## Tests

**Source regression coverage; not executed in this mirror pass.**

- `tests/hermes_cli/test_sessions_native_source.py`: failure isolation/fairness, endpoint LRU, cooldown, reconciliation, over-budget capture, eviction races, byte caps, activity deadlines, stale failure and closure.
- `tests/hermes_cli/test_sessions_native_startup.py`: actual SessionDB mutation visibility, warmed HTTP I/O tripwires, mounted list/sidebar/stamps parity, cold demand, profile scope, rollback, lifespan reentry and real app middleware composition.
- `tests/gateway/test_native_http_auth.py` and `tests/hermes_cli/test_subagent_session_activity.py`: integration clients wait for a cold sidebar projection to become ready and reject unrelated failure responses rather than assuming immediate 200.

No live routed latency, deployment activation, cross-database coherence or comprehensive native annotation freshness is claimed here.

## Applying

Use `core/README.md` snapshot replay, including earlier fact delivery and annotation composition dependencies. For a port, preserve startup-only binding selection and the nested lifespan teardown order. Allow cold 503 in clients/tests instead of extending Desktop timeouts. Enable or roll back at a natural boot; do not restart active serves just to activate this feature.

## Gotchas

This is in-process rendered-field composition, not dedicated fact-host activation or a per-row pure fact reducer. Source vectors identify captures, not a coherent multi-database transaction. Capture duty is a wall-time scheduling bound, not a measured CPU guarantee. Old bytes marked stale are not fresh success. This feature does not change Desktop timeouts or the separately published lanes stale-while-revalidate plugin.

## plugin?

Core owns canonical session routing, authorized profile discovery and application lifecycle. Plugins can consume/present facts but cannot replace startup authority with a query-selected binding.

# Gateway daemon tests verify their own listener and settled receipt

Patches: **2878, 2897, 2907**.

## Behavior

Test-only: daemon-backed API tests verify that the identified daemon owns its listener before sending requests, and gateway result tests wait for the receipt contract to settle rather than treating a terminal SQL row as a published result.

## How it works

- **2878:** `api_daemon` chooses a port for each boot, writes matching configuration and waits on that daemon PID’s `gateway_state.json` API-server state, avoiding a sibling fixture’s identical-key listener.
- **2897:** `settled_receipt` polls `prompt.receipt` itself until terminal/unknown inside `RPC_DEADLINE`; use it in managed construction, launch and workspace-readiness tests. The SQL terminal commit can precede completion-frame publication.
- **2907:** listener probing returns the settled record; reboot only on `api_server_port_in_use`, not every fatal code. Non-port failures and listener timeout include restart.log diagnostics.

## Code

Test-only changes; no production implementation is modified.

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_api_hosted_daemon.py` uses the owned API-port fixture across restart. `test_managed_worker_construct.py`, `test_managed_worker_launch.py` and `test_session_policy_workspace_readiness.py` consume settled receipts. `test_api_daemon_fixture.py` pins one-boot non-port failure, repeated port-race retries and timeout log text.

- `tests/gateway/fixtures/local_recovery_probe.py`
- `tests/gateway/test_api_hosted_daemon.py`
- `tests/gateway/test_managed_worker_construct.py`
- `tests/gateway/test_managed_worker_launch.py`
- `tests/gateway/test_session_policy_workspace_readiness.py`
- `tests/gateway/test_api_daemon_fixture.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

An identify-ready daemon need not have a usable API listener. A database row saying terminal need not yet expose receipt result. These fixture changes do not modify runtime publication semantics or make unrelated fatal errors retryable.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.

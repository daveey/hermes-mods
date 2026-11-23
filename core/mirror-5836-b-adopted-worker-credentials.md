# Execution-bound managed-worker credentials

Source patch ordinals: 5539–5542.

## Behavior

Managed workers start with an explicit operational environment, not arbitrary gateway/profile secrets. Private inference and declared-tool credentials are hydrated only after authenticated adoption of the exact live execution. Desktop completion delivery retains its owner without forwarding backend authentication.

## How it works

`stage_credentials` replaces inline secrets with frozen-policy placeholders and keeps material on the authority. The `worker.credentials` RPC checks execution scope, generation, PID/birth, secret and owner epoch before resolving it; teardown releases the handle. `_hydrate_worker_config` allows filling secrets only when stripping them reproduces the frozen policy. Provider routes include configured fallbacks/auxiliaries; tool credentials come from selected registry entries' `requires_env`. The nonsecret `desktop_completion_owner_v1` flag carries completion destination separately.

## Code

- `gateway/session_worker_credentials.py`
- `gateway/session_managed_worker.py`
- `gateway/session_worker.py`
- `gateway/session_controls.py`
- `agent/managed_worker.py`
- `agent/safe_worker_policy.py`
- `agent/runtime_session_store.py`
- `tools/process_registry_obligations.py`
- `tui_gateway/contracts/canonical.py`
- `apps/shared/src/gateway-contract.generated.ts`
- `apps/shared/src/gateway-contract.openrpc.json`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`tests/gateway/test_managed_worker_least_privilege.py` covers startup allowlisting, secret-free bootstrap, route-scoped keys and logical profile IDs. `test_managed_worker_env_compat.py`, `test_managed_worker_env_boundary.py`, `test_managed_worker_profile_env.py` and `test_managed_worker_route_key.py` in that directory cover profile isolation and declared tool compatibility. `tests/agent/test_safe_worker_policy.py` checks placeholder-only hydration; `tests/gateway/test_worker_admission_reservation.py` covers adopted execution integration; `tests/tui_gateway/test_tui_completion_obligation.py` covers completion custody. The handler ledger is regenerated; fixture consumers for MCP reload, native gateway and safe mode are updated.

## Applying

Apply the worker, authority RPC, canonical contract and generated client changes together, followed by the compatibility fixes. This is an ordered series, not an independently deployable client-only API addition. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

Logical profile IDs deliberately have no filesystem secret scope. The explicit allowlist preserves proxy, certificate, socket and other operational inputs; it is not permission to inherit arbitrary settings. Tool credentials must be declared. The completion flag is not an authentication token.

## plugin?

Core change. Plugins may declare tool credential requirements through the registry, but cannot replace execution-bound adoption checks.

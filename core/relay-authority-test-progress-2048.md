# Relay authority test progress and FIFO policy

Patches **2050, 2059**.

## Behavior

The authenticated relay integration test tolerates descheduled runners without changing production timeouts or delivery semantics. Its busy-message scenario explicitly selects FIFO queue mode and waits until the actual busy agent is published before sending the queued message.

## How it works

Patch 2050 introduces a 60-second progress watchdog, a model-gate watchdog twice that size and an outer subprocess watchdog five times that size. Condition-based waits return immediately on progress. The shared HTTP model fixture accepts a per-server `release_timeout`, retaining its previous default elsewhere.

Patch 2059 writes `display.busy_input_mode: queue` into the isolated test config and uses `restore_native_source` plus `_peek_session_state` to await `state.turn.agent is warm_agent`. This prevents the startup sentinel from accidentally bypassing redirect/steer logic in a test intended to prove separate FIFO turns.

## Code

Only `tests/gateway/test_relay_authority.py` and `tests/gateway/fixtures/shared_authority_peer.py` change.

## Tests

**Not rerun this pass.** `tests/gateway/test_relay_authority.py::test_authenticated_relay_uses_canonical_admission` launches the isolated peer probe. Its entry paths include authenticated connector WebSocket ingress, durable admission, gateway session-resume RPC, replay/deduplication, FIFO busy routing, delivery settlement and revoked-auth negative controls, using a loopback model stand-in rather than a live provider.

## Applying

Apply 2050 then 2059 within the [published core replay](README.md), with the existing relay authority harness and shared peer fixture.

## Gotchas

These watchdogs are hang limits, not latency acceptance thresholds. Queue mode is test-local, not a change to user defaults. Model blocking alone is not proof that the event loop has published the busy agent.

## Plugin eligibility

Not applicable: this is core test-harness maintenance, not an installable behavior plugin.

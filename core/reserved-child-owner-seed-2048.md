# Reserved child configuration owner seeding

Patches **2057–2058**.

## Behavior

Reserved child initialization fills missing constructor configuration through the owner's seed API rather than overwriting authoritative first-turn values with a patch. Existing values, including explicit nulls, remain authoritative. Worker and gateway CLI store adapters classify this new agent-loop call without granting workers owner-level routing or lineage writes.

## How it works

`AIAgent._ensure_db_session` builds a non-None constructor seed and calls `seed_session_model_config` on a reserved row. The existing owner API merges missing keys. `RuntimeSessionStore.seed_session_model_config` validates session scope then intentionally does nothing: no write, journal sequence advance or failure latch. Another session is refused. `GatewaySessionStore` adds the method to `_GATEWAY_OWNED`, making the proxy call a no-op because the gateway owns the actual write.

## Code

- `run_agent.py`: reserved-row initialization.
- `agent/runtime_session_store.py`: scope-checked worker no-op.
- `hermes_cli/gateway_session_store.py`: gateway-owned classification.

## Tests

**Not rerun this pass.**

- `tests/tools/test_delegate_transcript_readiness.py::test_reserved_child_init_preserves_early_owner_config`: builds a reserved child, seeds early owner values including null, stamps a prompt epoch and invokes initialization; checks lineage and all authoritative values survive.
- `tests/agent/test_runtime_session_store_surface_parity.py::test_seed_is_an_owner_write_the_worker_never_journals`: direct worker seed and adopted-row probe, unchanged journal/config, no warning or latched failure, foreign-session refusal and subsequent sidecar-write success. The file also checks agent-loop method surface classification.
- `tests/hermes_cli/test_gateway_session_store.py::test_reserved_child_first_turn_seed_is_gateway_owned`: gateway CLI adapter seed and `AIAgent._ensure_db_session`, no gateway calls and creation marked complete. Its surface-classification test guards new methods.

## Applying

Apply 2057 and 2058 together in [published replay order](README.md), after owner seeding and reserved-child infrastructure. The adapter follow-up is required; the delegate test alone does not establish adapter parity.

## Gotchas

The worker no-op is deliberate, not persistence success for arbitrary defaults. Owner-reserved routing/lineage remains owner-controlled; sidecar patching still works. Neither adapter test is a live gateway end-to-end deployment check.

## Plugin eligibility

Not plugin-only: this changes agent initialization and persistence authority boundaries in core stores.

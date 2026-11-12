# Compact routed requests before context attestations reject them

Patches: **2354**.

## Behavior

For class-routed agents, proactive compression now honors the selected route's attested request budget even when catalog or usage estimates disagree, and an otherwise eligible route refusal that only needs a smaller request is treated as bounded context overflow and compacted once. Billing, funding, mixed-capability, withdrawn-route and irreducible-budget refusals stay terminal; error text alone never authorizes compression.

## How it works

`output_reservation` gives one output-runway calculation shared by route admission and proactive compaction; `route_preflight_pressure` applies the attestation; `RouteContextOverflow` marks a refusal that must not authorize failover. `threshold_tokens` is evaluated lazily including the route budget, an aux limit is kept when a route cap is removed, and a runtime switch drops the old ceiling. Output-only overflow does not destroy history.

## Code

`agent/model_routing.py`, `model_routing_request.py`, `context_compressor.py`, `context_compressor_threshold.py`, `context_engine.py`, `conversation_compression.py`, `conversation_compression_feasibility.py`, `error_classifier.py`, `turn_preflight_gate.py`, `turn_request_assembly.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/agent/test_route_context_budget.py`, `test_route_context_budget_edges.py` and `test_route_context_recovery.py` (attested cap over metadata window, per-request reservation, unrouted unchanged, compact-then-send, non-context refusals terminal, plugin engine cap/reset).

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

Applies to routed requests only. Compression cannot fix an output-runway shortage; those refusals remain errors.

## Plugin

Core change; no plugin required.

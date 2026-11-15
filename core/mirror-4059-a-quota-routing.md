# Desktop quota-wall deferral with session routing identity

Mirror ordinals: 3754, 3755.

## Behavior

- Desktop agent construction can defer a far subscription-quota wall for a router-picked model to the existing within-class rerouter.
- Explicit user switches, short walls, session-less builds and private endpoint/key overrides keep their refusal semantics.
- The existing fallback notice is consumed once rather than becoming a permanent notice on every build.

## How it works

`agent_factory._resolve_runtime_or_defer_wall` catches only `SubscriptionQuotaUnavailable`. With no runtime overrides it asks `class_routing.walled_pin_deferral` under `routing_session(session_id)`; a refused deferral re-raises the original wall. `_make_agent` passes `session_id or key` to runtime resolution. Deferral removes internal class markers and converts the one wall notice into `_fallback_notice`, leaving durable route selection to the normal managed build. The second patch updates start-toolset test stubs to accept the new resolver argument.

## Code

- `tui_gateway/agent_factory.py`
- `tui_gateway/class_routing.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/agent/test_walled_default_pin.py` — Resolver/build cases cover legacy and current default router pins, alternative within-class selection and its walled-from metadata, one-time notice consumption, and refusal controls for explicit/short/session-less/endpoint cases or no available peer model. A source assertion checks _make_agent forwards the session identity; it is not a full Desktop UI test.
- `tests/tui_gateway/test_start_toolsets.py` — Existing _make_agent/start-toolset fixtures accept the session identity passed by the updated resolver; this is compatibility maintenance, not a separate rerouting feature.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

Only subscription quota walls accepted by the existing deferral policy move. This does not move an explicit user pin or promise another provider has capacity. No live account, routing or Desktop behavior was exercised by this pass.

## Plugin?

No. These changes belong to core lifecycle, persistence, or admission boundaries; a plugin cannot reliably replace those internal invariants.

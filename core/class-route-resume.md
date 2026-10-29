# Class routes: gateway rows, API reuse and resume ordering

Patches:

- `patches/0971-fix-gateway-an-agent-adopting-the-gateway-s-bare-ses.patch`
- `patches/0974-fix-api-a-class-request-on-a-reused-session-serves-i.patch`
- `patches/1012-fix-routing-restore-resumed-intent-before-turn-selec.patch`

Three ways a `class:` model selector was lost or mis-served.

## Behavior

- 971: when the lease adopts the gateway's bare session row it seeds the agent's init `model_config` (`SessionDB.seed_session_model_config` fills only missing keys atomically), so gateway rows under `class:deep` resume as a class, not a pin. Context-length resolution uses `class_context_length` for a class marker.
- 974: an explicit `class:` request on a reused API session supersedes a different persisted selection; `/model` and a confirmed lock still win.
- 1012: resumed intent is restored before turn selection and stale class routes recover.

## How it works

Each fix sits at the point where the row or request meets routing state.

## Code

`agent/turn_facade_lease.py`, `hermes_state_sessions.py`, `model_tools.py`, `gateway/platforms/api_server_class_routing.py`, `agent/model_routing_state.py`, `hermes_cli/cli_chat_turn_mixin.py`.

## Tests

`tests/gateway/test_class_row_provenance.py`, `tests/tools/test_tool_search_context_provider.py`, `tests/gateway/test_api_class_session_reuse.py`, `tests/agent/test_model_routing_stale_recovery.py`, `tests/hermes_cli/test_chat_resume_route_order.py`.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

None known beyond the notes above.

## Plugin

None.

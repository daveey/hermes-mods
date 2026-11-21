# Dashboard: model.options handed to a warm always-on successor

Mirror entries 5161-5165.

## Behavior

- When an always-on backend has been superseded but is still busy (its already-open connection is not moved by the proxy), `GET /api/model/options` and the `model.options` RPC now return the successor's payload through a loopback control read. Writes and prompts are never forwarded; a missing successor still answers 503; an active or warming backend never hands off.
- 5163: the handoff is session-scoped: the successor profile is derived from the session's profile home (unknown session scope is refused), the live agent model overlays the result, the query uses a shared lowercase-bool path, the successor port must be in the always-on backend port set, and the REST path runs the blocking read in a threadpool so the event loop is not stalled.
- 5164: if the live provider differs from the successor's provider rows the handoff fails closed; a matching provider overlays only the live model; empty or missing live provider preserves successor rows (5165).
- 5162: the loopback control-read helper is exempt from the session-token route path scan; its paths are not registered as session-token service routes.

## How it works

- `hermes_cli/always_on_rollover.py`: allowlisted `_loopback_control_get`, `handoff_control_read`, `pop_backend_port` binding.
- `hermes_cli/web_routers/models.py` (REST), `tui_gateway/methods_complete.py` and `methods_complete_helpers.py` (RPC, profile derivation, overlay).

## Code

- `hermes_cli/always_on_rollover.py`
- `hermes_cli/web_routers/models.py`
- `tui_gateway/methods_complete.py`
- `tui_gateway/methods_complete_helpers.py`

## Tests

Source tests (not executed in this mirror pass):

- `tests/hermes_cli/test_always_on_control_handoff.py`: handoff to successor, no handoff when active/warming, no write/prompt forwarding, non-JSON/failed read is not a bypass, 503 without successor, event-loop non-blocking, unknown-session refusal, profile and flag forwarding, provider-mismatch refusal, non-always-on port refusal, empty live provider.
- `tests/hermes_cli/test_dashboard_session_token_routes.py` (5162).

Live busy-predecessor/client behavior on a real deployment is untested here (the commit text lists it as outstanding).

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Read-only options only; no queue migration or model switching.
- Handoff needs a warm successor on an allowlisted loopback port.

## Why core instead of a plugin

The proxy, rollover and picker RPC are core server components; the handoff must run inside the stale backend's own request handlers.

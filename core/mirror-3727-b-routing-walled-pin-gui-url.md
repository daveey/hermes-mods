# Routing: a Desktop chat's own provider URL does not block the walled-pin reroute

Patches: **3727**.

## Behavior
- A Desktop GUI chat whose creation request carries its provider's own endpoint no longer causes a quota-walled default pin to refuse each turn; routing defers to the agent build's in-class reroute (e.g. builds on another model of the same class).
- A launch key, a foreign endpoint, or an explicit switch pin still refuses.
- One WARNING is logged per wall episode and reason when a wall of 6 h or more is not deferred, with reason `key`, `endpoint`, `explicit`, `no-target` or `no-session`. Short or unknown walls refuse quietly.

## How it works
3727 changes `route_runtime` to use `overrides_endpoint()` (as the managed resolver does) instead of treating any `base_url` as blocking deferral. `model_routing_walls.py` gains the non-deferral logging with per-episode dedup; `session_worker_construct.py` is adjusted accordingly.

## Code
- `agent/model_routing_walls.py`
- `gateway/session_worker_construct.py`
- `tests/agent/test_walled_default_pin.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/agent/test_walled_default_pin.py`: `test_gateway_gui_chat_on_its_own_codex_url_builds_on_opus`, `test_gateway_gui_chat_explicit_switch_pin_still_refuses_and_says_why`, `test_gateway_gui_chat_on_a_foreign_endpoint_still_refuses`, `test_walled_pin_with_a_launch_key_logs_why_it_was_not_deferred`, `test_walled_pin_on_a_foreign_endpoint_refuses_before_deferral_and_says_why`, `test_walled_pin_outside_a_routing_session_logs_no_session`, `test_walled_pin_with_nothing_else_in_its_class_logs_no_target`, `test_a_short_or_unknown_wall_refuses_quietly`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A foreign base_url is normally refused earlier by the managed resolver; its `route_runtime` branch is pinned only by an injected-wall test.

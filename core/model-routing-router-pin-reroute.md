# Model routing: a router-picked pin reroutes in-class on a far quota wall

Patches: **1916** (`16b0f02c53`, `[daveey] fix(routing): a router-picked pin is not David's pin; a far quota wall reroutes it in-class`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- Every new route records `pin_source` (`explicit` for an honoured switch or a model the user named, `default` for a router pick or config default); a concrete echo no longer upgrades an allocation to an exact pin. A `default` pin whose subscription wall resets 6 h or more away is re-selected in its class (`same-class-failover`, `walled_from` recorded). Explicit pins, Kanban card pins and walls under 6 h still hold.

## How it works

See Code. Legacy rows count as explicit only when they record `explicit-switch`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1916: Problem: MacBook Desktop chat 20261026_000514_23ef49 went dead for ~33 h from 11-02 13:59 PT. Every turn failed before it started with "Subscription quota unavailable for openai-codex/gpt-6-astra; resets at 1793774268" while claude-opus-5-5 in the same `deep` class was available.

## Code

`agent/model_routing.py`, `agent/model_routing_managed.py`, `agent/model_routing_runtime.py`, `agent/model_routing_state.py`, `agent/model_routing_walls.py`, `gateway/run_turn_prepare.py`, `gateway/run_turn_runner.py`, `gateway/session_worker_construct.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/agent/test_model_routing_state_contention.py`
- `tests/agent/test_walled_default_pin.py`: `test_echo_of_the_router_pick_stays_a_class_allocation`, `test_walled_legacy_default_pin_moves_to_anthropic_deep`, `test_rerouted_chat_stays_on_its_reroute_after_the_wall_clears`, `test_walled_explicit_switch_pin_still_holds`, `test_walled_named_model_on_a_new_session_still_holds`, `test_walled_config_default_on_a_new_session_moves`, `test_wall_under_six_hours_still_holds`, `test_kanban_card_pins_are_not_moved`, `test_gateway_local_turn_with_legacy_astra_pin_builds_on_opus`, `test_gateway_local_turn_with_explicit_switch_pin_still_refuses`, `test_gateway_explicit_pin_reply_names_the_reset_and_model_command`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.

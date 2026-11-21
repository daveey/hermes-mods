# Managed model pins at offer and session-creation boundaries

Mirror entries: 5079, 5080, 5081.

## Behavior

- `model.options` and refreshed `model.save_key` provider rows omit concrete pins rejected by the managed subscription policy.
- `session.create` rejects a policy-refused stored pin with JSON-RPC `-32602` before creating session state, with a short message and structured allowed-pin data.
- Bare `custom`/empty provider choices are re-derived exactly as the turn resolver derives them; class selectors remain routable. Policy-off or unreadable-policy behavior remains unchanged.

## How it works

`model_options_managed.py` shares refusal/filtering helpers with picker and session admission. Filtering follows memo lookup on a copy, so a policy edit affects memo hits without corrupting cached full inventory. All relevant model-ID lists are narrowed and counts recalculated.

`rederived_override_provider()` factors provider identity derivation out of agent construction. Both the admission gate and turn resolver use it with the profile's config default. Session admission judges what `create_overrides` will actually store, before catalog-coherence checks, rather than the raw incoming choice.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `hermes_cli/inventory.py`
- `hermes_cli/model_options_managed.py`
- `tui_gateway/methods_complete.py`
- `tui_gateway/methods_session.py`
- `tui_gateway/methods_session_model_guard.py`
- `hermes_cli/runtime_provider_custom.py`
- `tui_gateway/agent_factory.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Managed picker tests cover all ID lists, malformed shapes, policy changes across memo hits, save-key refresh and unreadable policy. Session-create tests cover pre-state refusal, allowed choices, profile policy, picker/create agreement and concrete/auto/custom/empty provider agreement with the turn resolver. Follow-ups pin re-derived provider, config-default behavior and stored-versus-raw override. Real authenticated provider catalogs and interactive Desktop picker/toast behavior are untested here.

Source test/coverage paths:

- `tests/hermes_cli/test_model_options_managed_pins.py`
- `tests/tui_gateway/test_session_create_managed_pin_guard.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

Unreadable policy is not silently made stricter by this change: the turn can still report its own error. A catalog ID alone does not prove a concrete provider pin is registered or unambiguous. Do not reuse the old early acceptance for any route sharing a model name.

## Why core instead of a plugin

Model offering, stored session overrides and runtime provider derivation are core admission boundaries; a UI plugin can hide a row but cannot prevent other callers creating an unusable session.

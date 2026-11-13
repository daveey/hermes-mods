# /model refuses concrete pins the managed routing registry rejects

Patches: **3503**.

## Behavior
- With `model_routing.enforce_subscription_only` on, `/model` (or a picker switch) to a model absent from the host's registry is refused at switch time instead of being saved and failing every later turn.
- The rejection names the model, the provider, every allowed pin, and the way out (`/model`, or the host's `model_routing.routes`).
- Registered models still switch; with the policy off any model is accepted; class selectors pass the pin check unchanged.

## How it works
3503 rewrites the `ManagedPinRejectedError` text in `agent/model_routing_managed.py` (class, `.allowed`, `.registry_empty` unchanged) and adds `hermes_cli/model_switch_managed.py`, which `switch_model` calls on the name it would persist, before the credential step (which previously swallowed the resolver error).

## Code
- `agent/model_routing_managed.py`
- `hermes_cli/model_switch.py`
- `hermes_cli/model_switch_managed.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_model_switch_managed_pin.py`: `test_rejection_names_the_model_provider_and_every_allowed_pin`, `test_switch_to_unregistered_model_is_refused_naming_allowed_pins`, `test_switch_to_registered_model_still_switches`, `test_policy_off_accepts_any_model`, `test_class_selector_passes_the_pin_verdict`.
- The CLI `/model` and Desktop picker paths are untested individually; tests call `switch_model`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
Only hosts with the managed subscription-only policy are affected.

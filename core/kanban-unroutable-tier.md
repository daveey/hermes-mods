# Keep the profile model when a chosen class is unroutable

Patch 0654 (`c92c6a0099`).

## Behavior

An automatically tier-chosen class without an eligible route on this host leaves the card's profile model in place instead of starting a worker with an unusable class. The `model_tier` event records `<reason>+unroutable:<class>:<why>`. Explicit card model pins are unchanged.

## How it works

`class_unroutable_reason()` uses the allocator's non-allocating eligibility filter: valid enabled policy and an eligible route on an enabled provider. `apply_model_tier()` applies this check to default and strong tiers before changing the card override.

## Code

`hermes_cli/kanban_model_tier.py`.

## Tests

`tests/hermes_cli/test_kanban_class_unroutable.py` covers a real dispatch tick with missing routes, ineligible routes, a disabled provider and disabled routing; a routable class stays selected; an unroutable review-lane strong tier keeps the profile model; an explicitly pinned class is untouched. `tests/hermes_cli/test_kanban_class_routing.py` now supplies a routable-host fixture for its tier-selection tests. These are upstream regression coverage, not a live provider-send probe.

## Applying

Apply 0654 after the model-tier and model-class-routing patches with `git am -3`.

## Gotchas

Keeping the profile model does not guarantee that model's credentials work. The fallback deliberately does not override an explicit card pin or allocate a route merely to test eligibility.

## plugin?

No; core dispatch model selection.

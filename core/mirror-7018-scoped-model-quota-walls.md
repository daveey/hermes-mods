# Scoped model quota walls

Patch coverage: 6642, 6643.

## Behavior

A quota wall recorded from a *model-specific* subscription-quota receipt used to be mirrored as a provider-wide wall, and refusal fallback excluded the whole provider before checking candidates, so a healthy sibling model on the same provider could never be admitted. Now `provider/model` keys are persisted only for explicit model-naming quota evidence (`Subscription quota unavailable for <provider>/<model>` naming exactly one model for that provider, taken from the owned attempt's receipt or, on the legacy exit-text path, from that exit text); broad, ambiguous and unknown 429 evidence stays provider-wide, and `class:` selectors never form a model key. A matching wall is the later of the provider-wide and the exact-model deadline; a sibling model's wall never matches. Episode mirrors carry a `wall_model`, release drops only the matching key (a scoped episode cannot remove an independent provider-wide wall), pruning requires the walled model itself to serve, and the release probe (6643) checks every scoped model so one serving model cannot release another model's confirmed or unknown wall. Scoped release and the broad-wall fixture contracts are preserved.

## How it works

`hermes_cli/kanban_provider_walls.py` (`wall_key`, `wall_until`, `quota_model`, `receipt_wall_model`, `record_wall`, `_wall_route`, `drop_wall`) derives the wall scope; related release/episode/probe modules (`kanban_quota_episode*.py`, `kanban_quota_probe.py`, `kanban_quota_release.py`, `kanban_class_walls.py`, `kanban_refusal_fallback.py`, `kanban_pin_reroute.py`, `kanban_ratio_routing.py`) honor the scoped key.

## Code

`hermes_cli/kanban_provider_walls.py` plus the sibling modules above.

## Tests

Read from source; **not executed here**. `test_kanban_model_quota_walls.py`, `test_kanban_quota_episode(_walls).py`, `test_kanban_quota_auth_churn.py`, `test_kanban_ratio_routing.py`. Real provider responses are **untested**.

## Applying

Apply 6642 then 6643.

## Gotchas

- Only explicit model-naming evidence narrows a wall; ambiguity stays provider-wide.

## Why core, not a plugin?

Dispatch/quota routing is core.

# Kanban walls: multiplexed pool reads and review-reroute longest wall

Patches: 2799, 2809.

- **2799** (`a05ea10062`, test(kanban): cover mutant M4 (review alternate walled only in the live pool); a short own wall never hides a longer known wall)
- **2809** (`bb0a5bbd35`, fix(kanban walls): read the live OAuth pool under the home's secret scope in a multiplexing dispatcher, and warn when a pool read fails)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- 2809: in a multiplexing gateway the dispatcher tick runs with no secret scope, so `load_pool` raised `UnscopedSecretError` every tick and `live_pool_serving` swallowed it (False). A disproved hinted episode kept holding a card and `prune_disproved_walls` kept a false wall. `live_quota_wall`, `live_quota_room` and `live_pool_serving` now bind the home's own scope when multiplexing with none bound (a routed turn's scope is left alone). A still-failing pool read logs a WARNING (provider and exception type) at most once per provider per 15 minutes.
- 2799: `pinned_route` and `record` read the known wall only when the card had no own wall on the pin provider, so a 2 h own wall hid a 55 h file wall. `_with_known_wall` now merges them, later reset winning, keeping the card's own episode id. Also pins that a review-unpinned alternate walled only in the live pool is skipped (mutant M4).

## How it works

- `hermes_cli/kanban_provider_walls.py` (owner_secret_scope binding, once-per-window warning) and `hermes_cli/kanban_pin_reroute.py` (`_with_known_wall`).

## Code

- `hermes_cli/kanban_pin_reroute.py`
- `hermes_cli/kanban_provider_walls.py`

## Tests

- `tests/hermes_cli/test_kanban_provider_walls_multiplex.py` (launch pool visible in multiplexing tick, exhausted pool reported, disproved wall pruned, bound turn scope left alone, warn once per window); `tests/hermes_cli/test_kanban_pin_reroute.py` (`test_a_short_own_wall_never_hides_a_longer_known_wall`, `test_review_unpinned_skips_an_alternative_walled_only_in_the_live_pool`).

Test files touched by these patches:

- `tests/hermes_cli/test_kanban_pin_reroute.py`
- `tests/hermes_cli/test_kanban_provider_walls_multiplex.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- The source notes ran the wider importing suites; this mirror did not.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.

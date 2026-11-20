# Kanban: default-tier cards by planned budget share (ratio routing)

Mirror entries 5017, 5018 and 5019.

## Behavior

Unpinned default-tier cards start on the governed pool furthest below its planned `v2` budget share (`kanban_ratio_routing`). `paced_route` no longer moves those unpinned default routes; the governor may still move a card that resolves onto a held pool at claim time (`kanban_pace_reroute`). Mandatory pins, class selectors and `require_model: true` cards keep their route.

5018: a pick loads each candidate credential pool once; a pool with a stale reading is never picked; a tick whose ratio memo cannot start fails open and still runs; recording the route never raises when the memo key fails.

5019: `_apply_model_tier` complexity stays at main by extracting a ratio-block helper. Behavior change: untested (refactor-only mail).

## How it works

Ratio routing reads the stored `v2` budget and pure config (no usage read, no hold, no write). `ratio_route_models` names the model each pool runs. Queue notes and stall notices name a ratio pick or a pace move.

## Code

- `hermes_cli/kanban_ratio_routing.py`
- `hermes_cli/kanban_paced_reroute.py`
- `hermes_cli/kanban_model_tier.py`
- `hermes_cli/kanban_dispatch_owner.py`
- `hermes_cli/kanban_pacing_defaults.py`
- `hermes_cli/kanban_queue_position.py`
- `hermes_cli/config_defaults.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

Source tests (not executed here):

- `tests/hermes_cli/test_kanban_ratio_routing.py` (choose by deficit, ineligible/stale pools skipped, class-default concrete pick, fail-open, credential load-once, preview/admission agree)
- `tests/hermes_cli/test_kanban_model_tier_paced_reroute.py` (`test_paced_route_never_moves_an_unpinned_default_route`)
- `tests/hermes_cli/test_kanban_pace_reroute.py`, `tests/hermes_cli/test_kanban_llm_pacing.py`, `tests/hermes_cli/test_kanban_llm_pacing_followups.py`

5019 has no test file of its own.

## Applying

Follow the full segmented recipe in the core mods [README](README.md) ("Applying"); never apply these mails on their own.

## Gotchas

- Ratio routing off or a failed probe keeps today's route.
- A walled ratio pick still moves via its quota episode; that is not a pacing hold.

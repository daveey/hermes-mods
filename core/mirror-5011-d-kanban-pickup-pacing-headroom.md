# Kanban pickup: count the pass's own picks against provider pacing, coalesce count noise

Patches: **4674, 4675, 4687**

- 4674 (`348ba7f6a9`): [daveey] fix(kanban): pickup counts its own picks against a provider's pacing limit
- 4675 (`f1c1971307`): [daveey] test: opt pickup headroom cases into start pacing
- 4687 (`4d44015bdf`): fix(kanban): coalesce pickup run-count changes within capacity bands [daveey]

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- A single pickup pass copies at most one card per unit of a paced provider's headroom (limit minus running minus already picked this pass), instead of copying a whole batch that local dispatch would then pace-hold and evict. Batch room remains for other providers, and a pinned card is reported as paced rather than falsely "budget exhausted".
- Pickup incident evidence no longer differs on every busy-worker tick: the rendered registry count paired with a numeric cap is normalized, so only cap crossings produce new comments. Raw delivered observations keep exact counts.

## How it works

- `kanban_llm_pacing_dispatch.pickup_hold` gains a per-pass notion of picks already spent; `kanban_worker_node._evaluate` threads it through candidate evaluation. Alternate-target picks spend headroom while claim remains unreserved.
- `kanban_pickup_observation.py` normalizes only count-with-cap text; reasons, targets and hold fields are preserved.
- 4675 opts the headroom tests into start pacing.

## Code

- `hermes_cli/kanban_llm_pacing_dispatch.py`
- `hermes_cli/kanban_worker_node.py`
- `hermes_cli/kanban_pickup_observation.py`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/hermes_cli/test_kanban_pickup_paced_window.py`: modified only
- `tests/hermes_cli/test_kanban_pickup_pacing_headroom.py`: `test_a_pass_copies_one_card_per_unit_of_provider_headroom`, `test_the_pinned_card_is_paced_not_budget_exhausted_when_the_batch_also_fills`, `test_spent_headroom_leaves_batch_room_for_other_providers`, `test_running_cards_and_this_pass_share_the_limit`, `test_pickup_copies_only_what_the_provider_can_start`, `test_alternate_target_spends_headroom_and_claim_remains_unreserved`, `test_class_reserves_each_limited_candidate_without_forcing_an_alternate`
- `tests/hermes_cli/test_pickup_incident_append.py`: `test_count_fluctuations_only_report_cap_crossings`

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- Headroom tests must opt into start pacing or they pass vacuously.
- Normalization is for incident dedupe only; do not apply it to raw observations.

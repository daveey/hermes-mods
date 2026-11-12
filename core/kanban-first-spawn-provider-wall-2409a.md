# Fresh-card routing around known provider walls

Mirror entries: [2327](patches/2327-daveey-fix-kanban-route-a-fresh-card-off-a-known-provider-wall-via-its-routing-class-before-the-firs.patch).

## Behavior

- Before a fresh unpinned card's first spawn, try an eligible alternate through the assignee's routing class when its base provider already has an active known wall.
- Refuse an alternate that is itself live-walled; preserve required-model behavior and the existing fallback when no class alternate is eligible.

## How it works

kanban_model_tier calls _class_alternate_off_wall before retier_for_walls. The helper checks the require-model marker, a concrete base model/provider, persisted or live quota walls and the existing quota-episode alternate_route eligibility policy. It returns the alternate plus the original walled provider without waiting for this card to fail once and create its own episode.

## Code

- `hermes_cli/kanban_model_tier.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/hermes_cli/test_kanban_quota_episode_walls.py` — `test_fresh_card_skips_a_known_wall_before_its_first_spawn`, `test_fresh_card_class_alternate_never_lands_on_another_wall`.

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

This avoids a known first-spawn wall; it does not predict unknown quotas or rewrite a mandatory model pin. The source tests cover file/live-pool evidence and rejecting another wall. No real provider call or quota reset was observed here.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.

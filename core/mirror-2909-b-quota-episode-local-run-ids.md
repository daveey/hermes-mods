# Quota episodes: map stored run ids to this replica

Patches: 2806, 2838.

- **2806** (`01a16e978c`, fix(kanban): quota-release maps the released run to this replica's run id)
- **2838** (`69382f9227`, fix(kanban): quota _outstanding/_unhinted read stored episodes through local run ids)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- A stored `quota_episode` payload keeps the recording gateway's run ids while `task_runs` and event rows use the replica's. 2806: `released_run` and `covers_latest` resolved the id through the stored episode event's own `run_id`, so a release on the hub actually ends holds on hub and worker replicas.
- 2838: `_outstanding` now starts at this replica's first run of the episode (`_episode_runs`, factored from `episode()`), so released earlier episodes are not re-counted; `_unhinted` maps the run through `_local_run` and otherwise anchors at the wall's recording time so a disproved hint without `backoff_until` expires.

## How it works

- `hermes_cli/kanban_quota_episode_release.py` (`_local_run`, `released_run`, `covers_latest`) and `hermes_cli/kanban_quota_episode.py` (`_episode_runs`, `_outstanding`, `_unhinted`). 2838 reuses the 2806 mapping, no second one.

## Code

- `hermes_cli/kanban_quota_episode_release.py`
- `hermes_cli/kanban_quota_episode.py`

## Tests

- `tests/hermes_cli/test_kanban_quota_episode_release_foreign_ids.py` (`test_release_of_an_episode_recorded_on_another_gateway_ends_the_hold`, `test_a_computed_episode_keeps_its_own_run_id`); `tests/hermes_cli/test_kanban_quota_episode_foreign_runs.py` (`test_outstanding_ignores_a_released_episode_recorded_under_foreign_run_ids`, `test_unhinted_backoff_expires_across_repeated_evaluations`).

Test files touched by these patches:

- `tests/hermes_cli/test_kanban_quota_episode_release_foreign_ids.py`
- `tests/hermes_cli/test_kanban_quota_episode_foreign_runs.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- Depends on the quota-episode and release code from earlier patches (see the quota auth-churn doc).
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.

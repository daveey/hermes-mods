# Kanban CI/deploy wait episode age (mirror 5476–5478)

## Behavior
- Scheduled card details expose the start (`since`) of the current continuous CI/deploy wait episode, derived from `task_events` (newest 512 semantic park/exit events). Display-only; no schema writes. Re-parks (scheduled/continued/blocked) by the same producer identity continue the same episode; a different producer or an exit event (completed, archived, reopened, status, etc.) ends it. A missing table/history, bad timestamp or truncated history gives an unknown (`None`) start; millisecond stamps are normalized.
- A changed commit head starts a new wait episode.
- A boundary-lint baseline entry records the intentional dashboard dependency on the new module.

## How it works
`hermes_cli/kanban_wait_episode.py` computes the episode; `plugins/kanban/dashboard/plugin_api.py` attaches it to task detail as `wait_episode` (only for `scheduled` cards; otherwise null).

## Code
`hermes_cli/kanban_wait_episode.py`, `plugins/kanban/dashboard/plugin_api.py`, `scripts/boundary_lint_baseline.json`.

## Tests
Source: `tests/hermes_cli/test_kanban_wait_episode.py`, `test_kanban_wait_episode_heads.py`. One test (`test_dashboard_task_detail_exposes_real_scheduled_episode`) covers the dashboard task-detail attachment; no rendering test is in these patches. Not executed in this mirror pass.

## Applying
Apply in order.

## Gotchas
Depends on lifecycle history retention; truncated history yields an unknown start.

## plugin?
Touches the bundled kanban dashboard plugin API; not a standalone plugin.

# fix(kanban placement): heavy_route works with the real Board registry (no pickup field) [daveey]

Patch 0832, source `ed53a153fdd60d3b0ca8005a4d295aa696b59426`.

## Behavior

fix(kanban placement): heavy_route works with the real Board registry (no pickup field) [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _heavy_marker(title: str, body: str, cfg: _Cfg) -> Optional[str]:`
- `def test_configured_heavy_markers_replace_the_builtin_list():`
- `def test_tool_create_sends_heavy_work_to_the_worker_by_runs_on(gws, monkeypatch)`
- `def _place_remote(args: dict, *, assignee: str, skills, parents, project_id, ses`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> GET /v2/machines publishes labels/state/hold but never pickup, so advertises_pickup() was False for every
> machine (hub and worker-1 included, though both claim runs_on cards) and 1ba4ced7e2's heavy route never
> fired on the MacBook. The heavy-route check now needs an active, unheld machine and treats a missing
> pickup field as on (heavy_route naming the worker is the operator saying so); an explicit pickup off
> still refuses.
>
> Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Code

- `hermes_cli/kanban_placement.py`
- `tools/kanban_tools.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_placement.py`
- `tests/hermes_cli/test_kanban_worker_node.py`

New or changed test definitions present in the source diff:

- `test_heavy_route_machine_check_reads_the_board_registry`
- `test_tool_create_heavy_route_works_when_the_registry_has_no_pickup_field`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

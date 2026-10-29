# feat(kanban placement): heavy work (tests/builds/benches) runs on a worker node via runs_on [daveey]

Patch 0830, source `1ba4ced7e2b1d06426d3d3829441f0ce17c277c0`.

## Behavior

feat(kanban placement): heavy work (tests/builds/benches) runs on a worker node via runs_on [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `A card stays local when any of these holds:`
- `DEFAULT_LOCAL_SKILL_MARKERS = (`
- `class _Cfg:`
- `def _local_marker(title: str, body: str, skills, cfg: _Cfg) -> Optional[str]:`
- `def _raw_explicit_pin(args: dict) -> str:`
- `def decide(args: dict, *, origin_intent=None, now: Optional[float] = None, cfg:`
- `def _decide(args: dict, *, origin_intent=None, now: Optional[float] = None, cfg:`
- `def test_hermes_hub_pin_on_the_hub_itself_creates_locally(mac, monkeypatch):`
- `def test_hub_and_worker_never_both_run_one_card(gws, svc):`
- `def _persisted_session_id(session_id: Optional[str]) -> Optional[str]:`
- `def _place_remote(args: dict, *, assignee: str, skills, parents, project_id, ses`
- `def _handle_create(args: dict, **kw) -> str:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> kanban.placement.heavy_route ({macbook: worker-1}) sends a card with no local-only need whose title or
> body names tests, builds, benchmarks or installs (DEFAULT_HEAVY_MARKERS, replaceable via heavy_markers)
> to a worker: created here with runs_on=[machine:<worker>] and claimed through the Board lease, so it
> keeps its parents and its chat. Cards naming the routed target's host keep the route, and any card does
> when the worker is not an active pickup machine. MacBook load hit 100-200 on 10-28 from agent test runs.
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

- `test_heavy_work_runs_on_the_worker_through_the_board_lease`
- `test_heavy_work_with_parents_still_runs_on_the_worker`
- `test_light_work_keeps_the_gateway_route`
- `test_mac_only_heavy_work_stays_local_without_runs_on`
- `test_heavy_work_that_names_the_target_host_keeps_the_route`
- `test_heavy_work_falls_back_to_the_route_when_the_worker_does_not_pick_up`
- `test_heavy_route_is_off_unless_configured_and_an_explicit_pin_wins`
- `test_configured_heavy_markers_replace_the_builtin_list`
- `test_tool_create_sends_heavy_work_to_the_worker_by_runs_on`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

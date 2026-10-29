# fix(kanban): placement pin becomes runs_on only for a machine that advertises pickup [daveey]

Patch 0744, source `af0521714fc8e8129cf89b3b76955cee437d8823`.

## Behavior

fix(kanban): placement pin becomes runs_on only for a machine that advertises pickup [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `Placement rules mirrored from the Board (runs_on a JSON list of labels):`
- `def set_runs_on(conn: sqlite3.Connection, task_id: str, runs_on: Optional[list[s`
- `HUB, WORKER, BOARD = "hub", "worker-1", "default"`
- `def test_tool_create_runs_on_and_placement_to_an_enrolled_machine(gws, monkeypat`
- `def _place_remote(args: dict, *, assignee: str, skills, parents, project_id, ses`
- `def _handle_create(args: dict, **kw) -> str:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> kanban_create(placement='hub') on the MacBook (route {macbook: hub}) turned the
> pin into runs_on=[machine:hub] because hub is an active registry machine, but
> hub runs with kanban.sync.pickup off, so the card sat ready on the MacBook board
> forever (4 stranded on 10-28). placement_runs_on now requires the registry entry
> to advertise pickup (missing field = no pickup); otherwise the pin falls
> through to today's placement, so a routed gateway pin goes through
> _place_remote again. Every kanban_create result now carries runs_where (plus
> placed / runs_on as before) so the caller can see where the card runs.
>
> The Board's /v2/machines projection is fixed (labels, pinned_only,
> max_card_runs, hold) and gateways cannot write it, so no machine advertises
> pickup until hermes-hub carries the field.

## Code

- `hermes_cli/kanban_worker_node.py`
- `tools/kanban_tools.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_worker_node.py`

New or changed test definitions present in the source diff:

- `test_a_routed_gateway_pin_to_a_machine_without_pickup_takes_the_gateway_route`
- `test_a_routed_gateway_pin_to_a_machine_with_pickup_still_gets_runs_on`
- `test_placement_runs_on_needs_advertised_pickup`
- `test_a_plain_local_create_says_it_runs_here`
- `test_tool_placement_to_an_unknown_gateway_still_refuses`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

# fix(kanban): refuse an explicit runs_on machine pin to a machine that does not pick up the board [daveey]

Patch 0938, source `cc3d681cb9ddf0993ec0f31a13b99702cb7a08cb`.

## Behavior

fix(kanban): refuse an explicit runs_on machine pin to a machine that does not pick up the board [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _cmd_create(args: argparse.Namespace) -> int:`
- `Guards:`
- `def check(conn: sqlite3.Connection, task_id: str, runs_on: Any, *, force: bool =`
- `def advertises_pickup(entry: Optional[Mapping[str, Any]]) -> bool:`
- `def test_max_moves_and_ping_pong(gws):`
- `+"""An explicit runs_on=[machine:<x>] is refused unless x advertises pickup in the Board registry. [daveey]`
- `def _handle_create(args: dict, **kw) -> str:`
- `KANBAN_MOVE_SCHEMA = _schema(`
- `KANBAN_CREATE_SCHEMA = _schema(`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Three hub cards created with runs_on=[machine:macbook] sat ready until
> archived: only the MacBook may lease them and it never copies hub cards
> (GET /v2/machines pickup: null). Create (kanban_create tool, hermes kanban
> create) and move (kanban_move, hermes kanban move) now refuse
> machine:<x> unless x is an active registry machine advertising pickup
> (null/missing = none; registry unreachable fails closed), with
> placement_unavailable naming placement="<x>" as the working route.
> Placement-derived runs_on (placement=<machine>, heavy_route) is unchanged.
> kanban edit and the dashboard cannot write runs_on.

## Code

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_move.py`
- `hermes_cli/kanban_worker_node.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_schemas.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_move.py`
- `tests/hermes_cli/test_kanban_runs_on_pickup_guard.py`

New or changed test definitions present in the source diff:

- `test_tool_create_pinned_to_a_machine_without_pickup_is_refused`
- `test_tool_create_pinned_to_an_unknown_machine_is_refused`
- `test_a_missing_pickup_field_counts_as_no_pickup`
- `test_an_unreachable_registry_refuses_a_machine_pin`
- `test_tool_create_pinned_to_a_machine_that_advertises_pickup_is_accepted`
- `test_own_machine_and_plain_labels_are_not_checked`
- `test_cli_create_gets_the_same_two_results`
- `test_move_queued_to_a_machine_without_pickup_is_refused_and_accepted_with_it`
- `test_cli_move_gets_the_same_two_results`
- `test_tool_move_of_a_running_card_to_a_machine_without_pickup_is_refused`
- `test_placement_pin_still_becomes_runs_on_only_with_pickup`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

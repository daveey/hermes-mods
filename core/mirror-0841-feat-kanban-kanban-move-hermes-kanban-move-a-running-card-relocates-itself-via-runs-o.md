# feat(kanban): kanban_move / hermes kanban move: a running card relocates itself via runs_on [daveey]

Patch 0841, source `dc19ad6ccc9a16da6125fa796673b5140e94d37e`.

## Behavior

feat(kanban): kanban_move / hermes kanban move: a running card relocates itself via runs_on [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `_TERMINAL_KANBAN_TOOLS = frozenset({`
- `EXPOSED_TOOLS: tuple[str, ...] = (`
- `def _profile_author() -> str:`
- `def _cmd_reclaim(args: argparse.Namespace) -> int:`
- `_HANDLERS = {`
- `from __future__ import annotations`
- `def continue_task(`
- `+"""A running card moves itself to another machine: kanban_move / hermes kanban move. [daveey]`
- `_SPECS = [`
- `FENCED = {"status", "current_run_uid", "result", "started_at", "completed_at"}`
- `class FakeService:`
- `class FakeHub:`
- `+"""kanban_move: a running card moves itself to machines with other runs_on labels (hermes_cli.kanban_move). [daveey]`
- `from tools.kanban_tools_placement import _placement_refused`
- `def _check_kanban_orchestrator_mode() -> bool:`
- `def _handle_continue(args: dict, **kw) -> str:`
- `_TOOLS = (`
- `KANBAN_SCHEDULE_SCHEMA = _schema(`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> One move = checkpoint + end this run as continued + new runs_on + a moved event, in one write txn
> (continue_task gains an in_txn hook). The Board ends the old lease when the card leaves running and its
> grant checks runs_on for every claimer, the creator included, so only a matching machine runs it next.
> Guards: target labels must match an active, unheld registry machine other than this one; kanban.move.max_moves
> (default 3); moving back to a machine the card left needs force; dir/worktree cards never move; synced boards
> only. Entry paths: agent tool kanban_move (worker run, terminal tool), CLI hermes kanban move (own run with
> --checkpoint, queued card, or --reclaim for a card running here). Fake Board: opt-in runs_on grant check and
> lease end on leaving running, as worker/src rows.ts + checkPlacement.
>
> Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Code

- `agent/kanban_stop.py`
- `agent/transports/hermes_tools_mcp_server.py`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_continuation.py`
- `hermes_cli/kanban_move.py`
- `hermes_cli/kanban_parser.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_schemas.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/kanban_sync_fake_board.py`
- `tests/hermes_cli/test_kanban_move.py`

New or changed test definitions present in the source diff:

- `test_a_hub_card_moves_itself_to_the_worker_and_completes_there_once`
- `test_move_refuses_unknown_held_and_self_only_targets`
- `test_move_needs_a_reason_and_a_running_card`
- `test_workspace_bound_cards_never_move`
- `test_max_moves_and_ping_pong`
- `test_labels_matching_a_machine_it_never_left_are_not_ping_pong`
- `test_tool_kanban_move_moves_the_workers_own_card`
- `test_tool_kanban_move_requires_its_arguments`
- `test_kanban_move_is_a_registered_terminal_tool`
- `test_cli_move_relabels_a_queued_card`
- `test_cli_move_of_a_running_card_needs_reclaim`
- `test_cli_move_by_the_cards_own_worker_ends_its_run`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

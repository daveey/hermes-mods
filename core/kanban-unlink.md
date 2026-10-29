# kanban_unlink, and a worker may close the card it parked

Patch **0694** (`14e8670d85`).

## Behavior

- `kanban_unlink` removes one parent→child edge. A dispatcher worker may unlink only an edge of its own card (it is the parent or the child). An orchestrator (no `HERMES_KANBAN_TASK`, or a caller that is not a dispatcher worker) may unlink any edge. A missing edge is an error. A child that no longer waits on anything is promoted at once (that promotion is `unlink_tasks`, not new logic in the tool).
- A worker that parked its own card with `kanban_block` and then unlinked an edge may still `kanban_complete` that card, as long as nothing has claimed it since. The run is the card's latest run, its outcome is `blocked`, status is `ready` or `blocked`, and there is no current run and no claim lock.
- A later run cannot complete the card. Once the card is reclaimed, the old run's complete is refused.
- The leaked-worker-env detector treats a parked card (`ready`/`blocked`, not `done`/`archived`) whose latest run still has a live `worker_pid` as that worker, not a leak. A gone pid, a live pid that is not the worker's, or no run pid is still a leak.

## How it works

`_handle_unlink` rejects a dispatcher worker whose `HERMES_KANBAN_TASK` is neither endpoint, then calls `kb.unlink_tasks`. `_handle_complete` passes `expected_run_id=None` when `_parked_by_own_run` matches, so the run-id fence does not treat the parked run as stale. `leaked_worker_env_diagnosis` reads `task_runs.worker_pid` when the card row's pid was cleared by the park and the status is not terminal.

The tool is on the kanban toolset, the MCP exposed-tool list, and the tools reference.

## Code

`tools/kanban_tools.py` (`_handle_unlink`, `_parked_by_own_run`), `tools/kanban_tools_schemas.py` (`KANBAN_UNLINK_SCHEMA`), `toolsets.py`, `agent/transports/hermes_tools_mcp_server.py`, `hermes_cli/top_level_env.py`, `website/docs/reference/tools-reference.md`.

## Tests

`tests/tools/test_kanban_tools.py`:

- `test_worker_blocks_on_dependency_then_unlinks_and_completes`
- `test_unlink_rejects_foreign_edge_and_missing_link`
- `test_unlink_orchestrator_may_unlink_any_edge`
- `test_unlink_tool_registered_for_workers`
- `test_old_run_cannot_complete_once_the_card_was_reclaimed`

`tests/hermes_cli/test_leaked_worker_env.py`:

- `test_no_diagnosis_for_a_live_worker_whose_card_was_parked` (parametrized on status)
- `test_parked_card_whose_worker_pid_is_gone_is_still_a_leak`
- `test_parked_card_with_a_foreign_live_pid_is_still_a_leak`
- `test_parked_card_without_any_run_pid_is_still_a_leak`

This mirror did not re-run those files. The CLI `hermes kanban unlink` entry point is untested (the tool handler is what these tests call). MCP registration is untested beyond the exposed-tool tuple edit.

## Applying

`git am -3` 0694. Needs `unlink_tasks` already on the board DB (earlier kanban-link patches).

## Gotchas

- A worker cannot unlink an edge it does not sit on, even if it created the edge from another card. Link the other way or have an orchestrator do it.
- Parking clears the card's `worker_pid`. The leak detector has to look at the latest run. A detector that only reads `tasks.worker_pid` will false-positive on a parked live worker.

## plugin?

No. The tool is core kanban. A plugin can call it; it cannot add the ownership rule.

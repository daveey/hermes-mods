# Owner-wake unblock grants resolve exact cards across local boards

Patches:
- `patches/1547-fix-kanban-resolve-exact-owner-wake-grants-across-lo.patch` (`29e429af347`, `fix(kanban): resolve exact owner-wake grants across local boards`)
- `patches/1548-test-kanban-exercise-fresh-owner-wake-discovery-and-.patch` (`a7f0ae1033d`, `test(kanban): exercise fresh owner-wake discovery and revoked board grants`)

Builds on the owner-wake requeue grant in [kanban-worker-requeue-and-crash-records.md](kanban-worker-requeue-and-crash-records.md) (a `created_by='owner-wake'` card titled `Owner wake: supervise ...` may `kanban_unblock` the `- <board>/<id>` lines before its body's first blank line). The supervisor cards themselves come from the chat-closeout owner wake ([kanban-owner-wake-visible-waits.md](kanban-owner-wake-visible-waits.md)).

## Behavior

Before, the grant only counted card lines naming the supervisor's own board, so a supervisor filed on `default` that listed `ops/<id>` had no grant and could neither see nor unblock that card. Now:

- **Cross-board grants.** A dispatcher-owned owner-wake supervisor can `kanban_show` and `kanban_unblock` a listed card on any board that exists locally. The target board is opened by its own `kanban.db`; the worker's board pin is never changed, and ordinary workers and delegated children keep the old resolution (no grant, no cross-board view).
- **Exact match only.** The `(board, id)` pair must be listed. An unlisted card on the listed board, or the listed id under a different `board` argument, is refused.
- **Omitted `board`.** Inferred only when exactly one listed line has that id; two lines with the same id on different boards return `ambiguous owner-wake target; specify board`.
- **Run fence.** The supervisor must be `running` and its `current_run_id` must equal the worker's run id; a stale run is refused (`owner-wake supervisor no longer owns its current run`).
- **Revocation at the write.** The grant is re-checked inside the target's unblock transaction (the `precondition` of `kb.unblock_task`): the target must still be `triage`/`blocked`, and the supervisor's body must still list exactly that `(board, id)` under the current run. A body edit, board rewrite, cleared run or target status change between the check and the write refuses the unblock.
- A listed board with no local `kanban.db` returns `orchestrator-only: listed owner-wake board is not available locally`.
- `has_requeue_grant` (tool availability) is true for any listed line, on any board, so a fresh worker's tool registry exposes `kanban_unblock`.
- The `owner_wake_requeue` audit event adds `supervisor_board`.

## How it works

- `hermes_cli/kanban_requeue.py`: `owner_wake_listed_refs(task)` returns ordered, de-duplicated `(board, id)` pairs from the card-line block; `owner_wake_listed_cards(task, board)` filters it.
- `tools/kanban_tools_self_heal.py`: new `granted_board(board, tid)` context manager reads the supervisor on the pinned board, matches refs, checks the run, then yields the pinned connection (same board) or a connection to `kb.board_own_db_path(<listed board>)`, plus the `(owner, target_board)` grant. Otherwise it falls back to `kt._board(board)`. `handle_unblock` uses it and passes the revocation `precondition`.
- `tools/kanban_tools.py`: `_handle_show` resolves through `_granted_board`.
- `hermes_cli/kanban_db.py`: docstring only (`board_own_db_path` now also serves this resolver).

## Code

`hermes_cli/kanban_requeue.py`, `tools/kanban_tools_self_heal.py`, `tools/kanban_tools.py`, `hermes_cli/kanban_db.py`.

## Tests

From the patches; not run for this mirror. `tests/tools/test_kanban_owner_wake_cross_board.py` (new):

- `test_cross_board_surface_and_unblock`: owner-wake vs `default` creator × `triage`/`blocked`/`scheduled`/`running` target; tool availability, show, unblock, unlisted and wrong-board refusals.
- `test_cross_board_delegate_cannot_use_supervisor_grant`: a delegated child gets no tool, show or unblock.
- `test_omitted_board_and_stale_run`: inferred board, and refusal under a stale run id.
- `test_ambiguous_id_requires_board`.
- `test_fresh_worker_registry_describe_and_dispatch` (1548): a subprocess builds the tool registry with `kanban_unblock` deferred, then `tool_describe` / `resolve_underlying_call` / `registry.dispatch` for show and unblock.
- `test_revocation_at_transition_boundary` (1548): board, body, run and target-status changes before the target write all refuse.

`tests/tools/test_kanban_owner_wake_requeue.py`: the audit payload now includes `supervisor_board`. Remote (non-local) boards are refused, not exercised through a sync replica; live dispatcher runs are untested.

## Applying

Depends on the preceding series (the requeue grant and the owner-wake plugin core). Apply the full ordered series per [README.md](README.md) "Applying"; standalone application is not certified.

## Gotchas

- Only boards with a local `kanban.db` qualify; a card on another gateway's board still needs an orchestrator.
- Only the line block before the first blank line grants; ids cited in later prose never do.
- The shell (`hermes kanban unblock`) fence is unchanged; the grant exists only in the in-process tool.

## Plugin?

No. The grant check and board resolution are in the core Kanban tool handlers; the chat-closeout plugin only writes the supervisor card.

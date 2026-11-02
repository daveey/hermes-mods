# Worker-terminal write refusal names the worker fence

Patches: 0963 1593 1598

- `patches/0963-fix-kanban-worker-terminal-write-refusal-names-the-w.patch`
- `patches/1593-daveey-kanban-fence-a-finished-card-s-live-worker-sh.patch`
- `patches/1598-fix-kanban-scope-the-worker-subprocess-tool-fence-to.patch`

Related: [top-level-env-scrub.md](top-level-env-scrub.md) (owns `hermes_cli/top_level_env.py` and the leaked-worker-env
diagnosis that 1593 refines).

A worker ran a repair script from its own terminal after two delegate_task reviews and read "delegate_task child contexts cannot mutate Kanban", then filed a lineage-leak investigation. There was no leak: `scrub_kanban_env` gives every worker tool subprocess the `HERMES_DELEGATED_CHILD_CONTEXT` marker with or without delegation.

## Behavior

**0963 (refusal names the worker fence)**

The env-inherited refusal now names the worker-terminal fence, the owning agent pid and card, and the authorized paths (kanban_* tools, or gateway/dispatcher board-repair code). In-process delegate children keep their text; leaked markers keep the leak diagnosis.

**1593 (finished card's live worker shell gets a kanban_comment hint)**

- Before: a worker that had already run `kanban_complete` and then ran a board-writing `hermes kanban ...` command
  from its own shell was told "leaked worker env ... quit and reopen Hermes Desktop (Cmd-Q)". No Desktop was
  involved; the env was the worker's own and its card was simply finished.
- Now, when the lineage card is `done`/`archived` and a live worker of that lineage is an ancestor of this process,
  `leaked_worker_env_diagnosis` returns "your Kanban card … is done: board writes from its worker shell are
  fenced. Use kanban_comment on the card you mean (or ask an operator chat), not the CLI." It names no leak and no
  Cmd-Q. A live worker here is the `worker_bound` pid, the delegation owner, or the latest run's worker pid.
- A finished card whose worker is dead, or alive but not an ancestor (a Desktop relaunched from inside a worker),
  still gets the leaked-env / Cmd-Q advice.
- CLI path: `hermes kanban archive` from such a shell still refuses ("cannot mutate Kanban tasks via the CLI"),
  and the refusal now carries the finished-card hint (tested end to end through `kanban_command`).

**1598 (tool fence scoped to fenced boards, like the DB fence)**

- Before: once `HERMES_DELEGATED_CHILD_CONTEXT` was set, every mutating `kanban_*` tool refused, whatever board it
  targeted. The DB fence (`kanban_path_is_fenced`) only refuses the pinned board and boards under the marker's root.
  A worker asked to show a live `kanban_link` / `kanban_block` refusal on a throwaway board had no route and handed
  a shell command back to the user.
- Now, for board-local writes, a subprocess fenced only by the env marker is refused only when the target board
  (`args["board"]`) or `HERMES_HOME` lies inside the fence. On a scratch `HERMES_HOME` outside the root, the tool runs
  its real validation (e.g. the "unknown task(s)" refusal for link and dependency block). Board-local writes are:
  the worker-guarded lifecycle tools (`_worker_guard`, e.g. `kanban_block`), `kanban_link`/`kanban_unlink`,
  `kanban_comment`, and `kanban_create` with `placement="local"` and no `origin_session`.
- Still refused everywhere: in-process delegate_task children, the legacy `"1"` marker, a missing marker, an
  unresolvable path (fails closed), `kanban_archive` (may relay to the card's home gateway), `kanban_create` with
  any other or omitted placement or an origin binding, and the other callers of `_reject_delegated_child_mutation`
  that pass no args (per the commit message: origin adoption, supervisor requests).
- Still refused under the root: a board whose `HERMES_KANBAN_HOME` is moved out while `HERMES_HOME` stays in the
  fenced home (so a scratch board never runs with the real home's routes and credentials), and the
  dispatcher-pinned `HERMES_KANBAN_DB` even when it sits outside the root.
- Both refusal texts, the DB fence's `descendant_refusal_text` and the tool fence's board-scoped refusal, now end
  with the scratch-home route: `H=$(mktemp -d ~/kanban-probe.XXXX)`, unset `HERMES_KANBAN_DB` `HERMES_KANBAN_BOARD`
  `HERMES_KANBAN_HOME`, run with `HERMES_HOME=$H`, then `rm -rf "$H"`. The hint says not to use TMPDIR, which
  sits under `~/.hermes`.

## How it works

The refusal text is built in `kanban_db_fence.py`, extracted from `kanban_db.py`.

**1593:** `_task_state` now falls back to the latest `task_runs.worker_pid` when the card's `worker_pid` is cleared
for any status, terminal ones included (before, only non-terminal parked cards; `complete_task` clears the card's
pid too). `leaked_worker_env_diagnosis` splits the terminal-status branch. If `_descends_from_live_agent(env,
bound_pid)` holds, or the worker pid is alive and `_is_ancestor`, it returns the finished-card hint. Otherwise it
keeps the "its worker card … is done" leak reason.

**1598:** `_reject_delegated_child_mutation(tool_name, args=None)` moves to the new `tools/kanban_tools_fence.py`
(re-exported from `kanban_tools`, which is over its line cap). With `args`, an env-marker-only process is refused
only if `_env_fence_covers_board(args.get("board"))` holds. That check is true for an in-process child, a `"1"` or
empty marker, `kanban_path_is_fenced(get_hermes_home())`, `kanban_path_is_fenced(kanban_db_path(board=board))`, or
any exception. Callers that pass no args are refused as before. `SCRATCH_HOME_HINT` is defined in
`hermes_cli/kanban_db_fence.py` and appended to the DB-fence text and to env-fenced board-scoped tool refusals.

## Code

- 0963: `hermes_cli/kanban_db_fence.py`, `hermes_cli/kanban_db.py`.
- 1593: `hermes_cli/top_level_env.py`.
- 1598: `hermes_cli/kanban_db_fence.py`, `tools/kanban_tools.py`, `tools/kanban_tools_fence.py` (new),
  `tools/kanban_tools_graph.py`.

## Tests

- 0963: `tests/tools/test_worker_terminal_fence_lineage.py` pins that a parent terminal env is identical to a worker's and checks each message.
- 1593: `tests/hermes_cli/test_leaked_worker_env.py`:
  - `test_diagnosis_names_a_finished_worker_card` (tightened: a finished card with no live ancestor still says Cmd-Q)
  - `test_finished_card_of_a_live_bound_ancestor_worker_gets_the_fenced_hint` (parametrized `done`, `archived`)
  - `test_finished_card_whose_run_pid_is_a_live_ancestor_gets_the_fenced_hint`
  - `test_finished_card_whose_delegation_owner_is_a_live_ancestor_gets_the_fenced_hint`
  - `test_finished_card_with_a_dead_worker_keeps_the_desktop_hint`
  - `test_finished_card_with_a_live_non_ancestor_worker_keeps_the_desktop_hint`
  - `test_finished_worker_shell_cli_refusal_points_at_kanban_comment` (real board, `kanban archive` through `kanban_command`)
- 1598: `tests/tools/test_kanban_tools_scratch_home_fence.py`:
  - `test_env_fenced_subprocess_reaches_real_logic_on_scratch_home`
  - `test_env_fenced_subprocess_still_refused_on_board_under_root`
  - `test_env_fenced_subprocess_refused_when_home_is_fenced_but_board_is_not`
  - `test_scratch_home_scope_covers_local_writes_only`
  - `test_env_fenced_subprocess_still_refused_on_pinned_db`
  - `test_legacy_marker_still_refuses_everywhere`
  - `test_in_process_delegate_child_still_refused_on_scratch_home`
  - `test_real_worker_subprocess_drives_tools_on_scratch_home` (real subprocess with `delegated_child_subprocess_env`)
  - `test_descendant_refusal_text_names_the_scratch_home_route`

These were not rerun by the mirror pass.

Untested:

- 1593: other board-writing CLI verbs (`kanban edit` from the original incident, etc.). Only `kanban archive` is
  exercised end to end. The kanban_* tool refusal path carrying the finished-card hint is also not exercised.
- 1598: `kanban_unlink` and worker-guarded lifecycle tools other than `kanban_block` on a scratch home; an explicit
  `board` argument naming an unfenced board (only the pinned-DB case passes `board`); the unresolvable-path
  fail-closed branch; origin adoption and supervisor requests staying refused; a `terminal` or `execute_code` tool
  call from a real worker (the subprocess test runs `python -c` with the worker env, not through those tools).

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required. 1593 builds on the
leaked-worker-env diagnosis in `hermes_cli/top_level_env.py` (see Related); 1598 builds on 0963's `kanban_db_fence.py`.

## Gotchas

- 1598 opens only board-local writes on a scratch home. Anything that may write another gateway (archive relay,
  non-local create, origin binding) stays refused on every board, so a probe of those still needs a real worker
  tool call.
- The scratch home must sit outside the fenced root. Under `~/.hermes` (which includes TMPDIR there) it is still
  fenced. The hint uses `~/kanban-probe.XXXX` for this reason.
- 1593's hint applies only when a live worker of the lineage is an ancestor. A finished card whose worker exited
  still shows the leak / Cmd-Q text, even from a process that is not a Desktop.

## Plugin

No: the fence and its refusal texts are inside the core kanban tool and DB layers, which a plugin cannot change.

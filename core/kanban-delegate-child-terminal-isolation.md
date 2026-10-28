# Delegate children keep their own terminal env; `--board` works from worker shells

Patches:

- `patches/0692-fix-kanban-daveey-delegate-children-get-their-own-te.patch`: per-child terminal snapshot overlay, worker-shell `--board`, `boards list` own-DB counts,
  fenced `kanban list`, refusal text.

## Behavior

- **Child exports stay in the child.** `delegate_task` children run their terminal commands in the parent's
  environment object, whose bash snapshot is sourced before every command and re-dumped after it. A child's
  `export HERMES_KANBAN_DB=~/.hermes/kanban/boards/runners/kanban.db` used to land in that shared snapshot, so every
  later parent command read the runners board. Now each child reads and writes its own overlay snapshot. The
  overlay starts from the enclosing child's state, else the parent's. The child keeps its exports across its own
  commands; the parent and sibling children never see them.
- **The parent snapshot never stores `HERMES_DELEGATED_CHILD_*`.** `HERMES_DELEGATED_CHILD_OWNER` joins
  `HERMES_DELEGATED_CHILD_CONTEXT` in the snapshot exclusion list.
- **What is by design, not a leak.** A Kanban worker's own terminal commands carry `HERMES_DELEGATED_CHILD_CONTEXT`
  and `..._OWNER` on every command. Upstream's `delegated_child_subprocess_env` (b578261584) stamps them into each
  subprocess env, so a worker shell's `hermes kanban <write>` is refused and the worker uses the `kanban_*` tools.
  An `unset` therefore "comes back" on the next call: the value comes from the per-command process env, not the
  snapshot. The refusal now says so and names the tools (kanban_comment, kanban_block, kanban_link,
  kanban_unlink, kanban_create).
- **`hermes kanban --board B` from a worker shell reads board B.** Before, the fence made every explicit board
  resolve through the inherited `HERMES_KANBAN_DB` pin: `--board runners show <id>` printed "no such task". Now a
  spawned descendant (env marker only, no `HERMES_KANBAN_TASK`, not an in-process child) follows its explicit
  board when that board's own DB is inside the fenced root. Writes there are still refused by the fence. A board
  outside the root, a dispatched worker's tools, in-process children and machine flows
  (`pin_first_board_resolution`) stay pinned.
- **`boards list` counts each board's own DB**, not the env-pinned one (it showed identical counts on every row).
- **`kanban list` works in a fenced shell.** Its best-effort `recompute_ready` is a write; it is skipped there
  instead of failing the listing.
- **Accepted limit: `env -u` bypass.** The fence is cooperative. Code running as the same OS user can drop the
  marker (`env -u HERMES_DELEGATED_CHILD_CONTEXT`) or write the SQLite file directly, because it has write
  access to the board. No layer inside Hermes can prevent that; only OS confinement can (another uid, or a
  sandbox without the board mounted). The fence stops accidental or habitual CLI writes and steers agents to
  the `kanban_*` tools, whose ownership checks are the real contract. This is documented in
  `delegated_child_subprocess_env`.

## How it works

- `agent/delegation_context.py`: `delegated_child_context()` pushes a random id onto a `_DELEGATED_CHILD_SCOPE`
  ContextVar; `delegated_child_scope_chain()` returns it. `explicit_board_resolves_inside_fence(path)` is the
  descendant predicate.
- `tools/environments/base.py` `_wrap_command`: inside a child, the snapshot written is
  `hermes-snap-<session>.child-<id>.sh`; reads fall back through enclosing children to the parent snapshot.
  `base_session_env._source_snapshot_line` emits the `if [ -f … ]; elif …` chain. `LocalEnvironment.cleanup` removes
  the overlays.
- `hermes_cli/kanban_db.py` `_board_path`: an explicit slug beats the pin when
  `_descendant_may_follow_explicit_board(slug)`; `board_own_db_path(slug)` is the pin-free path.
- `hermes_cli/kanban_boards.py` `_board_task_counts` opens `board_own_db_path(slug)`.

## Tests

- `tests/tools/test_delegate_child_terminal_env_isolation.py`: real `LocalEnvironment`. A child's exports
  (including `HERMES_KANBAN_DB/BOARD`) persist for the child but not the parent or a sibling. Nested children
  read through. The parent's terminal and snapshot hold no `HERMES_DELEGATED_CHILD_*`, even after a child exports
  them. Cleanup removes overlays.
- `tests/hermes_cli/test_kanban_worker_shell_board_flag.py`: through the real CLI parser, with a worker shell's env
  (pin + marker): `--board runners show/list/stats` read runners; `--board runners comment` is refused and writes
  nothing; dispatched worker, in-process child and machine flow stay pinned; `boards list --json` counts each
  board (with and without `HERMES_KANBAN_TASK`).
- `tests/hermes_cli/test_kanban_board_path_priority.py::test_delegated_child_board_arg_stays_pinned`: updated for
  the new rule. An in-root explicit board resolves to its own (fenced) DB; a board outside the root stays pinned.
- Untested: remote backends (SSH/Docker/Modal) write overlays through the same wrapper script, but no test runs one.

## Applying

`git am -3` in series order. Touches `agent/delegation_context.py`, `tools/environments/base*.py`,
`tools/environments/local.py`, `hermes_cli/kanban{,_db,_boards}.py`.

## Gotchas

- Overlays live next to the session snapshot until the environment is cleaned up (a few KB per child).
- Child `cd` was already per task id (`get_session_cwd`); this change is about exported variables only.

## Plugin?

No. The snapshot wrapper and board resolver are core internals with no hook.

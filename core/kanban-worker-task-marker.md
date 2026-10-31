# Kanban worker tool children carry `HERMES_KANBAN_WORKER_TASK`

Patches: 0775 1365

- `patches/1365-fix-kanban-worker-protocol-prompt-names-HERMES_KANBA.patch`: the worker protocol prompt names the marker for shell use.

- `patches/0775-fix-kanban-worker-tool-children-carry-HERMES_KANBAN_.patch`: informational card marker exported
  to every tool child of a Kanban worker; docs row in `environment-variables.md`.

## Behavior

- **A worker's tool children know their card.** Every process a dispatcher-spawned Kanban worker's tools start
  gets `HERMES_KANBAN_WORKER_TASK=<card id>`: terminal foreground commands, background processes (the process
  registry), and the `execute_code` sandbox. Shell tooling reads it to tell it runs for a card: gh-shim call
  attribution, `bb land`'s "card <id>" note, and the swift/xcodebuild compile cap (`_build_cap.sh`), all in the
  private state repo.
- **`HERMES_KANBAN_TASK` stays scrubbed.** Upstream's `delegated_child_subprocess_env` (b578261584) removes it on
  purpose: a nested `hermes` started from a worker terminal must not act as the worker (lifecycle tools,
  `kanban_complete` defaulting to the card, board-pin rules all read it). The new name is informational only. A
  process carrying only the marker gets no worker-lifecycle tools, owns no task, and is not board-pinned.
- **Descendants keep it.** A shell started by a worker terminal (scrubbed again, `HERMES_KANBAN_TASK` already
  gone) still carries the inherited marker.
- **No card, no marker.** A non-worker chat's tool children, and a `delegate_task` child of a non-worker chat,
  get neither variable.
- **Agent-scope launches strip it.** It shares the `HERMES_KANBAN_` prefix, so a Desktop, `serve` or gateway
  launched from a worker shell drops it with the rest of the agent-scope family (`strip_agent_scope_env`).

- **The protocol prompt names it (1365).** `KANBAN_GUIDANCE` said the task id is in `$HERMES_KANBAN_TASK`, but terminal and execute_code shells never have it, so `--card $HERMES_KANBAN_TASK` expanded to a bare `--card` and the worktree fallback branch became `wt/`. The prompt now says shells use `$HERMES_KANBAN_WORKER_TASK`, and the fallback is `wt/$HERMES_KANBAN_WORKER_TASK`.

## How it works

- `agent/delegation_context.py`: `KANBAN_WORKER_TASK_ENV`; `scrub_kanban_env` sets it from
  `_worker_task_for_children(env)`, which takes `HERMES_KANBAN_TASK` from the passed env, then from this process
  (execute_code passes an env already filtered of `HERMES_*`), then an inherited marker.
- Every spawn surface already routes through `delegated_child_subprocess_env` → `scrub_kanban_env`, so no call
  site changed.

## Tests

- `tests/tools/test_kanban_worker_task_marker.py`
  - worker context, one test per entry path (terminal foreground via `LocalEnvironment.execute`, background via
    `ProcessRegistry.spawn_local`, execute_code via `_build_child_env` + subprocess): the child sees the marker
    and neither `HERMES_KANBAN_TASK` nor `HERMES_KANBAN_RUN_ID`;
  - non-worker context, same three paths: neither variable;
  - delegate child of a non-worker chat: no marker;
  - grandchild of a worker terminal: marker kept;
  - nested `hermes` process with only the marker: `owned_kanban_task()` empty, not a worker for tool_search, not
    board-pinned, `kanban_complete/block/heartbeat/request_review` check_fns false (control: the same probe with
    `HERMES_KANBAN_TASK` sees all four).
  - RED: with the marker assignment disabled, the 3 worker entry-path tests and the grandchild test fail.

- 1365: `test_worker_protocol_prompt_names_the_variable_its_shells_have` asserts `KANBAN_GUIDANCE` contains `$HERMES_KANBAN_WORKER_TASK` and neither `wt/$HERMES_KANBAN_TASK` nor ``$HERMES_KANBAN_TASK`;``. Not run for this mirror. Code: `agent/prompt_builder.py`.

## Apply

`git am core/patches/0775-*.patch`. On a newer upstream, re-implement in `scrub_kanban_env` (or wherever
`HERMES_KANBAN_TASK` is removed from tool-child envs) and keep every reader of the new name out of identity code.

## Gotchas

- Never read `HERMES_KANBAN_WORKER_TASK` for worker identity, tool gating or board scope. Use
  `owned_kanban_task()` for that.
- Cores without this patch export nothing; `_build_cap.sh` keeps its dispatcher-argv ancestor walk for them.

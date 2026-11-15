# Tool observation barriers, PTY drain and scratch diagnostics

Patches: **4271, 4277, 4284, 4323** (4352 snapshot).

## Behavior

post_tool_call waits out an in-flight plugin registry reload rather than losing the observation while hooks are temporarily absent. The final PTY drain has an enforced budget.

Kanban GC prints kept-scratch reasons to stdout as well as its existing log, allowing wrappers to retain refusal and backoff explanations.

## How it works

Tool completion joins the registry reload barrier before hook emission, respecting its event-loop constraints. PTY drain tests use always-ready and controlled EOF/readiness fixtures to pin the final cap. Loop-accounting tests distinguish explicit error kinds from non-errors.

A temporary package logging handler relays only Scratch preserved records during the workspace GC pass. Refusal detail is constrained to a relay-safe alphabet, cut to 200 characters, and line breaks cannot forge additional stdout lines. The handler is removed even on failure.

## Code

- `hermes_cli/kanban_ops.py`
- `hermes_cli/kanban_scratch_backoff.py`
- `hermes_cli/kanban_worktree_gc.py`
- `model_tools.py`
- `tools/process_registry_pty_drain.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_gc_keep_lines_stdout.py`
- `tests/hermes_cli/test_loop_accounting.py`
- `tests/tools/test_process_registry_pty_drain.py`
- `tests/tools/test_registry_reload_barrier.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

The observation barrier is not a plugin install procedure, and event-loop threads must not block themselves. GC still preserves refused archives/detaches; printing a reason is not successful cleanup. Broad prior implementation evidence supplied for the overall batch was 15467 passed, 2 unrelated GC isolation failures and 24 skipped; this doc pass reran none of it.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.

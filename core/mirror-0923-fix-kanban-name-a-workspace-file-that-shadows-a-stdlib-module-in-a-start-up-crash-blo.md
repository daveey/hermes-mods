# fix(kanban): name a workspace file that shadows a stdlib module in a start-up crash; block it at once [daveey]

Patch 0923, source `b3f78f0a909f5085958fda93afcea0364f259990`.

## Behavior

fix(kanban): name a workspace file that shadows a stdlib module in a start-up crash; block it at once [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `from hermes_cli.quiet_single_query import KANBAN_WORKER_EXIT_TRAILER`
- `def _classify_worker_exit(pid: int) -> tuple[str, Optional[int]]:`
- `class _DeadWorker:`
- `def _classify_dead_worker(`
- `def _account_crashes(conn: sqlite3.Connection, crash_details: list) -> list[str]`
- `+"""Classify a dead Kanban worker's exit code. [daveey]`
- `+"""Name the workspace file that shadowed a Python stdlib module in a dead worker. [daveey]`
- `+"""A task workspace file named like a stdlib module must not crash-loop its card. [daveey]`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> What: when a worker exits non-zero before its first heartbeat, the dispatcher lists top-level
> <stdlib-name>.py files in the task workspace and appends "workspace file inspect.py shadows stdlib
> module inspect. Rename it ..." to the crash error (event payload: workspace_stdlib_shadows). When
> the worker log also carries a traceback frame from that file, the card is blocked at once as
> `assignment` instead of being retried. The regression test also runs the real worker argv with
> --version from a workspace holding a failing inspect.py (green with the -P argv from PR #153).
>
> Why: hub cards t_01a185a36329ca4e and t_01a18555704bff20 wrote a helper inspect.py; `python -m
> hermes_cli.main` put the cwd first on sys.path, dataclasses imported the helper, and each card
> crash-looped ("exited with code 1") until the breaker gave up, with supervisors blaming
> unrelated log lines. Self-heal defects kanban-3ba0fd6351 / kanban-50a03854e8.
>
> Red: on 6db9b6d0bb the 3 contract tests fail (no hint, retried instead of blocked, argv imports
> the workspace inspect.py). Green: 5 passed with this commit on top of #153.

## Code

- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_dispatch_exit_code.py`
- `hermes_cli/kanban_workspace_shadow.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_workspace_stdlib_shadow.py`

New or changed test definitions present in the source diff:

- `test_worker_argv_starts_from_workspace_holding_inspect_py`
- `test_stdlib_shadows_lists_only_top_level_stdlib_names`
- `test_traceback_from_workspace_inspect_py_blocks_at_once_and_names_it`
- `test_silent_shadow_exit_names_file_but_keeps_retry`
- `test_crash_after_heartbeat_or_without_shadow_is_unchanged`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

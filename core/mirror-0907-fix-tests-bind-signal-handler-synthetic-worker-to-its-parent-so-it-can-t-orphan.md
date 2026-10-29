# fix(tests): bind signal-handler synthetic worker to its parent so it can't orphan

Patch 0907, source `d1dfda1f43f8b151aaedc9187e3ec1e7fda1a482`.

## Behavior

fix(tests): bind signal-handler synthetic worker to its parent so it can't orphan. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _synthetic_worker_script() -> str:`
- `def _spawn_synthetic(env_overrides: dict) -> subprocess.Popen:`
- `def _cleanup(proc: subprocess.Popen) -> None:`
- `def test_sigterm_with_kanban_task_env_terminates_quickly():`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Adds stdin-EOF + ppid watchdog threads to the synthetic stuck-thread child and
> a regression test that SIGKILLs the spawning parent.

## Code

- `tests/hermes_cli/test_signal_handler_kanban_worker.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_signal_handler_kanban_worker.py`

New or changed test definitions present in the source diff:

- `test_synthetic_child_exits_when_parent_is_sigkilled`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

# fix(dashboard): --stop names a launcher child's KeepAlive job; old launcher's wrapper sh is no successor [daveey]

Patch 0757, source `42ea812e27cc380c1db74d39ecbf87b98eda5617`.

## Behavior

fix(dashboard): --stop names a launcher child's KeepAlive job; old launcher's wrapper sh is no successor [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _kill_stale_dashboard_processes(`
- `def _restart_killed_backends(`
- `def _launcher_supervisor(pid: int) -> tuple[int, tuple[str, ...]] | None:`
- `def _launcher_children(tail: tuple[str, ...]) -> list[int]:`
- `def _await_launcher_successors(waits: dict[tuple[str, ...], list[int]], stopped:`
- `respawn died with a SyntaxError, the runtime was booked failed and the recei`
- `def _run_launcher_child_update(monkeypatch, successor_after, *, fence_owner=None`
- `def test_launcher_children_reads_the_process_table(monkeypatch):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> hermes dashboard --stop of a hermes-gateway-launcher child printed the generic
> 'restart it when you're ready' hint, but the launcher exits with its child and
> the job's KeepAlive brings the backend straight back. Resolve the launcher's own
> launchd job (its XPC_SERVICE_NAME) and print the launchctl bootout hint.
>
> Update path: a surviving exec-less wrapper sh, still the original launcher's
> child just after the kill, counted as a fresh successor (false 'recovered').
> Only children of a launcher other than the original count now.

## Code

- `hermes_cli/dashboard_procs.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_update_dashboard_launchd_wrapper.py`

New or changed test definitions present in the source diff:

- `test_dashboard_stop_of_launcher_child_names_the_launchers_keepalive_job`
- `test_surviving_wrapper_shell_under_the_old_launcher_is_not_a_successor`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

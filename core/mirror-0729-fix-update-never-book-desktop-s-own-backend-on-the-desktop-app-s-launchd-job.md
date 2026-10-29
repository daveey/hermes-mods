# fix(update): never book Desktop's own backend on the Desktop app's launchd job [daveey]

Patch 0729, source `2b9ed17df6bd5386e715b39b23a294e915613ca1`.

## Behavior

fix(update): never book Desktop's own backend on the Desktop app's launchd job [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _is_ephemeral_port_zero_backend(argv: list[str]) -> bool:`
- `def _launchd_job_from_launch_env(`
- `def test_cmdline_capture_keeps_exact_argv_of_spaced_runpy_backend(monkeypatch):`
- `def test_terminal_launched_dashboard_still_respawns_from_argv(monkeypatch):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> The 04:36 PDT fenced update on David-MBP (2026-10-28) still owed a false gateway_restart after
> fc7b1ac399: the dashboard refresh stopped Desktop's local `serve --port 0` (pid 38035), and
> _launchd_job_from_launch_env attributed it to gui/501/application.com.nousresearch.hermes.* --
> the Desktop app's LaunchServices job, whose live pid (the app) is the backend's parent. The update
> kickstarted the app job, waited for launchd to report a fresh APP pid, booked the backend
> unrecovered and recorded the follow-up, while Desktop respawned its backend a second later.
>
> - `application.*` labels (LaunchServices app instances: Desktop, terminal apps) never claim a
>   backend; real KeepAlive backend jobs keep the kickstart path.
> - _is_ephemeral_port_zero_backend reads through the `Hermes Python.app -I -c <bootstrap>` form
>   Desktop spawns its backend with; without it the now-unattributed Desktop backend would have been
>   replayed as a detached `--port 0` orphan (verified on the live pid: raw False, resolved True).
>
> Tests: app-label attribution, the 38035 replay end to end, the bootstrap port-0 check and respawn
> filter fail without the fix; controls keep a never-returning real job unrecovered and a fixed-port
> backend replayed.

## Code

- `hermes_cli/dashboard_procs.py`
- `hermes_cli/main_dashboard.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_update_dashboard_launchd_wrapper.py`

New or changed test definitions present in the source diff:

- `test_desktop_local_serve_is_not_booked_on_the_desktop_app_job`
- `test_real_launchd_backend_job_that_never_returns_stays_unrecovered`
- `test_port_zero_check_reads_through_the_desktop_inline_bootstrap`
- `test_killed_desktop_bootstrap_backend_is_never_respawned`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

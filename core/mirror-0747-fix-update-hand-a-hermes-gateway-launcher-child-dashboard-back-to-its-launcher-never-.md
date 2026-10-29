# fix(update): hand a hermes-gateway-launcher child dashboard back to its launcher, never argv-respawn it [daveey]

Patch 0747, source `28936c0d972bd3462cdd93424fccc57812ea0d4c`.

## Behavior

fix(update): hand a hermes-gateway-launcher child dashboard back to its launcher, never argv-respawn it [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _kill_stale_dashboard_processes(`
- `def _restart_killed_backends(`
- `def test_killed_desktop_bootstrap_backend_is_never_respawned():`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> An unfenced hermes update stopped the launchd redde-serve dashboard (child of
> hermes-gateway-launcher; plist ProgramArguments = launcher, child XPC_SERVICE_NAME=0),
> found no launchd owner and replayed its argv detached on :9119 while the launcher's
> KeepAlive successor brought its own child up -> EADDRINUSE / duplicate backend.
>
> Classify a backend whose parent (through exec-less shells only) is a live
> hermes-gateway-launcher as launcher-supervised: stop it, never respawn from argv,
> count it recovered when a launcher running the same command has a fresh child
> within 60 s, and defer (not unrecovered) inside an ancestor-held core-update fence.

## Code

- `hermes_cli/dashboard_procs.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_update_dashboard_launchd_wrapper.py`

New or changed test definitions present in the source diff:

- `test_launcher_child_dashboard_is_not_respawned_and_recovers_on_successor`
- `test_launcher_child_that_never_returns_stays_unrecovered`
- `test_launcher_child_inside_ancestor_fence_is_deferred_not_waited`
- `test_launcher_tree_backend_behind_a_non_shell_parent_is_not_claimed`
- `test_launcher_children_reads_the_process_table`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

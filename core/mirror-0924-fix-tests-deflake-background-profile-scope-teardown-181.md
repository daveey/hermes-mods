# fix(tests): deflake background profile-scope teardown (#181)

Patch 0924, source `af02121e95c9fc3680005378c15978dddc05724c`.

## Behavior

fix(tests): deflake background profile-scope teardown (#181). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_lsp_client_spawn_hides_console_window(monkeypatch):`
- `def test_auto_title_thread_runs_in_the_turns_profile_scope(served_home, monkeypa`
- `def test_ws_orphan_reap_tears_down_under_the_sessions_profile(served_home, monke`
- `This is the shape that covers the workloads delegate_task can't:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> * fix(tests): tolerate throttled profile teardown and join reap timer [daveey]
>
> * fix(tests): keep plugin discovery and state.db open out of the timed ws-reap wait [daveey]
>
> Under SIGSTOP/SIGCONT starvation the reap wait failed even at 60s: the timed path
> ran ~3.8s CPU of environment-dependent work unrelated to the profile-scope contract
> (plugin discovery parsing every installed manifest twice; state.db writer opens that
> scan every host process's /proc fds). Stub the plugin dispatch leaf (recording the
> hook's scope), stub the reap keep-alive gates, and hold B's registry db generation
> so the teardown acquire is a cache hit. Timed work drops to ~70ms CPU; starved wait
> 1.5s (was 26s+/fail).
>
> * fix(tests): tolerate throttled profile teardown and join reap timer [daveey]
>
> * fix(tests): keep plugin discovery and state.db open out of the timed ws-reap wait [daveey]
>
> Under SIGSTOP/SIGCONT starvation the reap wait failed even at 60s: the timed path
> ran ~3.8s CPU of environment-dependent work unrelated to the profile-scope contract
> (plugin discovery parsing every installed manifest twice; state.db writer opens that
> scan every host process's /proc fds). Stub the plugin dispatch leaf (recording the
> hook's scope), stub the reap keep-alive gates, and hold B's registry db generation
> so the teardown acquire is a cache hit. Timed work drops to ~70ms CPU; starved wait
> 1.5s (was 26s+/fail).
>
> * fix(ci): synchronize overlap docs and LSP spawn mock contracts [daveey]

## Code

- `website/docs/user-guide/features/kanban.md`

## Tests

Source test files changed by this commit:

- `tests/test_windows_subprocess_no_window_flags.py`
- `tests/tui_gateway/test_background_thread_profile_scope.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

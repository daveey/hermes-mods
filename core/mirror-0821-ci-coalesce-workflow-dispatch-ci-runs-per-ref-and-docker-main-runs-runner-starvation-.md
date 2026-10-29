# ci: coalesce workflow_dispatch CI runs per ref and Docker main runs (runner starvation) (#158)

Patch 0821, source `76a514de64c488ca032329698ed2667e48a175a7`.

## Behavior

ci: coalesce workflow_dispatch CI runs per ref and Docker main runs (runner starvation) (#158). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `permissions:`
- `on:`
- `class _Parser:`
- `+"""Replay ci.yaml's concurrency group (and every called workflow's) per event.`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> * ci: main pushes cancel superseded main runs of hermes-hub workflows
>
> A per-run concurrency group on main meant cancel-in-progress never cancelled
> older main runs, so each push piled a full CI on the hermes-hub pool and
> starved short required PR jobs (PR #145 stall). Tags and release calls keep
> per-run groups.
>
> * ci(docker): coalesce standalone main Docker runs into one group (63-run docker-ci backlog); allowlist BootPinnedFinder git lookup
>
> Main pushes/dispatches of docker.yml shared nothing (group = run_id), so every
> merge queued its own build+publish on the 4 docker-ci runners. Standalone main
> runs now share docker-refs/heads/main-standalone without cancel-in-progress:
> an in-flight publish is never cancelled, only the newest pending run survives.
> Release calls (release-phase test/publish) keep per-run groups.
>
> Also adds the reviewed allowlist row for gateway/boot_pinned_imports.py
> (shutil.which('git') from 4b29ad8e4de), which made
> test_bare_which_and_known_path_tables_are_allowlisted red on main.
>
> Task: t_01a18284c98aa108
>
> * ci: drop resolution allowlist row (main resolved boot_pinned_imports which() site)
>
> * fix(kanban): probe notify holder pid via _pid_exists, not os.kill(pid, 0)
>
> Windows footguns lint (blocking) is red on main since bf9ca96750:
> os.kill(pid, 0) sends CTRL_C_EVENT on Windows. gateway.status._pid_exists
> uses psutil (EPERM counts as alive, matching the old PermissionError path).

## Code

- `.github/workflows/ci.yaml`
- `.github/workflows/docker.yml`

## Tests

Source test files changed by this commit:

- `tests/ci/_gha_expr.py`
- `tests/ci/test_ci_concurrency_groups.py`

New or changed test definitions present in the source diff:

- `test_main_pushes_coalesce_without_cancelling`
- `test_dispatches_on_one_ref_coalesce_without_cancelling`
- `test_dispatch_never_shares_a_group_with_push_release_or_other_ref`
- `test_release_runs_never_coalesce_or_cancel`
- `test_pull_requests_collapse_per_pr_and_cancel`
- `test_concurrent_callers_are_in_distinct_ci_groups`
- `test_concurrent_callers_never_share_a_callee_group`
- `test_callee_set_covers_the_hub_workflows`
- `test_format_matches_actions_semantics`
- `test_docker_main_pushes_coalesce_without_cancelling`
- `test_docker_release_calls_keep_per_run_groups_apart_from_main_pushes`
- `test_docker_pull_requests_collapse_per_pr_and_cancel`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

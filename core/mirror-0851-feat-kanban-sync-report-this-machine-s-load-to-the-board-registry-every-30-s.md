# feat(kanban sync): report this machine's load to the Board registry every 30 s

Patch 0851, source `192b9db93edf0cfe5bf9011681ef75a8cb717cec`.

## Behavior

feat(kanban sync): report this machine's load to the Board registry every 30 s. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `class Hub:`
- `def run_forever(keep_running: Callable[[], bool], interval: Optional[float] = No`
- `+"""This machine's load, reported to the Board's machine registry for display (Desktop Runners panel).`
- `+"""Machine load reporting for the Board registry: readers per platform, the 30 s reporter, the sync-loop hook."""`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> hermes_cli/machine_metrics.py reads CPU load (getloadavg, cpu count), memory in
> use (/proc/meminfo on Linux; sysctl hw.memsize + vm_stat on macOS) and free disk
> under the kanban home. The sync loop POSTs it to /v2/machines/me/metrics at most
> every 30 s; a Board without the route (404/405) is asked again after 10 min, and
> no failure reaches the sync loop. Display only (Desktop Runners panel meters).
>
> Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Code

- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/machine_metrics.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_machine_metrics.py`

New or changed test definitions present in the source diff:

- `test_linux_memory_reader`
- `test_macos_memory_reader`
- `test_collect_reads_this_machine`
- `test_reporter_sends_at_most_every_30s_and_backs_off_on_old_board`
- `test_hub_report_metrics_posts_to_own_route`
- `test_sync_loop_reports_metrics_once_per_window`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

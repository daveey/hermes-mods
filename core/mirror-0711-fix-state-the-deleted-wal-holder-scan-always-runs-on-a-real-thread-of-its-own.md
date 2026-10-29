# fix(state): [daveey] the deleted-WAL holder scan always runs on a real thread of its own

Patch 0711, source `e5dd8a90ecec2678fd7f4b3223b6527d52a53dfb`.

## Behavior

fix(state): [daveey] the deleted-WAL holder scan always runs on a real thread of its own. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `_DARWIN_FD_SCAN_THREAD_NAME = "hermes-wal-holder-scan"`
- `def _join_darwin_fd_scan(base: str | None = None, key: tuple | None = None,`
- `def test_timed_out_scan_is_not_cached(monkeypatch, tmp_path):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Since ca3e667b61 (one deleted-WAL holder scan in flight per process), every SessionDB open on macOS
> starts the scan pass from _join_darwin_fd_scan through threading.Thread, while holding _DARWIN_SCAN_LOCK.
> 93 test call sites replace threading.Thread with stubs whose start() runs the target inline or never.
>
> - Inline stub: the pass took _DARWIN_SCAN_LOCK again in its finally, on the thread already holding it,
>   and deadlocked. All of tests/tui_gateway/test_tui_gateway_server.py (671 cases) hung on every Mac from
>   10-27. faulthandler showed the main thread parked in hermes_state_dbfile.run under a stub's start().
>   run_tests.sh reported the file as CRASHED, with no results.
> - Never-start stub: the open waited out the 5 s scan timeout, and the stub recorded the pass as the
>   thread of the code under test. test_interrupt_before_agent_ready_prevents_late_turn_start counted 2
>   threads and called the scan as if it were the turn.
>
> CI runs on Linux, which never takes the darwin path, so nothing caught it.
>
> The pass now starts on threading.Thread as the threading module defined it, found through the main
> thread's class MRO. Binding threading.Thread at import is not enough: this module is often first imported
> while such a stub is installed, and the binding then captured the stub. The lock is unchanged.
>
> Tests: test_thread_stubs_never_capture_the_scan_pass[inline|never] fails before ("a threading.Thread stub
> captured the scan pass": ['_DarwinFdScan.run'] == []) and passes now. On macOS the server file runs again:
> test_prompt_submit_fails_open_inline_when_compute_host_dispatch_breaks (hung) and
> test_interrupt_before_agent_ready_prevents_late_turn_start (failed) pass, and the whole file passes in one
> process (671 passed in 373 s).

## Code

- `hermes_state_dbfile.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_state/test_state_db_darwin_fd_scan_hang.py`

New or changed test definitions present in the source diff:

- `test_thread_stubs_never_capture_the_scan_pass`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

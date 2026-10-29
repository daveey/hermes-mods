# fix(lsp): do not killpg a reused server pid when the reader exits during init [daveey]

Patch 0867, source `47ae53b158057763cba308ce65ea131764c9035c`.

## Behavior

fix(lsp): do not killpg a reused server pid when the reader exits during init [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _process_tree_snapshot(pid: int, *, hard_kill: bool):`
- `def kill_process_tree(pid: int, *, sig: Optional[int] = None) -> bool:`
- `class LSPClient:`
- `def _live_system_guard(request, monkeypatch):`
- `async def test_reader_exit_at_end_of_initialization_retires_client(tmp_path: Pat`
- `class TestKillProcessTree:`
- `def test_os_killpg_blocks_foreign_pgid():`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> The crash mock exits right after initialize. Cleanup calls kill_process_tree
> while asyncio has not reaped the session leader (start_new_session, so
> pgid == pid). getpgid then succeeding is not identity: a new group leader
> also has pgid == pid, and the live-system guard blocks that foreign killpg
> (CI run 40785286726, first attempt; retry passed).
>
> Record the spawn-time create_time and refuse the group signal unless the PID
> is still that incarnation. A parent walk that loses the process mid-flight
> treats it as a stale PID, the same no-op already allowed when Process() fails.
>
> Problem: test_reader_exit_at_end_of_initialization_retires_client failed with
> live-system guard blocked os.killpg(966, 9).
> Why 1: kill_process_tree killpg'd a PGID outside the test subtree (deadline.py:489).
> Why 2: the server had exited; a live foreign group leader now owned that PID,
> or the guard's parent walk failed closed when the child vanished.
> Why 3: killpg was keyed only by PID. The comment named the TOCTOU and left it.
> Root: process-group teardown had no spawn-time incarnation check.
> Not caught: the guard turns the wrong kill into a test error; nothing simulated
> getpgid succeeding on a reaped PID.

## Code

- `agent/deadline.py`
- `agent/lsp/client.py`

## Tests

Source test files changed by this commit:

- `tests/_fixtures/live_system_guard.py`
- `tests/agent/lsp/test_client_e2e.py`
- `tests/agent/test_deadline.py`
- `tests/test_live_system_guard_self_test.py`

New or changed test definitions present in the source diff:

- `test_cleanup_kill_carries_the_spawn_stamp`
- `test_reaped_pid_is_not_signalled_when_getpgid_succeeds`
- `test_spawn_stamp_does_not_signal_a_different_incarnation`
- `test_matching_create_time_still_kills`
- `test_non_group_leader_falls_back_to_single_kill`
- `test_killpg_allows_own_child_when_parent_walk_loses_it`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

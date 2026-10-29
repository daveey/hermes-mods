# test(guard): live-system guard sees its own children without psutil

Patch 0733, source `a80d30f3e1c6b1793a4abbb783f630e5794554de`.

## Behavior

test(guard): live-system guard sees its own children without psutil. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `Imported into tests/conftest.py so pytest registers the fixture there;`
- `_GATEWAY_LOOKALIKE_MARK = "spawns_gateway_lookalike"`
- `def _live_system_guard(request, monkeypatch):`
- `def test_os_kill_blocks_negative_one():`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> psutil is a core dependency only on Python >= 3.14, so on a 3.11 dev venv
> (MacBook) the guard's subtree walk answered 'foreign' for every pid,
> including a fixture's own direct Popen child: 6 teardown ERRORs in
> tests/hermes_cli/test_gateway_start_refused.py (live_pid's proc.kill()).
>
> Without psutil the guard now walks ppids via /proc or 'ps -o ppid=' (the real
> check_output), fails closed when a parent is unreadable, and treats a stale
> pid as a no-op like the psutil path. Foreign pids (init, our own parent) stay
> blocked. Regression tests in tests/test_live_system_guard_self_test.py.

## Code

- `tests/_fixtures/live_system_guard.py`
- `tests/test_live_system_guard_self_test.py`

## Tests

Source test files changed by this commit:

- `tests/_fixtures/live_system_guard.py`
- `tests/test_live_system_guard_self_test.py`

New or changed test definitions present in the source diff:

- `test_fixture_popen_child_can_be_killed_under_guard`
- `test_subtree_walk_without_psutil_finds_child_and_grandchild`
- `test_subtree_walk_without_psutil_fails_closed_when_ppid_unreadable`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

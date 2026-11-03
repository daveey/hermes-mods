# push-guard

## Landing-overlap gate (`gh pr merge` on a `require_check` repo)

After the exact-head check passes, the guard asks GitHub's compare API how far the base branch moved since
`merge-base(head, base)`. If any of those commits change a file the PR changes, the merge is refused: the green
check ran on an older base, so the tree that would land was never tested. The message names the overlapping
files, the commits that changed them, and the landing path (`hermes-core-wt ship <slug>` for `wt/*` branches,
otherwise rebase, push, wait for the check on the new head). A moved base with no shared files is admitted.
Compare lists only 300 files; when that list is full and shows no overlap, each PR path's base-branch history is
read instead. Unreadable compares, 1000+ commits of drift, or a truncated compare plus 60+ PR files block as
unverified. `PUSH_GUARD=off` still skips it. Tests: `test_landing_overlap.py` replays PR #475 (refused) and
PR #681 (admitted) from recorded API answers in `fixtures/`, through terminal and execute_code.

## Decision-module updates in running workers

Once this reload boundary has been activated, running workers pick up changes to
`guard.py` on their next tool call, without exiting. The hook stats its nanosecond
mtime and size before computing a verdict; unchanged code is not re-read. A changed
file is executed into a fresh module, then swapped only after successful loading.
A syntax/load error retains the last good decisions and logs one warning per changed
fingerprint. Successful reloads log `push-guard: reloaded guard.py`.
`__init__.py`, hook registration and tool schemas are **not** hot-reloaded by this
boundary; their existing activation paths still apply.

Regression coverage: `tests/test_guard_decision_reload.py` at the repository root
loads temporary plugin copies and exercises the registered pre-tool hook across
allowed-to-refused edits, syntax errors, recovery and same-second/same-size edits.

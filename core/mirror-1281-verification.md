# Public core mirror: 1281-entry snapshot

## Frozen inventory

- Source tip: `9117ffc398e` (fork PR #315 tip, patch 1281).
- Upstream exclusion tip: `2d2808ee84549f9836b0056750265058e5488a8a`; merge-base `5608b5faf19ac22d023e62a8fd9675a42d1a61c6`.
- Previous snapshot: [1253](mirror-1253-verification.md). Published ordinals 0001-1253 are unchanged.
- 28 new entries 1254-1281, appended in source `git log --reverse --no-merges` order for the commits whose SHA header is not already published. The SHA set of all 1281 patches equals the source set minus later commits: no omissions or duplicates.
- Feature docs: [kanban-supervisor-requests.md](kanban-supervisor-requests.md) (1278-1281), [frozen-decision-reset.md](frozen-decision-reset.md) (1273 now final), and the grouped docs linked from `README.md` for 1254-1277.

## Executed checks

A detached worktree at the merge-base applied all 1281 entries in numeric order:

```sh
git am -3 --empty=keep core/patches/[0-9][0-9][0-9][0-9]-*.patch
```

Exit 0, no manual resolution. Comparing the applied tree with the frozen source tip leaves the same 37 public-normalization paths as the 1253 snapshot (renamed contributor markers and example-identity/path substitutions). Of the files touched by the new entries, only `kanban_placement.py`, `test_kanban_placement.py`, `test_kanban_wake_check.py` and `test_kanban_worker_owner_restart.py` appear, and each differs only by the same substitutions; the supervisor-request module, its test and the tool schemas are byte-identical to source.

In that applied tree, `bash scripts/run_tests.sh -j 2 tests/hermes_cli/test_kanban_supervisor_requests.py`: **25 passed, 0 failed**. Other suites touched by 1254-1277 were not rerun by this publication.

All new patch files and docs were scanned with the private denylist: zero hits. The pre-commit gate also runs gitleaks and `core/check_coverage.py`.

## Boundaries

Source mirror and documentation only. No running gateway, dispatcher or Desktop deployment is certified.

# Public core mirror: 1253-entry snapshot

## Frozen inventory

- Source tip: `2dc64603e1ed4f667baa8293c4345136072ba773`.
- Upstream exclusion tip: `2d2808ee84549f9836b0056750265058e5488a8a`.
- Merge-base: `5608b5faf19ac22d023e62a8fd9675a42d1a61c6`.
- Canonical PR296 entry: **1239**, source `903f425fa63606c267c2b45646beff2a74348d19`.
- Feature doc: [frozen-decision-reset.md](frozen-decision-reset.md).

Full raw format-patch exports were regenerated in scratch for all 1253 non-merge
source commits. Their source SHA set equals the published SHA set: no omissions or
duplicates. Published ordinals 0001-1235 are unchanged byte-for-byte. The 18 new
entries 1236-1253 append in first-parent merge order; the five branch commits
1245-1249 are placed at the tested sync merge that integrated them. None of the
new entries required a merge-resolution adaptation. Patch 1228 retains the
previously documented curated merge resolution.

## Executed checks

A detached worktree at the merge-base applied all 1253 entries in numeric order:

```sh
git am -3 --empty=keep core/patches/[0-9][0-9][0-9][0-9]-*.patch
```

Exit 0, no manual resolution. Applied tree:
`bd5631eae97cc0b7e38e4a3beffed79f013cd0c5`.

Comparing that tree with the frozen source tip leaves 37 public-normalization
paths, as in the [1235 snapshot](mirror-1235-verification.md): three renamed
contributor markers, 33 other inherited example-identity/path substitutions, and
the example laptop comment in `test_kanban_worker_owner_restart.py`. Only that
last path was touched by source changes since the prior snapshot; its remaining
difference is still exactly the same single comment substitution. The other
residual paths were untouched by the new source commits. No new unexplained
source-tree difference appears. These inherited public substitutions mean the
mirror is not byte-for-byte identical to the private source tree.

In a second detached source worktree at the frozen tip:

```sh
bash scripts/run_tests.sh -j 2 tests/gateway/test_frozen_decision_reset.py
```

Result: **9 passed, 0 failed**, exit 0. This is the compression/reset matrix,
including event/waiter settlement and payload/digest preservation. Injected caller
cancellation during the write remains untested. Other follow-up suites were not
rerun by this publication. The PR296 readback confirmed MERGED at the source SHA
above, and `All required checks pass (v2)` was SUCCESS.

All 18 new patch files and the new feature/report docs were scanned in full with
the private denylist's case-insensitive expressions: zero hits. The normal
pre-commit gate also checks the staged diff with gitleaks and the denylist.
`python3 -B core/check_coverage.py` checks the staged patch/doc inventory.

## Boundaries

This is a source mirror and documentation publication only. No running gateway,
Desktop deployment, click path or plugin integration is certified by it. The
source checkout HEAD was not changed. Both verification worktrees were removed.

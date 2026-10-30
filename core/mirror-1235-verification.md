# Public core mirror: 1235-entry snapshot

## Frozen inventory

- Fork tip: `2385895f5ac46acd0461c4ce4daa8dd61f1d1e06`.
- Upstream exclusion tip: `6337f1f22fe282636165c24568fa6da018653d30`; merge-base `5608b5faf19ac22d023e62a8fd9675a42d1a61c6`.
- Inventory: 1235 non-merge commits, one mail patch per source SHA, numbered `0001` through `1235`.

This publication appends 33 entries, `1203-1235`, to the published 1202. Full raw exports were regenerated into a temporary directory and compared by SHA header with the published directory. All 1202 published patches were still present; 1158-1202 matched their fresh export after the public identity and example-host substitutions, so they were kept byte-for-byte, with no renumbering.

## Why the new entries are not in `git log` order

Three new source commits (the drain fix, the upstream-sync gate and its test) and one CI-slices commit reached the fork through merged branches. In `git log --reverse --no-merges` order they sort before patches that were already published, so inserting them at their log position would renumber 36 published patches and breaks replay: a later published patch conflicts with the drain fix because the conflict was resolved inside a merge commit that `--no-merges` omits.

Instead 1203-1235 are appended in first-parent order of the fork history after the published tip, with each merged branch's commits placed at its merge. All but one replay cleanly. Patch 1228 (the drain fix) does not apply raw at its position, so its mail keeps the source SHA, subject, author and date, and its diff is the net change to the files the commit touched as they stand after the merge, plus the two extra files the merge resolution changed (`hermes_cli/kanban_owner_unavailable.py` and its test). The first-parent merge residual check found no other file differing.

## Executed replay and tree audit

A detached source worktree at the merge-base replayed every patch in numeric order:

```sh
git am -3 --empty=keep core/patches/[0-9][0-9][0-9][0-9]-*.patch
```

Result: exit 0 across all 1235 mail patches, no manual resolution in the final replay. The applied tree was compared with the source tip tree (`git diff --name-only <tip> HEAD`): 37 paths differ, all explained:

- 36 paths are the inherited public normalization (3 renamed contributor-marker files and 33 files with example-identity substitutions), identical to the set that differed at the previously published tip (a replay of 1-1202 versus its source tip).
- 1 path, `tests/hermes_cli/test_kanban_worker_owner_restart.py`, differs by one comment line where a laptop hostname was replaced with `example-laptop`.

No unexplained difference remains. The source checkout HEAD was not changed; the replay worktree and its scratch branches were removed.

## Privacy and coverage

The author header is the public noreply identity. The 33 new patch files and the 7 new feature docs and this report were scanned in full with the private denylist's case-insensitive expressions: every count is `0`. Operational routing literals in CI YAML (`hermes-hub` runner labels) are kept as written because rewriting them would change the patch context. `python3 -B core/check_coverage.py` exits 0 with the staged tree.

## Not claimed

No full test suite, hosted CI result or live deployment is claimed. The new feature docs describe what each patch's own tests prove; where a path has no test the doc says so.

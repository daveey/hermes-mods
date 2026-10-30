# Public core mirror: 1301-entry snapshot

## Frozen inventory

- Source tip: `7fbf27b309d3da51a1c6e54a26c61c5e1aca2a2d` (fork main, 2026-10-30 11:08 PDT).
- Upstream exclusion tip: `2d2808ee84549f9836b0056750265058e5488a8a`; merge-base `5608b5faf19ac22d023e62a8fd9675a42d1a61c6`.
- Previous snapshot: [1299](mirror-1299-verification.md). Published ordinals 0001-1299 are unchanged.
- Two new entries, the source commits whose SHA headers were unpublished, in `git rev-list --reverse --no-merges` order:
  - 1300 `642d2c6878` fix(launch): preserve permission errors across probe boundaries (PR #339), in [launch-preflight-bound.md](launch-preflight-bound.md);
  - 1301 `7fbf27b309` fix(gateway): hold old-owner mutation_lock until a cancelled caller's native rehome write commits, in [frozen-decision-reset.md](frozen-decision-reset.md).
- The 1301 SHA headers are unique and equal the set of `git rev-list --no-merges base..source-tip` (1301 commits).
- Policy: [mirror-policy-1301.json](mirror-policy-1301.json). It is the 1299 policy rebound to the new tip: the two
  new entries add no normalization path, and the same 37 paths remain (12 explained by substitutions, 22 fingerprints,
  3 renames).

## Executed checks

```sh
python3 -B core/verify_mirror.py --source-repo <fork clone> \
  --source-tip 7fbf27b309d3da51a1c6e54a26c61c5e1aca2a2d \
  --base 2d2808ee84549f9836b0056750265058e5488a8a \
  --patch-dir core/patches --policy core/mirror-policy-1301.json
```

Exit 0 on the hub:

```json
{"base": "2d2808ee84549f9836b0056750265058e5488a8a", "merge_base": "5608b5faf19ac22d023e62a8fd9675a42d1a61c6", "patches": 1301, "source_tip": "7fbf27b309d3da51a1c6e54a26c61c5e1aca2a2d"}
```

The source repository was the same fresh bare fork clone as the 1299 snapshot, with the hub checkout's objects as an
alternate.

In a detached source worktree at the frozen tip:
`bash scripts/run_tests.sh -j 2 tests/gateway/test_rehome_cancel_lock.py tests/hermes_cli/test_launch_probe.py tests/pm/test_worker.py`
gave **49 passed, 0 failed, 1 skipped** (platform-gated), exit 0.

Both new patch files and the docs were scanned with the private denylist (comment-stripped, case-insensitive):
zero hits. The pre-commit gate also runs gitleaks and `core/check_coverage.py`.

## Boundaries

Source mirror and documentation only. No running gateway, dispatcher or Desktop deployment is certified.

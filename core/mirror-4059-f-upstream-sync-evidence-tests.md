# Upstream-sync evidence and fetch replay tests

Mirror batch 6; ordinals 4014, 4038, 4049, 4054.

## Behavior

Test-only changes pin exact SHA shape, audit-receipt ordering, recovered-blob identity and CI-compatible Bash selection.

## How it works

Malformed caller SHA values deliberately match receipt metadata so equality cannot mask format validation. Dual approvals pin the first accepted receipt in success detail. Blob recovery uses real binary prehashing followed by an injected successful store with the wrong identity. Fetch helpers use replay_bash directly and a fixture-bin Bash symlink for nested workflow commands.

## Code

Source paths touched by this feature group (including regression fixtures):

- `tests/ci/test_fetch_merge_base.py`
- `tests/ci/test_lint_advisory_fetch.py`
- `tests/scripts/ci/test_upstream_sync_offkey_review.py`
- `tests/scripts/ci/test_upstream_sync_review.py`

Mail entries in the ordered series:

- 4014: [`fdb3e450035447149d2faa91182589a83391af50`](patches/4014-test-ci-pin-off-key-receipt-full-SHA-validation-daveey.patch)
- 4038: [`4f17d83e5bf4519b22ed989169d526a634eacb01`](patches/4038-daveey-Test-first-approving-receipt-in-upstream-sync-success-detail.patch)
- 4049: [`6825785665f98163977037f5bb9789691e81348e`](patches/4049-daveey-Replay-fetch-integration-tests-with-the-CI-bash-version.patch)
- 4054: [`608185b126e557d5236ae8faec9100bf2fdb9c44`](patches/4054-daveey-Test-recovered-blob-store-output-identity-validation.patch)

## Tests

- `tests/scripts/ci/test_upstream_sync_offkey_review.py`: full lowercase SHA is accepted; short, long, uppercase, nonhex and trailing-newline values reject even with matching metadata. A Kanban approval preceding a GitHub approval remains the named success receipt, with no new review filed.
- `tests/scripts/ci/test_upstream_sync_review.py`: binary bytes are prehashed correctly; return-code-zero storage with a different stdout OID raises cannot-store refusal and exercises hash-before-store ordering.
- `tests/ci/test_fetch_merge_base.py`, `tests/ci/test_lint_advisory_fetch.py`: only shell selection changes; fetch fallback, lazy tree access and workflow assertions remain in place. The latter also supplies modern Bash to nested commands.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results. Source reports, where quoted, are historical/reported evidence only; replay verification belongs to the parent mirror task.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks. Use the README’s complete series recipe, not a glob of just this feature’s patches.

## Gotchas

No production review gate changes. Native scheduled macOS workflow verification is not established by the documentation pass.

This document makes no deployment or live-display verification claim; those checks are owned separately.

## Plugin?

No. These are core lifecycle/state contracts or their regression tests; an external plugin cannot replace the underlying ownership boundary.

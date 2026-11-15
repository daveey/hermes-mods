# Independent exact-head review for fork-authored upstream syncs

Mirrored entries: 3855, 3895.

## Behavior

A mechanical upstream sync can land with exact-head green CI alone; a sync containing fork-authored resolution or extra fork commits additionally waits for independent exact-head approval through the existing ship review gate. Classification is available without landing via `--classify`.

Only a freshly fetched upstream ref from the canonical public upstream repository is trusted when excluding upstream-owned commits. Invalid upstream URLs, unreadable review infrastructure and unexpected ship-tool faults refuse instead of bypassing review. Implementation ownership comes from `--impl-task`, not an environment fallback.

## How it works

Classification examines commits introduced over the fork base but absent from verified upstream. Two-parent merges with empty remerge diffs are mechanical; nonempty remerge diffs name review files, nonmerge fork commits require review, and octopus merges are conservatively fork-authored. A landed-head dry run derives the pre-landing first-parent base.

`ShipReview` dynamically loads the existing landing tool, validates its expected entry-point signatures, reuses keyed review cards, calls review_gate and review_reply_guard, and carries the approval receipt into the landing PR. Missing review evidence parks; unjudgeable evidence refuses. `verified_upstream` validates every configured upstream URL and force-fetches upstream main before classification. The follow-up wraps arbitrary tool-load/check exceptions as named ReviewUnavailable faults and removes the implementation-task environment variable.

## Code

- `.github/LANDING.md`
- `scripts/ci/upstream_sync.py`
- `scripts/ci/upstream_sync_review.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/scripts/ci/test_upstream_sync_review.py`: Uses local Git conflict merges, fixup commits and a ship-tool stub to assert fork-authored heads park without approval, reuse one review card and land only after exact-head approval; mechanical heads never call the review gate. Covers missing/incompatible tools, worker review actions, unanswered points, dry runs, false upstream remotes, a forged local upstream ref repaired by fetch, named tool faults, and flag-only implementation identity.
- `tests/scripts/ci/test_upstream_sync.py`: Fixture upstream uses the canonical configured URL with a local insteadOf transport so the gate tests exercise URL validation without a network dependency. The change is fixture plumbing, not new behavior assertions in this file.
- `tests/hermes_cli/upstream_gate_fake.py`: Updates the fake Git interface for the review-classification calls; this is supporting test infrastructure, not an independent approval assertion.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

A clean CI run is insufficient for fork-authored sync content. The classifier dry run now fetches the upstream tracking ref even though it does not stage or land a change. A trusted URL check is not a general Git configuration security boundary (the tests themselves use insteadOf transport mapping). This pass does not invoke classification, file review cards or land anything.

## Plugin?

No. This extends the core sync landing gate while deliberately reusing the ship tool’s review authority.

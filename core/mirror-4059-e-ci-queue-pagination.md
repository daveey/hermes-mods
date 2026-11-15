# Bounded queue pagination and confirmation scans

Mirror ordinals: 3982, 4007.

## Behavior

A short nonterminal workflow-run page no longer hides a waiting macOS runner job; a second bounded scan can recover runs shifted behind the first scan's offsets.

## How it works

Each open run state is scanned twice and run IDs are unioned, excluding the caller. Each run's jobs are fetched once. Scanning stops on empty, or a short page whose page offset is at least total_count; missing totals require empty. The existing maximum-page saturation and fail-open policy remain.

## Code

- `scripts/ci/macos_os_tests_gate.py`

## Tests

- `tests/ci/test_macos_os_tests_workflow.py`: Controlled short pages with/without totals reveal later waiting jobs; confirmation finds an offset-shifted run with one jobs read. Exact offset==total_count stops below and at the page cap; final supported pages are read in both scans.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

Two scans are bounded reconciliation, not snapshot completeness under arbitrary concurrent churn. A full final page is not an exhaustion proof merely because its offset meets a total.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.

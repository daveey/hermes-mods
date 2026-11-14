# PR conflict detection tests exercise either GitHub conflict field

Mirrored entries: 3865.

## Behavior

The regression tests require either mergeable=false or mergeable_state=dirty to count as a conflict, even if the other field is still unknown. This batch changes tests only, not the conflict policy.

## How it works

A shared CONFLICTS parameter set drives both park-time refusal and parked-card sweep-wake tests over (false, dirty), (false, unknown) and (null, dirty). The API fixture accepts each pair independently, so deleting either half of the production OR condition can no longer survive a fixture that always sets both.

## Code

These entries modify only the test files listed below; production behavior is unchanged.

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/hermes_cli/test_kanban_ci_progress_pr.py`: Checks each conflict-field pair refuses a park with PR/rebase guidance and wakes a previously parked card once, returning it to ready without inventing a runner blocker.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

Unknown by itself is not made a conflict. These are deterministic API fixtures; they do not fetch a current PR or verify a live CI lane.

## Plugin?

No new plugin. This pins the shared core PR-progress classifier.

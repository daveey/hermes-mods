# Lane PR observations and CI park whole-commit wait

**Scope:** Head-bound GraphQL snapshot, no-check heads, CI widened waits.

**Source ordinals:** 6486, 6487, 6488, 6523

## Behavior

- PR reads are attributed to lanes-flow through the child process env (shared env not mutated) and expose terminal states such as CLOSED; required checks come from one head-bound GraphQL snapshot followed by the existing sequential confirmation; an explicit null rollup is parsed as empty checks (whether an empty list counts as green is decided by the caller, not in this series). The parser returns nothing (unknown) on GraphQL errors, a non-OPEN/MERGED/CLOSED state, review or check pagination, a head/commit mismatch, a non-boolean `isRequired` or an unknown check enum; MERGED/CLOSED PRs return no checks. Only required checks are bucketed.
- When the one run a CI park watches succeeds early, the sweep reads the commit's workflow runs once; if others are pending the card stays parked with one widened-wait event and no comment. Later sweeps read only that commit's runs; a failed run is never widened (it wakes at once); a failed read of the commit's runs falls back to judging the single run; the widened wait is capped at 60 minutes, after which the card wakes with a comment listing the still-pending runs. Newest run per workflow decides, plus an older run on another event only if it failed; a commit with more runs than one page is reported unknown (also in the PR-checks path).

## How it works

- `kanban_lanes_flow_pr*.py`, `kanban_ci_progress.py`.

## Code

Files touched by the series (paths relative to the source tree):

- `hermes_cli/kanban_ci_progress.py`
- `hermes_cli/kanban_external_adapters/github.py`
- `hermes_cli/kanban_external_watch.py`
- `hermes_cli/kanban_lanes_flow_pr.py`
- `hermes_cli/kanban_lanes_flow_pr_snapshot.py`

## Tests

Source test files changed or added by the series:

- `tests/hermes_cli/test_kanban_ci_wait_whole_commit.py`
- `tests/hermes_cli/test_kanban_lanes_flow_pr.py`
- `tests/hermes_cli/test_kanban_lanes_flow_pr_snapshot.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6486-6523, 4 commits: 6486, 6487, 6488, 6523) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Partial errors rejected.
- Truncated contexts/reviews and missing/malformed rollups are rejected rather than paginated; unknown observations stay out of the success cache.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Lane engine core.

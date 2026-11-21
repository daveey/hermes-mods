# Spend-budget sparing with verified PR ownership

Mirror entries: 5261, 5262, 5263, 5264, 5265. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

An over-budget card may be spared through its own published-PR metadata, or through a PR candidate in its own run's unquoted comment whose open main-targeting PR body has exactly one `Hermes-Card: <this-task>` trailer. A mention alone is discovery, not ownership proof.

## How it works

The shared `gh pr view` read now includes `body` alongside state/comments/head/base; cached parsed trailer values retain duplicates and empty values, and unknown body state fails closed. Comment discovery accepts core-repository URLs and forms such as `PR #7`, `PR 7` and `PR7`, but still verifies state/base/trailer before exemption. Candidates are considered newest-number first, capped at 20; at most eight uncached comment-candidate PR reads per card are charged. Valid cached candidates are still checked after that read cap. The write-transaction recheck uses database-only recognized candidates.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `hermes_cli/kanban_db_dispatch_pr_state.py`
- `hermes_cli/kanban_spend_budget.py`
- `hermes_cli/kanban_spend_pr_owner.py`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Source tests cover exact/single ownership trailers, duplicates/conflicts/empty values, CRLF bodies, own-run versus foreign comments, quoted lines, open/main constraints, no reads below budget, shorthand candidate forms, uncached-read cap/fail-closed behavior and free cached candidates. Existing active-head and park-invariant fixtures are updated to expect the shared body read; fixture reuse is fixed without shadowing collected tests.

Relevant source suites/fixtures:

- `tests/hermes_cli/test_kanban_active_pr_awaited_head.py`
- `tests/hermes_cli/test_kanban_park_invariants.py`
- `tests/hermes_cli/test_kanban_spend_pr_owner.py`
- `tests/hermes_cli/test_kanban_spend_pr_trailer.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

This sparing applies to the recognized core-repository PR ownership path, not every arbitrary URL or repository. It does not approve/merge a PR, reset real spend, or authenticate text merely because it says PR. Live GitHub behavior is untested; tests control the GitHub seam.

## Why core instead of a plugin

The spend decision, shared PR cache and run-comment ownership mapping are used inside dispatch and its transactional recheck. A plugin exemption would risk skipping the provenance/read-budget contract.

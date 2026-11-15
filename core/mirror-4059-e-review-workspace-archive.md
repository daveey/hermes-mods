# Review workspace archival does not reopen dependency history

Mirror ordinals: 3969.

## Behavior

Treats workspace_archived as bookkeeping rather than lifecycle activity when deciding whether an old finished review can be exempted for one continuation.

## How it works

The explicit historical-parent event exclusion list gains workspace_archived. Unknown events still fail closed. The continuation allowance remains a claim-consumed grant, not a side effect of merely checking respawn guards.

## Code

- `hermes_cli/kanban_dependency_review.py`

## Tests

- `tests/hermes_cli/test_kanban_dependency_review_history.py`: Both legacy/current park shapes ignore archival bookkeeping, allow one claim, then retain active_pr after claim and crash; lifecycle and unknown events still block.
- `tests/hermes_cli/test_kanban_review_workspace_archive.py`: Real local and replica-parent fixtures combine archived historical reviews and a reaped orderly park. Repeated reads do not consume release; one claim does, and later reclaim cannot reuse it.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

Only enumerated bookkeeping events are exempt. Workspace archival must not hide a later claim, completion, unblock or unknown event.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.

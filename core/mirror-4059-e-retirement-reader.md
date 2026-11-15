# Retirement-reader failures retain replay reset deadlines

Mirror ordinals: 3960.

## Behavior

Adds coverage ensuring an unreadable retirement state does not manufacture evidence of a successor or erase quota reset delays. No production changes.

## How it works

The retirement reader raises RuntimeError while four independent consumers evaluate the same future wall reset: failed wake scheduling, durable notice retry, scalar reset and failure-dictionary reset.

## Code

Test-only patch set; production implementation is unchanged. The regression files below are the changed code.

## Tests

- `tests/hermes_cli/test_superseded_host_loops.py`: Asserts wake/scalar/dictionary consumers preserve the exact reset and notice retry preserves reset plus its defer slack.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

Code skew or a reader failure is not retirement proof; this test concerns the failure path, not successful successor detection.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.

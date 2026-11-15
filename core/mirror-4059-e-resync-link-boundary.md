# Resync link preservation at the inclusive boundary

Mirror ordinals: 3964.

## Behavior

Documents conservative link preservation and adds exact inclusive-boundary tests without changing sweep logic.

## How it works

The resync sweep holds links present at its starting read and spares a child's links when linked/unlinked events occurred at or since that read. The new comment makes explicit that protecting local work can retain an edge the Board deleted.

## Code

- `hermes_cli/kanban_sync_resync_sweep.py`

## Tests

- `tests/hermes_cli/test_kanban_sync_live_resync_sweep_boundary.py`: For linked and unlinked events at since-1, since and since+1, asserts only the qualifying child edge survives, sibling edges are swept, counts agree and tombstone keys exactly match removed edges; no outbox/card hold masks the boundary.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

A retained Board-deleted edge may linger until rebuild or be re-pushed by repair. This trade deliberately favors preservation over tombstoning local work.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.

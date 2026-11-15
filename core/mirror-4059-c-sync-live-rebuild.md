# Kanban live rebuilds preserve concurrent writes and cold-start at the feed head

Mirrored entries: 3848, 3849, 3850, 3851, 3877, 3878.

## Behavior

A live-file resync sweep does not delete rows whose outbox operations existed before the rebuild, even if a concurrent reconciliation acknowledges those operations during paging. Newly created rows and new/recreated links also survive.

A never-pulled primary live file with a multi-page history rebuilds from a partitioned snapshot rather than slowly replaying the entire feed before releasing its outbox. An interrupted cold rebuild stays cold and is retried as a rebuild. A separately started failed-result observer reads the board’s actual sync mode.

## How it works

`held_at_start` records link keys before reading every outbox operation in any state, plus the cards those operations name; child-card lookup is grouped in bounded chunks. The sweep rechecks candidates in the apply transaction, requires creation time strictly before the rebuild second, bounds links to keys present at the start, and excludes child cards with linked/unlinked events since that point. The 410 test backdates fixtures so it still tests deletion of genuinely old rows.

`pull_live` peeks the initial changes page without applying it. If a cold cursor-zero file has more history, a partitioned `resync` upserts the snapshot and returns a rebuild sentinel to repair logic; otherwise ordinary credited pulling retains the page budget. This cold path intentionally does not sweep absent local cards. `connect_existing` registers sync mode without installing schema for the failed-result observer.

## Code

- `hermes_cli/kanban_sync_resync_sweep.py`
- `hermes_cli/kanban_failed_result_observer.py`
- `hermes_cli/kanban_sync_backlog.py`
- `hermes_cli/kanban_sync_client.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/hermes_cli/test_kanban_sync_live_resync_sweep.py`: Exercises acknowledgment during snapshot paging, new links written/acked mid-rebuild, old absent-link deletion, same-second row protection and unlink/relink of an old key. Asserts preserved rows/links create no tombstones; the 410 fixture is aged before rebuilding.
- `tests/hermes_cli/test_kanban_sync_cold_live.py`: Asserts one-round cold catch-up sends a run-start and clears pending outbox without importing another partition; warm lag still uses the bounded budget; neutral requeue releases the Board lease for reclaim. A failed snapshot leaves cursor zero and the next round rebuilds successfully.
- `tests/hermes_cli/test_kanban_failed_result_sync.py`: Adds observer_process entry coverage so real sync echo preserves failed-result closure even when that observer did not run board initialization.
- `tests/hermes_cli/test_kanban_sync_push_starvation.py`: Updates the round call-count assertion to account for the cold initial peek instead of a full live pull budget while keeping backlog/head bookkeeping coverage.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

The round lock does not serialize every reconcile push, hence the start snapshot is required. Timestamp precision is whole seconds: same-second rows are deliberately spared until a later rebuild. Snapshot upserts on cold attachment are not the 410 absent-row sweep, because local cards may not have been imported yet. This documents fixture assertions, not a replay or remote Board verification.

## Plugin?

No. Outbox custody, snapshot application and feed cursor progression are core sync invariants.

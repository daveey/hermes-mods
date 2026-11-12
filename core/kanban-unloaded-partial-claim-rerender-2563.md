# Unloaded sweep re-renders only undelivered events of a claimed range

Patches: **2515**.

## Behavior

The local unloaded-session sweeper claims a subscription's whole cursor range and writes one notice keyed by every event in it. The notice writer refuses a row whose event keys only partly overlap the chat's lineage (`KanbanNoticeOverlapError`). When a live poller had already written the row for an earlier event (for example `blocked`) and died before its ack, every sweep pass raised, rewound the cursor and logged a traceback, and the later `done` notice waited until the chat was opened. Now claimed events already present in the lineage are dropped before rendering; only the missing ones are written. A range with nothing left is acked without a row.

Also, one corrupt record no longer aborts the batch: `SweepBatches.flush` now catches `OverflowError` (an absurd owner time such as `-1e30` overflowing `datetime.fromtimestamp`), releasing only that chat's claims while other chats in the same flush are delivered.

## How it works

`_deliver_sub` in `tui_gateway/kanban_unloaded.py` calls new `_undelivered(db, tip, slug, claimed)`, which asks `db.kanban_notice_keys_delivered` for the `<board>:<event id>` keys already held. A failed key read (`sqlite3.Error`, `OSError`) keeps the whole range, and the writer's atomic recheck still guards a race after the filter: it raises, rewinds and the next pass re-filters. In `tui_gateway/kanban_remote_late.py` `SweepBatches.flush` adds `OverflowError` to its caught tuple, as `collect` already does. See [kanban-unloaded-session-delivery.md](kanban-unloaded-session-delivery.md) and [kanban-notice-refusal-rewind.md](kanban-notice-refusal-rewind.md).

## Code

`tui_gateway/kanban_unloaded.py`, `tui_gateway/kanban_remote_late.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tui_gateway/test_kanban_unloaded_partial_overlap.py` (partly delivered range writes only the missing notice; fully delivered range is acked without a row; failed key read offers the whole range and the writer still guards it; a race after the filter rewinds and retries) and `tests/tui_gateway/test_kanban_remote_late.py` (an absurd owner time in one chat does not hold another chat's group). Covered entry path: the unloaded sweeper and the remote late-notice sweep against a session DB. Live Desktop or chat surfaces are untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

No migration or new flags. The key read adds one lookup per claimed range on the passive-row path.

## Plugin

Core change; no plugin required.

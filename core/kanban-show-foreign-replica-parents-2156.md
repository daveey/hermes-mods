# kanban_show reads foreign cards from the Board replica

Patch **2150**.

## Behavior

`kanban_show(task_id)` for an id that is not on the local board (for example a parent card created on another gateway) falls back to the synced Board replica and returns a read-only detail labelled `"source": "replica"` with an `owner` field (the card's `created_on`). Local cards report `"source": "live"`. Nothing is imported or made writable. A missing card is still "not found", the live card wins when both exist, foreign comments are still refused, and with sync disabled no foreign read happens.

## How it works

`tools/kanban_show.py` splits into `show` and `_detail`. `show` takes the board grant first, tries `kb.get_task`, then `kanban_foreign_card.open_replica(_live_db_file(conn))`. For replica reads `_detail` skips the own-card capsule, queue place and hint, and the undelivered-comment lookup (they belong to this gateway's live dispatcher, not a replicated owner's cached ids) and returns `undelivered_comments: []`.

## Code

- `tools/kanban_show.py` (uses `hermes_cli/kanban_foreign_card.py`).

## Tests

**Source regression coverage; not executed in this mirror documentation pass.** Names come from the source diff; no pass/fail result is claimed.

`tests/tools/test_kanban_show_replica.py`: `test_foreign_parent_read_is_labelled_and_does_not_import`, `test_disabled_sync_does_not_read_foreign_card`, `test_missing_card_remains_not_found`, `test_live_card_wins_over_replica`, `test_foreign_comment_is_still_refused`.

## Applying

Apply ordinal 2150 as part of the snapshot-aware full replay described in the [README](README.md) (full recipe in its Applying section), in published order, not as an independent patch. It depends on `kanban_foreign_card` from earlier patches. No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

- Replica data is only as fresh as the last sync.
- The read is not a cross-board permission grant.

## Could this be a plugin?

No. It is the core `kanban_show` tool reading core replica storage.

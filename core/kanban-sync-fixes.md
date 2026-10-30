# Kanban sync: pickup publish, partition repair, lease renewal, claim fence

Patches:

- `patches/0967-feat-kanban-publish-configured-machine-pickup-on-syn.patch`
- `patches/0996-fix-kanban-recover-consumed-cross-gateway-dependency.patch`
- `patches/1004-fix-kanban-lease-renewal-skips-a-task-with-no-curren.patch`
- `patches/1048-fix-kanban-a-pull-never-unclaims-an-unacknowledged-l.patch`
- `patches/1064-fix-kanban-sync-diff-ignores-other-gateways-chat-lin.patch`

Four independent defects in the Board sync client and dispatcher found in live use.

## Behavior

- 967: the sync client posts `kanban.sync.pickup` (null when unset) once per round and only when it changed, because the Board gives a machine pin `runs_on` only when `/v2/machines` advertises pickup.
- 984: consumed cross-gateway dependency links are recovered by a partition repair (`kanban_sync_partition_repair.py`). The commit message also carries the earlier `/api/ws?client=agent` change (such chats born hidden, submits not human input), which is already in the 0958 overlay.
- 992: lease renewal skips a task with no current run (it sent an empty `run_uid`, refused with 400) and logs at INFO how long a renewed lapsed lease had lapsed.
- 1048: a pull never unclaims an unacknowledged local claim. `apply_remote_changes(local_pending_wins=True)` drops task fields that have a non-rejected outbox op, read inside the apply transaction; spawn records the claimed run id.

## How it works

The repairs live in `kanban_db_sync.py` and `kanban_sync_client.py`; the dispatcher's `_set_worker_pid` takes the claimed run id so the spawned event and run pid are never empty.

## Code

`hermes_cli/kanban_sync_client.py`, `hermes_cli/kanban_db_sync.py`, `hermes_cli/kanban_sync_partition_repair.py`, `hermes_cli/kanban_db_dispatch.py`.

## Tests

`tests/hermes_cli/test_kanban_worker_node.py` (pickup), `test_kanban_sync_partition_repair.py`, `test_kanban_sync_leases.py`, `test_kanban_claim_pull_fence.py`.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

None known beyond the notes above.

## Plugin

None.

## Follow-up patch (1064)

**1064** (`b811677a4ef`, #221): a picked-up copy of another gateway's card (`kanban_worker_node.import_cards`) carries runs, comments, attachments and events but no chat links (the creator's chats are read from the replica). `diff()` still compared every `task_chat_links` row of a live card against the replica, so each running foreign copy was a permanent `child_mismatch` with `pending=0`. Links of this gateway's own chats on those cards, and every link on own cards, are still compared. The commit message also lists squashed desktop session-outcome commits (already mirrored as 1008-1011); the diff of this patch is only the sync diff in `hermes_cli/kanban_sync_client.py` plus `tests/hermes_cli/test_kanban_sync_foreign_chat_links.py`. Tests were added or changed by these commits; they were not re-run for this mirror.

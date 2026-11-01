# Kanban sync keeps dependency and chat-link edges; placement no longer pins Linux work local

Patches:

- `patches/1082-fix-kanban-foreign-copies-keep-the-Board-s-dependenc.patch`
- `patches/1084-fix-kanban-placement-a-Baby-Bear-title-on-Linux-CI-o.patch`
- `patches/1110-fix-kanban-capture-chat-link-deletes-placement-links.patch`
- `patches/1510-fix-kanban-recover-own-chat-links-on-foreign-copies-.patch` (`42af4d197c`)

Fixes to what the Board sync preserves for foreign card copies and chat links, and to where a placed card runs.

## Behavior

- 1082: evicting a foreign copy keeps an edge whose child is still in the file; a pickup imports the card's parent edges (the foreign parent is mirrored at import); `repair_partition_links` restores any replica edge from a foreign parent to a card in the file, under the same tombstone/outbox guards. Edges between own cards are untouched.
- 1084: a product-name marker in a title no longer pins a card local when the card names Linux/Ubuntu and nothing Apple- or device-bound (Xcode, simulator, signing, TestFlight, iOS, Swift, Mac/macOS, Keychain/TCC...). A replaced `local_markers` list keeps full control.
- 1110: local deletes of `task_chat_links` rows are captured (delete op + tombstone); reconcile deletes placement-attribution links instead of releasing them; `sync_board` queues a Board delete for this gateway's placement links the live file already lost. Real chat links missing from live stay a reported mismatch.
- 1510: a picked-up foreign card gets back this gateway's own chat links. Evicting a foreign copy removed its `task_chat_links` without tombstones. The later pickup left them out, and the feed cursor had already consumed their history, so the chat that shepherded the card lost its link for good. `repair_partition_chat_links` runs each round, right after `repair_partition_links` in `sync_board`. From the freshly pulled replica, it inserts every link that meets all three conditions: its `chat_gateway` is this gateway, its card is a foreign card (`created_on` another gateway) present in the live file, and its uid is missing from live. It is applied under `applying = 1`, so no upload is echoed and the card and claim are not touched. It does nothing in these cases:
  - the board is not `primary`, or the replica's cursor is behind live's (the live pull may have consumed a newer delete);
  - the link or the card has a tombstone (live or replica) or a `pending`/`rejected` outbox op;
  - the session is a placement placeholder, or the card is this gateway's own or is missing.

  Other gateways' chats stay only in the replica.

## How it works

The sync capture triggers and partition repair run each round; applying under `applying` suppresses echo.

## Code

`hermes_cli/kanban_sync_partition_repair.py`, `hermes_cli/kanban_worker_node.py`, `hermes_cli/kanban_placement.py`, `hermes_cli/kanban_chat_links.py`, `hermes_cli/kanban_db_sync.py`, `hermes_cli/kanban_sync_client.py`.

## Tests

`tests/hermes_cli/test_kanban_sync_copy_links.py` (1082, includes a missing own-card edge staying a mismatch), `tests/hermes_cli/test_kanban_placement.py` (1084: the observed title goes to the hub; Apple/device counterexamples stay local), `tests/hermes_cli/test_kanban_chat_links.py` and `test_kanban_sync_placeholder_links.py` (1110).

1510 adds `tests/hermes_cli/test_kanban_sync_copy_chat_links.py`, with three tests:

- `test_round_recovers_own_chat_links_consumed_before_foreign_pickup` runs a full `sync_board` round.
- `test_repair_preserves_scope_edits_deletions_and_audits` is parametrized over barriers: a placeholder session, another gateway's chat, a pending or rejected outbox op on the task or link, live and replica tombstones, an existing row, shadow mode, and an absent or own task. In each case every live sync table stays unchanged.
- `test_delete_between_replica_and_live_pulls_does_not_resurrect_link` covers a delete that lands between the replica pull and the live pull.

These tests were not run for this mirror.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

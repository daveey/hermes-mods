# Kanban sync keeps dependency and chat-link edges; placement no longer pins Linux work local

Patches:

- `patches/1082-fix-kanban-foreign-copies-keep-the-Board-s-dependenc.patch`
- `patches/1084-fix-kanban-placement-a-Baby-Bear-title-on-Linux-CI-o.patch`
- `patches/1110-fix-kanban-capture-chat-link-deletes-placement-links.patch`

Fixes to what the Board sync preserves for foreign card copies and chat links, and to where a placed card runs.

## Behavior

- 1082: evicting a foreign copy keeps an edge whose child is still in the file; a pickup imports the card's parent edges (the foreign parent is mirrored at import); `repair_partition_links` restores any replica edge from a foreign parent to a card in the file, under the same tombstone/outbox guards. Edges between own cards are untouched.
- 1084: a product-name marker in a title no longer pins a card local when the card names Linux/Ubuntu and nothing Apple- or device-bound (Xcode, simulator, signing, TestFlight, iOS, Swift, Mac/macOS, Keychain/TCC...). A replaced `local_markers` list keeps full control.
- 1110: local deletes of `task_chat_links` rows are captured (delete op + tombstone); reconcile deletes placement-attribution links instead of releasing them; `sync_board` queues a Board delete for this gateway's placement links the live file already lost. Real chat links missing from live stay a reported mismatch.

## How it works

The sync capture triggers and partition repair run each round; applying under `applying` suppresses echo.

## Code

`hermes_cli/kanban_sync_partition_repair.py`, `hermes_cli/kanban_worker_node.py`, `hermes_cli/kanban_placement.py`, `hermes_cli/kanban_chat_links.py`, `hermes_cli/kanban_db_sync.py`, `hermes_cli/kanban_sync_client.py`.

## Tests

`tests/hermes_cli/test_kanban_sync_copy_links.py` (1082, includes a missing own-card edge staying a mismatch), `tests/hermes_cli/test_kanban_placement.py` (1084: the observed title goes to the hub; Apple/device counterexamples stay local), `tests/hermes_cli/test_kanban_chat_links.py` and `test_kanban_sync_placeholder_links.py` (1110).

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

# Kanban sync client: fresh op ids, board folders, shared-card links

Patches **0468** (source `29bf90695b`) and **0469** (source `b23ad074be`).

## Behavior

- 0468: a row the central service rejected transiently (for example a link row refused "unknown table" during a
  deploy) is retried under a fresh `client_op_id`. Before, the service's `applied_ops` idempotency returned the
  remembered refusal forever. `board_paths()` keeps only valid board ids, so `boards/_archived` no longer fails every
  round with "invalid board id".
- 0469: `links-diff` keeps one replica row per (chat, card), so a card shared by several chats no longer collapses to
  the first chat. A link to a card on a board this host does not run lives in a link-only board
  (`<root>/kanban/sync-links/<board>.db`, outside `boards/`, never dispatched or listed) that holds this gateway's
  links and syncs that board's replica.

## How it works

- `hermes_cli/kanban_sync_client.py`: fresh op id on a deferred row; board-id filter in `board_paths()`;
  link-only board paths for sync.
- `hermes_cli/kanban_chat_links.py`, `hermes_cli/kanban_db_sync.py`: replica cards deduplicate per chat and card;
  `session_tasks` still dedupes by card for a lineage.

## Tests

- `tests/hermes_cli/test_kanban_sync_client.py` (0468: retry under a fresh op id).
- `tests/hermes_cli/test_kanban_chat_links.py` (0469: shared card keeps every chat; link-only board).

## Applying

`git am -3` 0468 then 0469; both extend `kanban-sync-seams.md`. Off unless `kanban.sync.enabled`.

## Gotchas

- Written from the commit messages and diffs; the 0469 link-only board path is covered only by the unit tests above
  (no live-fleet test recorded).

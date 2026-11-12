# A pulled comment keeps its sending gateway

Patches: **2579, 2580**.

## Behavior

- A comment that arrives through the Board feed now stores its sending gateway in `task_comments.origin_gateway`, so `hermes kanban wait-comment ... --from-gateway <gateway>` can match it. Before, the column is machine-local and nothing derived it on apply, so a pulled copy stored NULL and no gateway value could match a reply written by a profile-named author on another machine; a parked card slept through two such replies.
- The stored value is the author's gateway when the author names one (a relay author such as `hub:gw:<name>` or `<name>/t_...`), otherwise the gateway that wrote the row on the wire.
- It is never this gateway's own name by fallback: no sender, or a malformed sender name, leaves NULL. An existing local row is not touched, and rows already pulled with NULL stay NULL.
- A card copied from a Board replica into a worker node keeps each comment's sender, so worker-node imports carry it too.
- 2580 is test-only: no runtime behavior change. The "feed does not overwrite a local origin" test now reaches the update path (plain author, acknowledged outbox row), so a mutant that overwrites the origin fails.

## How it works

- 2579: new `synced_origin(author, writer)` in `kanban_comment_origin.py` (the author's gateway, else the validated writer, else nothing; unlike the local resolver it has no local-gateway fallback). `_upsert_row` in `kanban_db_sync.py` calls it for a newly inserted `task_comments` row when the column exists. `_changes_for` in `kanban_worker_node.py` passes each comment's `origin_gateway` from the replica as the change's sender.
- 2580: edits the test only.

## Code

`hermes_cli/kanban_comment_origin.py`, `hermes_cli/kanban_db_sync.py`, `hermes_cli/kanban_worker_node.py`, `tests/hermes_cli/test_kanban_comment_origin.py` (2580).

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_comment_origin.py`. Entry path: the module API `kanban_db_sync.apply_remote_changes` with a synthetic comment change, then the `wait-comment --from-gateway` CLI handler on the stored value; and `kanban_worker_node.import_cards` from a seeded replica. Covered: a profile-named author written elsewhere, a relay author naming the real sender, no sender (NULL), a malformed sender (NULL), a local origin not overwritten, and the worker-node import. A real Board feed pull end to end is **untested**.

Per entry: 2579 adds the feed-origin cases; 2580 only hardens the no-overwrite test.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Rows pulled before this patch keep NULL; there is no backfill.
- Only inserts derive the origin; a later update of an existing row leaves the column as it was.

## Plugin

Core change; no plugin required.

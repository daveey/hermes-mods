# Remembered rows for removed versus offline owners

Mirror snapshot 5011; ordinals 4873, 4874.

Entries:

- **4873**: Remembered rows: retire a removed worker's copy of another gateway's card; close a card that left a live owner's scope
- **4874**: Remembered rows: tell a removed owner from an offline one, and use bulk publication lookups only

## Behavior

- A removed owner's published copy of a card another gateway still publishes is retired.
- A live owner's closed card that left its publication window is released as aged-out; an owner back without a clean fresh read of that scope takes the release back.
- **Removed** means: no `gateway/info` row (`known_gateways`, read every call, never memoized), no task record in the projection's per-gateway seen set, and no fresh scope heartbeat. Evidence without `known_gateways` counts nothing as removed.
- An owner with a stale info row and no records is offline, not removed; its copy is not retired.
- Lookups use bulk publication maps only; a malformed `created_at` proves nothing and does not raise.

## How it works

- `hermes_cli/kanban_replica_aggregates.py` (`_release_removed_owners`, `known_gateways`) plus session-activity records and scope heartbeats.
- Publication presence for “another gateway publishes it” and “nobody publishes it” uses the one scan `remembered_publications` already resolves.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_replica_aggregates.py`
- `hermes_cli/kanban_scope_heartbeat.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_session_activity_records.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_kanban_release_removed_owner_rows.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 5011. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported.

## Gotchas

- Do not treat a silent owner as removed. Without the list of known gateways, nothing counts as removed.

## Plugin?

None.

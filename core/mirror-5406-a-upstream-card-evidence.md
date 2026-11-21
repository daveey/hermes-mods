# Local-authoritative upstream evidence on session cards

Mirror entries: 5059, 5060, 5097, 5098, 5099, 5100, 5101, 5102, 5103.

## Behavior

- Session-linked cards carry nearest blocked/triage ancestry through owner inventory, origin-ledger and replica projections. Contained ancestors include episode/authority detail; a nearest open non-blocked ancestor can be shown as `upstream`.
- Local task membership owns the complete local parent set, even an empty set after unlink; stale replicas cannot restore removed edges or override local parent status.
- Foreign ancestry can resolve from a sync-shadow replica. Root lookups are batched, with deeper traversal cycle/depth bounded and old schemas tolerated.

## How it works

Owner publication enriches ancestry before hashing its payload. Optional stored replica stamps take precedence when eligible; done/archived/blocked/triage cards do not receive a blocked-upstream stamp. Projection helpers preserve optional evidence without requiring a new synced lifecycle column.

The shared walk reads local tasks/links first and falls back to replica-only ancestors. Owned replica connections are tracked separately (no dynamic attributes on SQLite connections), opened read-only through board lockguard with quoted file URIs, and closed by their owner. Explicit task projections work with tuple and `sqlite3.Row` factories without mutating caller connection policy. Remote enrichment receives only newly appended remote rows. Root membership and parent edges are fetched in batches to avoid per-listed-card queries.

## Code

Paths below refer to the mirrored core source tree, not standalone plugin files.

- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_origin_ledger.py`
- `hermes_cli/kanban_replica_projection.py`
- `hermes_cli/kanban_replica_upstream.py`
- `hermes_cli/kanban_session_activity_card_details.py`
- `hermes_cli/kanban_session_outcome_tasks.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

Remote tests cover owner publication before hashing, origin/hub/replica projection, optional stamp precedence, eligibility, legacy graphless schemas and tuple/Row factories. Detail tests cover contained authority/episode, open upstream, foreign ancestors, missing replicas, stale status and locally removed edges. Execution-observation tests assert constant root-query behavior for many rows with and without legacy link tables. Live Desktop chips and actual cross-host inventory rendering are untested here.

Source test/coverage paths:

- `tests/hermes_cli/test_kanban_blocked_upstream_remote.py`
- `tests/hermes_cli/test_kanban_blocked_upstream_details.py`
- `tests/hermes_cli/test_kanban_execution_observations.py`

## Applying

Apply these entries only as part of the **whole ordered 5406 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches, execute source tests or activate runtime behavior.

## Gotchas

Presentation evidence does not change lifecycle or confer release authority. Missing replica data fails soft. A local empty edge set is authoritative, not permission to consult stale remote links. Batching roots does not imply constant work for arbitrarily deep ancestry.

## Why core instead of a plugin

These fields must be computed consistently across core owner hashes, SQLite readers and transport projections. A Desktop plugin alone cannot fix stale-edge precedence or server-side query scaling.

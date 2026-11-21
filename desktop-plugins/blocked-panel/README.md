# Blocked panel: request evidence and gateway routing

The shared snapshot consumed by the panel and user-steps separates explicit human
requests from agent-owned operational blockers. Complete canonical
`task.human_request` evidence wins over event history; explicit null or malformed
evidence cannot resurrect an older ask. Older owners without that field retain
the event compatibility path. This classification never authorizes execution.

## Cross-gateway path

- The focused session's owner/profile is used for compression-lineage reads when
  the SDK exposes it. An ambiguous owner is not queried on the active gateway.
- `/api/session-kanban-tasks` is read on each registered connection. Hub-sync
  `/session-links` also discovers hub-platform subscriptions and offline owners.
- SDK gateway-name mapping resolves replica owners to connection IDs. List,
  detail cache, rewrite cache, deduplication, panel React keys, and snapshot rows
  preserve connection + board + task identity.
- Details, reply comments, unblock PATCHes, and rewrite requests use that owner.
  The core drawer hook receives its third, owner-connection argument. An unknown
  owner cannot fall through to active-gateway writes or a legacy local popup.
- Profile qualification: core's local `/api/session-kanban-tasks` rows carry no
  profile, gateway or connection. The focused owner's connection is read through
  the owner profile (`ownerRoute()`, the same rule as session-tasks'
  `focusedRoute`), and only those untagged rows inherit that profile
  (`withOwnerRoute`, mirroring session-tasks' `withLocalRoute`). Rows naming their
  own owner (replica `gateway`, explicit `profile`, `profile: null`,
  `ownership_state: "unknown"`) and rows read on any other connection keep what
  they have, so user-steps' exact `sameCard` fails open (shows both) rather than
  merging. Without this, the session panel's chip and the strip's legacy card for
  one card never matched and the card rendered twice.
- Replica discovery is not actionable evidence: an unavailable owner remains a
  visible operational item with its assignee. Live owner status overrides stale
  replica status. Failed detail refresh discards expired evidence.

The external hermes-hub producer currently drops `human_request` before it reaches
hub-sync. See `plugins/hub-sync/tests/test_human_request_path.py`: this is verified
against real `collect_tasks` + `stage` serialization, not merely hand-built replica
fixtures. That producer must be fixed separately for replica-only core counts to
match live owner classification. No live deployment is established by these tests.

## Shared reads

blocked-panel polls every 10 s, but most of what it needs is already held by its neighbours, so it takes it from
them while it is current and reads only what is missing:

- **Lineage**: session-tasks' `lineageIds` (its cache, kept across launches and filled by the gateway feed). An
  incomplete ancestry, or no session-tasks, walks it here as before.
- **The owner gateway's card list**: session-tasks' `kanbanRows` (the raw `/api/session-kanban-tasks` rows of its own
  read of the same route and lineage, within 15 s, or of a feed-kept chat). They get the same owner-route
  qualification as a read here (`withOwnerRoute`). Other registered gateways and hub `session-links` are still read
  here: session-tasks does not hold them.
- **A blocked card's detail**: session-panel's card cache (`window.__sessionPanel.cachedCard`, which never starts a
  read) when the gateway feed pushed the card's detail, for the same connection, profile, board and id, not stale or
  pending, and either the feed vouches for the chat (`feedVouches`) or it was confirmed within the 60 s detail TTL.
  A pushed detail has no comments, so the step rewrite reads the card once (as before) for its comments.
- A forced `refresh(true)` reads everything itself.

Tests: `test/shared-caches.test.mjs` (current data: no lineage, list or detail reads, steady state; identical
snapshot to a live read; stale/missing data read here; no neighbours; forced refresh; the detail checks) and
`../session-panel/test/producer-composed.test.mjs` (real session-tasks feeding blocked-panel: the owner list is not
read again, the other gateway still is, identical qualified items).

## Tests

From the mods root:

```
node --test --test-reporter=tap desktop-plugins/blocked-panel/test/*.test.mjs desktop-plugins/user-steps/test/*.test.mjs
```

`cross-gateway.test.mjs` runs the actual refresh/register/snapshot/action path with
an injected Desktop bridge: colliding IDs, remote focused-session profile, replica
discovery, owner details, comment-before-unblock routing, drawer ownership, unknown
owners, canonical evidence parity, and stale replica status. No live writes occur.

`desktop-plugins/session-panel/test/producer-composed.test.mjs` registers
session-tasks, blocked-panel, user-steps and session-panel together against a
bridge answering with core's real row shapes (no profile on local rows) and
asserts the rendered DOM: the exact same card once, a separate canonical request
tile, gateway/board/profile collisions kept, and legacy cards with Open card only.

Downstream consumers must also use connection-qualified React/deduplication keys;
receiving a qualified snapshot alone does not fix a consumer's own unqualified key.

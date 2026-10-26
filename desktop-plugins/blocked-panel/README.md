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
- Replica discovery is not actionable evidence: an unavailable owner remains a
  visible operational item with its assignee. Live owner status overrides stale
  replica status. Failed detail refresh discards expired evidence.

The external hermes-hub producer currently drops `human_request` before it reaches
hub-sync. See `plugins/hub-sync/tests/test_human_request_path.py`: this is verified
against real `collect_tasks` + `stage` serialization, not merely hand-built replica
fixtures. That producer must be fixed separately for replica-only core counts to
match live owner classification. No live deployment is established by these tests.

## Tests

From the mods root:

```
node --test --test-reporter=tap desktop-plugins/blocked-panel/test/*.test.mjs desktop-plugins/user-steps/test/*.test.mjs
```

`cross-gateway.test.mjs` runs the actual refresh/register/snapshot/action path with
an injected Desktop bridge: colliding IDs, remote focused-session profile, replica
discovery, owner details, comment-before-unblock routing, drawer ownership, unknown
owners, canonical evidence parity, and stale replica status. No live writes occur.

Downstream consumers must also use connection-qualified React/deduplication keys;
receiving a qualified snapshot alone does not fix a consumer's own unqualified key.

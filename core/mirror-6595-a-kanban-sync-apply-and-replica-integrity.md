# Kanban sync: bounded feed apply, replica write guards and snapshot rebuild

Patches: 5972, 5987, 5997, 6000, 6001, 6002, 6015, 6016, 6018, 6086, 6103, 6104.

## Behavior

- Eligible default apply calls prepare and publish a page-writer schema before BEGIN IMMEDIATE (one-row JSON column reads, cached ordered-column lookups, containment-guard schemas read together). Attached databases, shadowing temp tables and custom keep callbacks retain the uncached path; a changed schema version is re-read under the writer.
- Feed pages apply in prefixes of at most 32 rows under a 250 ms monotonic budget, with writer gaps between prefixes (including across SQLite busy retries); over-budget batches roll back and halve, a single oversized row fails closed. Pending pushes are offered between prefixes.
- Dedicated replica files no longer run the legacy executor-run backfill. Installed replica guards ignore outbox inserts and stray tombstones; before each pull, a bounded recovery pass attempts to undo previously captured operations from fresh Board row evidence. Unreadable or quarantined rows remain for later rounds rather than blocking the feed.
- Snapshot rebuild re-reads the task snapshot after replaying containment history and re-applies rows still at the written version (in 500-value chunks, comparing Board-set columns too), so finished cards are not left ready.
- The outbox-age sampler skips opening the board when every pending op is Board-deferred.

## How it works

kanban_db_sync, kanban_sync_apply_budget and kanban_sync_apply_schema implement the bounded apply; kanban_db_connect installs replica guards; kanban_sync_snapshot_tasks.finish_rebuild holds the rebuild tail; kanban_sync_outbox_age / replica_age short-circuit on deferred rows. A design note covers the writer budget.

## Code

Paths touched by these entries (non-test):

- `docs/designs/kanban-feed-writer-budget.md`
- `hermes_cli/kanban_containment_guard.py`
- `hermes_cli/kanban_containment_replica.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_sync_apply_budget.py`
- `hermes_cli/kanban_sync_apply_schema.py`
- `hermes_cli/kanban_sync_client.py`
- `hermes_cli/kanban_sync_outbox_age.py`
- `hermes_cli/kanban_sync_pending.py`
- `hermes_cli/kanban_sync_replica_age.py`
- `hermes_cli/kanban_sync_round_send.py`
- `hermes_cli/kanban_sync_snapshot_tasks.py`
- `hermes_cli/kanban_sync_status_origin.py`

## Tests

Entry-path behavior above is source-reviewed but **untested in this review**. The files below identify source regression targets, not proof that this review exercised those entrypoints.

Source regression coverage named in these commits (inspected from the source repository; **source coverage was NOT executed in this mirror run**):

- `tests/hermes_cli/test_kanban_containment_feed_predicates.py`
- `tests/hermes_cli/test_kanban_sync_apply_budget.py`
- `tests/hermes_cli/test_kanban_sync_apply_schema_cache.py`
- `tests/hermes_cli/test_kanban_sync_apply_schema_reads.py`
- `tests/hermes_cli/test_kanban_sync_client.py`
- `tests/hermes_cli/test_kanban_sync_copy_chat_links.py`
- `tests/hermes_cli/test_kanban_sync_empty_outbox.py`
- `tests/hermes_cli/test_kanban_sync_outbox_deferred.py`
- `tests/hermes_cli/test_kanban_sync_partition_content.py`
- `tests/hermes_cli/test_kanban_sync_replica_outbox.py`
- `tests/hermes_cli/test_kanban_sync_shadow_backfill.py`
- `tests/hermes_cli/test_kanban_sync_snapshot_tasks.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Apply budgets use Python checkpoints and a SQLite progress handler every 1,000 VM opcodes. A long statement can be interrupted, but an individual opcode, fsync and OS scheduling are not preemptible wall-clock guarantees.
- Pre-guard contamination is recovered only for rows linked to retained outbox operations whose Board evidence is readable and whose restore passes quarantine (at most 64 evidence reads per round; three read failures stop further evidence reads). This is not an unconditional repair of every divergent row.
- A cache fixture must fail both boot-id read paths to be platform independent.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes the Kanban sync client and board connection layer inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.

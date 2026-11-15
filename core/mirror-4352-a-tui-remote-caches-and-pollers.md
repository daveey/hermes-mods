# Desktop remote caches: bystander rows, reader deferral and replay budgets

Mirror snapshot 4352; ordinals 4146, 4158, 4209, 4219, 4220, 4223.

Entries:

- **4146**: test non-finite original/short retry deadlines on different code
- **4158**: Test released-root compaction in watch_store.release_under
- **4209**: tui remote caches: bystander replica rows keep no cards
- **4219**: Defer remote-index reader misses to a live, beating holder
- **4220**: J non-RPC complete-row replays; single-trial gate benches
- **4223**: observation compact lane: a renamed gateway re-classifies every replica row

## Behavior

- **Bystander replica rows keep no cards** (4209): the replica of other gateways' tasks is large, and the serve process retained decoded projections of every row. A row is now held compact (state plus sequence, a few hundred bytes) only when it is a bystander per `kanban_replica_relevance.is_bystander`, `_linked_chats(task, me)` is empty (including withdrawal recipients), its seq is an int, its owner has no `/` and it has a status; any doubt keeps the full form. Terminal states of compact rows are still written to the ledger in the same transaction, and stale-claim settling re-reads compact rows with a seq-checked point read. 4223: after an in-place gateway rename, the compact lane records the gateway it classified for (`Lane.me`) and re-classifies every row, so a row now linked to the new name is observed.
- **Reader deferral** (4219): a notification poll landing between a replica commit and the holder's republish used to rebuild the whole remote index itself. For the host's own replica and root-home ledger, a miss with a change key now returns no remote cards when a live holder of the `kanban-remote-index` lease beat within 5 s, for at most 4 s from a chat's first deferred miss (so a deadline poll never defers twice); any doubt (no lease, released, dead or stale, future-dated heartbeat, unreadable lease dir, profile home, explicit paths, no key) computes at once. Misses are no longer memoized.
- **Test-only** (4146, 4158, 4220): non-finite original/short retry deadlines on different code in `notice_retry.effective_due`; compaction of `_released_roots` in `watch_store.release_under` (ancestor release drops descendants; release under a released ancestor adds nothing); and whole-row replay budget benches for the serve change watcher, pollers, producer and PID memo, each run once with unchanged thresholds (the minutes-long cells run only with `J_ROWS_RUN=1`).

## How it works

- `tui_gateway/remote_sweep_projection.py` (`StatesView`, compact rows `"seq\0state"`), `kanban_remote_observation_compact.py` (per-flight sorted `array('q')` columns of hash/seq/state-hash, lane table), `kanban_remote_observations.py`, `kanban_remote_notices.py`, `kanban_remote_index.py` (deferral, `DEFER_MAX_S`).
- The J-row benches live under `tests/tui_gateway/` with `j_nonrpc_scheduler.py` as a helper.

## Code

Shipped non-test paths (union over these entries):

- `tui_gateway/kanban_remote_index.py`
- `tui_gateway/kanban_remote_notices.py`
- `tui_gateway/kanban_remote_observation_compact.py`
- `tui_gateway/kanban_remote_observations.py`
- `tui_gateway/remote_sweep_projection.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/gateway/test_status_pid_memo.py`
- `tests/tui_gateway/j_nonrpc_scheduler.py`
- `tests/tui_gateway/test_j_nonrpc_producer_pid_rows.py`
- `tests/tui_gateway/test_j_nonrpc_rows.py`
- `tests/tui_gateway/test_kanban_remote_index_defer.py`
- `tests/tui_gateway/test_notice_retry_successor.py`
- `tests/tui_gateway/test_notify_subs_gate.py`
- `tests/tui_gateway/test_producer_admission_gate.py`
- `tests/tui_gateway/test_remote_projection_bystander_memory.py`
- `tests/tui_gateway/test_watch_store_gate.py`
- `tests/tui_gateway/test_watch_store_release_compaction.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- Compact lane is turned off by a second ledger path and re-read when the ledger file is replaced.
- Deferral can delay a notice by up to one poll when a holder beats but never publishes; the 4 s bound limits that.
- The CPU budgets in the replay tests are machine-dependent measurements.

## Plugin?

None.

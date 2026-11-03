# Cross-pass structural reads

## Scope and dependency trace

`_wake_tick` builds `_pass_reader()` each interval. That reader batches pending ledger IDs and memoizes
local/remote merged cards only **within** that pass. Its `healthy()` result is also pass-local.
`watchdog_tick`, quiet-period calculations, absence timers, and wake admission continue to execute normally.

The cross-pass cache is deliberately below that layer, and only on Kanban board and sync-shadow files:

- `core._board_rows`: SQL over one board's tasks, schema, events, runs and parent links. Key: database path
  and identity, retained observer's committed `PRAGMA data_version`, full/light mode, sorted bare IDs.
  Board naming, path discovery and cross-board ambiguity are still evaluated by `core.read_cards` each time.
- `core.unseen_counts` (used by `_unseen_counts`): per local board, `tui` notify subscriptions whose cursor
  trails a terminal event, grouped by chat. Same SQL and event kinds (`ksa.UNSEEN_KINDS`) and the same board
  list (`ksa._board_db_paths()`) as the `unseen` map of core `_all_counts`, which is built only from local
  boards. Key: board identity, observer revision, kinds. Summed afresh on every call.
- `core.shadow_rows` (used by `_read_shadow`): a shadow's `kanban_sync_state` rows and the requested IDs present
  in its `tasks`, read in one snapshot. Key: shadow identity, observer revision, sorted IDs. The 120 s
  freshness rule is judged from those rows with `time.time()` on **every** call, never cached.

`core.last_user_at` (state.db) is **not** cached and keeps no connection: it uses the pass's `ReadPool`
(closed when the pass ends) or a connection closed before it returns. Quiet-period decisions use the
current clock. Remote projection, hub freshness, replica completion lookup and ledger/proof reads are not
cached either. A missing, unreadable or stale shadow still vetoes absence on the next pass.

### Why `_unseen_counts` no longer calls `_all_counts`

The native Mac replay (`NATIVE_REPLAY_REPORT.md` in the task workspace) measured `_unseen_counts ->
ksa._all_counts` at 58% of the closeout callback, mostly its remote-links refresh after the 3 s TTL, and
`_read_shadow` at 92 ms of 94 ms in `_board_absence_veto`. Closeout uses only `_all_counts()[2]`. Errors
differ deliberately in one direction: core counts nothing when a board's unseen query fails; closeout now
treats a failure other than a missing table/column as unreadable (`None`: no wake this pass, retried next
pass). A board that cannot be opened was already `None`. Cores without `UNSEEN_KINDS`/`_board_db_paths`
keep the old `_all_counts` read.

## Cache contract

`_StructuralReads` owns one process-wide pool of read-only/autocommit observers shared by every thread, so
descriptors stay bounded however many threads read. SQLite `data_version` is compared only on the same
connection; replacing or dropping an observer also discards its results. Device/inode checks reject path
replacement and deletion. A read is labeled with its **pre-read** revision, and cached only if file identity,
`-shm` identity and revision remain unchanged afterward. Errors discard the observer and retry next time.
No transaction spans intervals. The LRU bounds are 48 databases per process (local boards plus shadows) and
128 query results per database; eviction explicitly closes the observer. Past the bound a read is answered
from a one-shot connection, uncached. Each connection is used and closed only under its own lock.

WAL generations: a retained handle on a database whose `-wal`/`-shm` an external process unlinked would keep
the deleted generation mapped, read a frozen `data_version`, and make core `kanban_generation.recover()`
refuse every board connect in the process. So every observer opens through core's guarded, registered reader
(`kanban_generation.open_ro`); `evict` is registered with `register_reader_cache`, so recover closes them
before any other connect (a borrowed observer raises `DeletedWalGenerationError` instead of waiting, and the
caller retries; `evict` unpublishes under the pool lock and closes only after releasing it, because closing
a board connection takes that board's lifecycle lock and core runs the hook both with and without it held:
`test_evict_never_closes_under_pool_lock_against_lifecycle_lock`); and every read compares the recorded `-shm` identity with the live one and reopens on a
change. Cores without the registry (or a partial `hermes_cli`) fall back to the plain open plus that check.
Core's own counts read the same boards and shadows through retained registered readers (`cached_ro`).

state.db has no such registration with SessionDB's fence, which is why it is never observed: the reviewer
probe showed a retained plain handle making the next `SessionDB()` in the process fail with
`DeletedWalGenerationError` after a stray close and a foreign last close removed the sidecars.

Returned card dictionaries, parent lists, unseen maps and shadow ID sets are constructed afresh per call.

## Unchanged ticks (pass union read and gate validation)

Native replay V3 still spent most of a steady 30 s tick on two `read_cards` (wake and watchdog each build a pass
reader) and three gate validations (wake, owner wake, watchdog), all for unchanged boards and replica.

- `_gated_inventory` first tries `_gate_fast`: the memo last validated by `_gate_hit` or stored by `_gate_project`
  is returned (restamped) without re-reading its inputs while `_gate_inputs` is unchanged: `_origin_scope()`, the hub
  replica's stat (main + `-wal`: the key core's own `_hub_content_signature` memo uses; it also covers `last_ok`),
  every core board's and the origin ledger's committed revision (`core.revision_token`, retained observers), the
  ledger stat, and state.db's stat only when the memo resolves alias tips (`memo["sessions"]`; never opened). The
  replica verdicts are re-judged at the current time from the recorded meta and no owner-window cut-off may be
  crossed, exactly as `_gate_hit` does. Keys are read before validating/projecting and recorded only if unchanged
  after; any unreadable key (None) takes no shortcut. An outer `inputs_pin` disables it.
- `read_cards(..., reuse=True)` (only a pass reader's union read): a successful read is kept with its content key
  (ids, mode, board list, each board's revision read **before** the read) and the very inventory row objects it
  merged. It is returned again (fresh dict copies) only while the key is unchanged, `inventory()` hands back the
  same row objects in the same order (the gate's unchanged projection; core's own re-projection yields new objects
  and is read afresh), and, if the hub replica supplied a completion time, the replica stat is unchanged. A read
  with a failed board, a failed inventory read or a failed replica completion read is never kept. Absence coverage
  (`_board_absence_veto`, shadow freshness at the current time) is recomputed on every reuse; `healthy()` and the
  hub-freshness check still run per pass.
- Not covered: state.db reads (`last_user_at`, proofs) still run every pass; within one pass each connection's
  `messages` columns are read once (`ReadPool.cols`, closed with the pass). The watchdog reads the ledger's pending
  rows once per tick instead of once per check.

Hub microbenchmark (copied hub state, 40 ticks, not native acceptance): see the task report.

## Verification

Run from the repository with an isolated HOME/HERMES_HOME, no inherited Kanban DB pins, and explicit core:

```
python scripts/test-all -k chat-closeout --hermes-core /path/to/hermes-agent
```

`tests/test_idle_reads.py` covers repeated board queries, WAL and rollback commits, PASSIVE checkpoint reuse,
query-error retry without a commit, database replacement/deletion/recreation, a commit during a read,
observer/query bounds and close-on-eviction, session/database/filter isolation, last-user reads on every pass
with nothing retained, quiet-period expiry, and full/light pass shadow expiry/disappearance.
`tests/test_idle_reads_generation.py` covers disposable sidecar loss followed by a core board connect, polled
observers reopening after sidecar loss, recovery refusing a borrowed observer without waiting then succeeding,
the older-core `-shm` fallback, and the process-wide observer bound across threads.
`tests/test_idle_hot_paths.py` covers: no state.db descriptor after `last_user_at` (with and without a pass
pool), every call re-reading, unreadable state.db skipping, and the reviewer's SessionDB-after-sidecar-loss
probe; unseen counts against the subscription rule, summed boards, reuse with every commit seen, missing
tables, unreadable boards and uncached failures, `_all_counts` never called, the older-core fallback, and
equality with core `_all_counts()[2]` on a real core board; shadow reuse in WAL and rollback modes, commits,
the clock re-judged on a reused read, replacement/corruption/deletion, uncached errors, a commit during the
read, no open transaction, and shadow sidecar loss followed by a core connect.
Existing light-read, acknowledgement, missing-card, unlink, relay and watchdog suites remain in the gate.

RED evidence before implementation:

- Board test: a PASSIVE checkpoint without a new commit caused a second SELECT, in both full and light modes.
- state.db: `last_user_at` left three descriptors open on state.db; the SessionDB probe exited 1 with
  `DeletedWalGenerationError`.
- Shadow: eight unchanged calls ran eight `SELECT id FROM tasks`; unseen: `core.unseen_counts` was absent.

## Limits

This is structural query-count evidence plus a synthetic hub microbenchmark, not native acceptance; the native
Mac replay must be rerun to claim a CPU reduction. Remote inventory/merge, hub health, lineage/proofs, state.db
reads, `parked_waits` and clock-sensitive orchestration still run. A retained observer detects committed
SQLite changes, not arbitrary unsupported in-place replacement of a live database. The race tests commit
between the pre-read revision and the read; they do not suspend SQLite inside the WAL frame-publication gap.

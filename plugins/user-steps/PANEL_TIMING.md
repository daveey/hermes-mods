# Reconciliation stage diagnostics

The existing session-panel `producer_readers` entries carry `stage_timing` for
canonical panel reconciliation. This is diagnostics only: source selection,
reader mode, freshness/unknown fences, memo keys and retry/ceiling policy are
unchanged. Nothing writes a separate trace sink or logs request/task payloads,
SQL/arguments, filesystem paths, bindings or error text. Correlation IDs contain
only process ID and a monotonic process-local call counter.

`wall_ns` and `cpu_ns` are monotonic elapsed and **thread** CPU for the user-steps
call. `stages` aggregate exclusive time and invocation counts by static code
label, with at most 64 labels plus overflow and residual. Nested child durations
are subtracted from their parent. For each clock, sum of stages (including the
explicit `residual`) equals the total exactly. Residual includes uninstrumented
work and instrumentation overhead. Stages remain available on deferred and
exceptional calls; a missing diagnostic from an older adapter stays null.

CoreReaders additionally records `preflight_{wall,cpu}_ns` (home/lineage lookup),
`reader_{wall,cpu}_ns` and `reader_residual_{wall,cpu}_ns`. Preflight plus the
user-steps total plus reader residual equals the reader total exactly. This
avoids attributing lineage work to reconciliation or silently omitting it.

Key labels:

- `owners`, `store_init`: session ownership and ActionStore/Mirror initialization.
- `remote_stamp`, `membership`, `action_stamp_read`, `action_stamp_hash`:
  composed membership/stamp work, with nested work excluded.
- `state_sqlite`, `ledger_sqlite`, `replica_sqlite`, `shadow_sqlite`: explicit
  open/execute/fetch/close boundaries in the read-only membership capture.
- `inventory_scan`: complete streaming inventory read, hashing/cache lookup,
  decoding and membership selection. Every current publication is still read
  and hashed, including those irrelevant to the current chat.
- `inventory_hash_sample`, `inventory_decode_sample`, `inventory_lock_sample`
  and `*_sqlite_row_sample`: **systematic 1/64 row probes**, identified by
  `row_sample_stride=64`. First row is sampled, then every 64th. These are raw
  sample durations, never extrapolated totals. Unsampled work remains in the
  enclosing stage (inventory_scan for the large replica stream). Samples can
  miss rare contention/decode events; absence is not proof of absence. SQL
  execution and explicit fetchone/fetchall calls are measured in full.
- `remote_reconcile`, `remote_memo_lock`, `local_reconcile`, `local_fence`,
  `local_key`, `local_full_pass`, `local_db_open`, `local_sqlite`,
  `reader_pool_lock`, `reader_entry_lock`: remote/local producer boundaries and
  explicit lock acquisition. Lock labels measure acquisition, not lock holding.

Wall minus thread CPU means **off-CPU**, which may be scheduling, GIL, filesystem
I/O or lock/SQLite waiting. It does not alone prove SQLite lock wait. Explicit
lock-acquisition spans localize a lock wait; SQL spans localize SQL-boundary
latency without pretending to separate SQLite's internal CPU and wait mechanisms.

The 27,000-publication regression exercises a warmed successful composed memo,
checks unchanged/fail-closed semantics, bounds extra traced CPU to 50 ms and
prints raw paired CPU measurements. It is an offline overhead control, **not**
production latency acceptance. Dedicated tests also check exact nested totals,
exception/context cleanup, bounded storage, source privacy, read equivalence,
and real retained-reader lock contention. Natural live diagnostics require a
normally activated, routed process; never edit live code, attach a debugger,
change the links reader, manufacture writes or restart a busy backend for proof.

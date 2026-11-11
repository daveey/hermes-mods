# loop-watch request statistics

The stall and slow-request reports are diagnostics, not a request census. Their
existing thresholds and report caps are unchanged.

## Bounded journal retention

All stall, CPU, slow-request and stats writers share a cross-process sidecar lock
around rotation and append. `loop-watch.jsonl` is capped at **16 MiB**, with three
numbered generations (`.1` newest through `.3` oldest); each append opens the current
file afresh. Stats still flush and fsync, and propagate write failures for retry.
Records larger than the cap fail without altering retained data. Diagnostics remain
best-effort. A legacy oversized log becomes `.1` on its first new write, so archive
it before rollout if that history must be retained; the bound applies to newly
written generations, not a pre-existing oversized archive.

Readers seeking historical statistics must read all retained generations and dedupe
record IDs. Rotation bounds retention, not a time interval: missing coverage outside
those files is unknown, never zero requests. The lock file is permanent and must not
be removed while writers run. Tests: `tests_rotation.py` covers both sinks, stats error
propagation, generation bounds, oversized records and concurrent process integrity.

## Restart-resilient deltas (schema 2)

Production installation starts one independent `loop-watch-stats` daemon per
installed tracer. It flushes every **5 seconds**, even with no requests or a
stalled asyncio loop. Hour changes also flush. ASGI lifespan exit, watcher exit,
and Python `atexit` flush the tail. Uvicorn's `Server.shutdown` is wrapped once,
closing the tracer on that server's `config.app` after the original drained
shutdown. Bounded traversal resolves `.app`, `__wrapped__`, the gateway mount's
`.web.app`, and a function closure's named `app` cell. This covers late
installation after lifespan has started and Uvicorn's SIGTERM re-raise (which
bypasses `atexit`); unrelated servers are unaffected. A request draining after
shutdown schedules an independent flush. Repeated flush/close calls do not
recount requests. Recording only takes a short data lock: serialization, sink
writes and fsync hold a separate flush lock, never the request's data lock.

`request_stats` retains `hour`, `pid`, `slow_s` and `paths`. New fields:

- `schema: 2`, `semantics: delta`: sum counters, **never replace a prior hour**.
- `producer_id`: random tracer-lifetime identity (PID alone can be reused).
- `record_id`: producer plus increasing sequence. **Deduplicate this ID** before
  aggregating. An ambiguous sink failure retries the identical record/ID.
- `hour_epoch`: UTC hour start in Unix seconds. `hour` is now UTC, not local time.
- `reason`: periodic/manual/shutdown/start (hour rollover wakes the periodic worker).
- Each path keeps exact `n`, `slow`, `max_s`, `max_inflight`. Every bounded path
  counter is emitted; the old top-40 output cap no longer discards counts.
- `p50_s`, `p95_s` describe the **first at most 5000 durations in this delta**;
  `duration_samples` gives their sample size. Do not average these percentiles or
  call their maximum a daily percentile. Query lists remain truncated diagnostic
  summaries, not complete query counts.

A request is counted once when the wrapped ASGI invocation finishes (including
exceptions/cancellation). Its latency is time to response start, or total elapsed
time if it never starts a response. The hour is its completion hour, not start
hour. In-flight requests at hard death are outside this finished-request census.

Normalize ID-like path segments; after 300 distinct paths in a buffered hour,
fold additional paths into `(other)`. Overall counts remain exact; a specific
route's count is not guaranteed complete if it could have folded into `(other)`.

## Coverage is evidence, not an assumption

`request_stats_coverage` records have the same identity fields plus `since`,
`through` (wall-clock epoch seconds), `elapsed_s` (monotonic), `interval_s`,
`reason` (`start`, `periodic`, `shutdown`), and `dropped_requests`.
A receipt is emitted **after** all buffered deltas have been acknowledged. Empty
periods emit receipts too. The default sink flushes and fsyncs each stats/coverage
record; failures propagate for retry rather than silently clearing counters.

For an acceptance window:

1. Deduplicate all v2 records by `record_id`, select UTC `hour_epoch`, and sum
   path `n`/`slow` across **all** deltas and producers. Preserve legacy records as
   separate, coverage-unknown data; legacy `hour` uses the producer's timezone.
2. For each serving process/tracer, retain its start and shutdown receipts and
   union the `since`–`through` intervals (retries may overlap). Verify the window
   is inside those intervals, all loss counters are zero, no JSON lines are
   corrupt/missing, and any log rotation preserves all files. A start receipt
   establishes the initial boundary, not coverage before installation.
3. Reconcile every serving process and downtime/startup gap. A new producer's
   receipt does not prove the previous producer shut down cleanly. An unclosed
   producer only proves coverage **through its last successful receipt**. Do not
   declare the remainder zero traffic or use an incomplete day as a full day.
4. Compare monotonic elapsed with wall-clock span to detect clock adjustments.
   A slow/missing receipt invalidates the nominal flush-cadence assumption.

Under healthy thread scheduling and storage, hard process death can discard the
current **5-second interval plus scheduling/write time** since the last receipt;
this is a time bound, not a bound on request count. GIL starvation, suspend,
stalled I/O, SIGKILL, `os._exit`, disk failure or a partial JSON append can exceed
that bound or leave an unknown tail. No unconditional real-time or power-loss
claim is made. Failed writes retain one immutable pending batch (up to two hour
records and one coverage receipt) plus at most two buffered hour buckets; further
hour eviction increments `dropped_requests`,
invalidating complete-window acceptance. A death during a storage outage cannot
report that loss retrospectively. Do not infer full coverage from counters alone.

## Tests

Run `scripts/test-all -k loop-watch`. `tests_stats_persistence.py` covers repeated
flushes, rollover, replacement, concurrent recording/flushing, all-path count
preservation, stable-ID retries, real graceful-process exit/restart, real SIGKILL,
independent timer flushing, lifespan/late-drain shutdown, bounded outage memory,
and actual sink fsync failure. Existing tests cover counts beyond slow-report
caps and unchanged slow-request tracing. Production acceptance additionally needs
real records on both sides of a safe service restart.

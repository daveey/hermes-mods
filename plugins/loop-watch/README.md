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

## Opt-in one-shot cache census

This is **off by default**. To opt in, give the ordinary gateway/serve launcher
`HERMES_LOOP_WATCH_CACHE_CENSUS=1` before its next natural start, with loop-watch
enabled. Do not restart a busy gateway, signal it, or force a plugin reload for
this diagnostic. `HERMES_LOOP_WATCH=off` disables it along with the plugin.
An already running process without the opt-in has no request listener.

On startup it creates `<HERMES_HOME>/loop-watch-census/<pid>-<random-generation>/`
(mode 0700). `ready.json` (0600) identifies the PID and schema. Select the directory
for the intended live process and its current generation, not a leftover directory.
Create a **new regular mode-0600** file named `request` in that directory containing
exactly `cache-census-v1\n` (16 bytes including newline). For example, after setting
`directory` to that verified directory:

```sh
(umask 077; printf 'cache-census-v1\n' > "$directory/request.new" &&
 mv "$directory/request.new" "$directory/request")
```

The listener polls once per second, atomically consumes the request as `consumed`,
and exits after **one attempt per process**, including rejected attempts. Symlinks,
nonregular files, hardlinks, wrong owners/modes/commands and pre-start timestamps
are rejected. No overlapping probes or repeated requests are accepted. A fresh
process has a different generation and will not read old requests. Directories
remain for owner inspection; there is no automatic cleanup or retention-policy change.

The fixed output is `result.json` (0600, atomic replacement, at most 4096 bytes).
It contains only numeric aggregate counts, booleans and fixed schema labels:

- `schema: 1`, `kind: cache_census`, `status: ok|partial|unknown`.
- `cached_entries` (when the cache is readable), `agents` (sampled entries),
  `active`, `idle`, `activity_unknown` (sampled cache entries only, not all work).
  Activity uses the matching raw gateway SessionState/TurnState; unavailable
  representations are unknown. Non-cache API/worker agents are not included.
- `buffer_lengths`: summed lengths of exact builtin `_session_messages` and
  `conversation_history` lists/tuples. Aliased fields count in both lengths.
- `containers`, `shallow_container_bytes`: identity-deduplicated exact builtin
  list/dict/tuple containers reached from those fields, at most three edges deep.
  These **exclude text payload bytes and are not retained heap size**, RSS or a
  leak attribution. `retained_bytes` is always null.
- `visited_values`, `truncated`, `deadline_exceeded`. At most 128 cache entries
  and 10000 visited values/containers are scanned, with a cooperative 100ms
  deadline. Depth/value/entry limits and concurrent mutation can yield partial
  coverage; do not extrapolate it into an exact total. This is not a hard
  real-time bound on scheduling, individual native operations or output I/O.
  A size-cap fallback sets `output_truncated: true` and `status: unknown`.

The only runner source is the already-loaded core `gateway.run` module's existing
`_gateway_runner_ref` weak reference, verified against its exact `GatewayRunner`
class. Unsupported core/serve layouts and a busy cache lock report `unknown`, not
zero retained memory. The scanner never imports the gateway, enumerates the heap,
serializes message content/keys, calls arbitrary properties/size/repr methods,
starts tracemalloc, or changes eviction, GC, trim, TTL or budget policy. The existing
stall/CPU/request tracing behavior is unchanged.

`tests_census.py` covers counts/sizing/privacy, unsafe files, stale/duplicate
requests, process-generation targeting, bounds, lock contention and a standalone
normal PluginManager discovery subprocess with opt-in both off and on (core-backed;
skipped in public core-free CI). Live deployment and memory-budget acceptance are
separate operator checks, not established by these fixtures.

## Tests

Run `scripts/test-all -k loop-watch`. `tests_stats_persistence.py` covers repeated
flushes, rollover, replacement, concurrent recording/flushing, all-path count
preservation, stable-ID retries, real graceful-process exit/restart, real SIGKILL,
independent timer flushing, lifespan/late-drain shutdown, bounded outage memory,
and actual sink fsync failure. Existing tests cover counts beyond slow-report
caps and unchanged slow-request tracing. Production acceptance additionally needs
real records on both sides of a safe service restart.

## Opt-in tracemalloc heap census

Also **off by default**, and meant for one diagnostic process lifetime only: tracing adds memory
(roughly 50-100 bytes per live block) and CPU to every allocation. Give the ordinary launcher
`HERMES_LOOP_WATCH_HEAP_CENSUS=<frames>` (an integer 1-32; anything else is off) before its next
natural start. When loop-watch registers, it calls `tracemalloc.start(<frames>)` (unless tracing is
already on) and arms the same private one-shot trigger as the cache census, under
`<HERMES_HOME>/loop-watch-heap/<pid>-<random-generation>/`, with the command `heap-census-v1\n`
(15 bytes). The request rules (fresh regular 0600 file, atomic consume, one attempt per process) are
identical to the cache census above.

`result.json` (0600, at most 64 KiB) holds `schema: 1`, `kind: heap_census`, `status: ok|unknown`,
`traced_bytes`, `peak_bytes`, `tracemalloc_overhead_bytes`, `traces`, `frames`, `gc_counts`,
`gc_collections`, `seconds`, and three top-40 lists of `[location, bytes, blocks]`:

- `by_line`: innermost frame of each live block.
- `by_app_line`: innermost frame outside the stdlib/site-packages (the code that asked for it).
- `by_app_entry`: outermost such frame within the captured `<frames>` (the entry point, if the stack fits).

Locations are `file:line` only (home shortened to `~`); no values, keys or reprs are serialized.
Allocations made before loop-watch registered (most imports) are not traced. The snapshot runs on the
census thread, holds the GIL for roughly a second per million live blocks, and briefly needs about
150 bytes of extra memory per live block (a 5M-block heap: ~750 MB). After the one report the census
stops tracing (if it started it), releasing the per-block overhead. A failing snapshot writes
`status: unknown` with the exception type. Remove the env var after the diagnostic run.

**TTL.** If no request is consumed within `HERMES_LOOP_WATCH_HEAP_CENSUS_TTL_S` seconds (default
21600 = 6 h; unset, non-numeric, non-finite or <= 0 means the default), the census thread stops
tracing (only if loop-watch started it; tracing that was already on is left alone), writes
`result.json` with `{"schema": 1, "kind": "heap_census", "status": "expired", "ttl_s", "frames",
"tracing_left_on"}` and no scan, and exits. The one request opportunity is then spent; a later
`request` file is never consumed. Send the request well inside the TTL (a 1 h warm-up fits the
default); raise the TTL in the same launcher env for a longer warm-up. When the census arms, loop-watch
logs one WARNING to agent.log naming `HERMES_LOOP_WATCH_HEAP_CENSUS=<frames>`, the pid and the TTL,
and another when it expires, so a forgotten opt-in is visible instead of only slow.

**Keep it out of shared `.env` files.** hermes-cred-relay copies `.env` keys to every gateway. A
`HERMES_LOOP_WATCH_HEAP_CENSUS` set in one host's diagnostic `.env` once reached two other gateways,
and every process started there traced for its whole life (allocation-heavy paths 5-20x
slower, Desktop polls of minutes). The relay's `env_deny` therefore holds `^HERMES_LOOP_WATCH_`; keep
it there, and set this key on the launcher of the one process you are diagnosing. The TTL is the
second guard.

`tests_heap.py` covers env parsing, default-off, attribution of a held allocation to its line, value
privacy, the one-shot trigger and output cap, the TTL (expiry stops tracing with a fake clock, a
request before the TTL still reports, pre-existing tracing is never stopped), and that the cache census
defaults are unchanged.

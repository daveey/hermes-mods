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
(roughly 50-100 bytes per live block) and CPU to every allocation. One arming needs all of:

| Key | Meaning |
|---|---|
| `HERMES_LOOP_WATCH_HEAP_CENSUS=<frames>` | integer 1-32; anything else is off |
| `HERMES_LOOP_WATCH_HEAP_CENSUS_UNTIL=<epoch seconds>` | wall-clock end of the arming window. Absent, non-numeric, in the past or more than 24 h away = **off**. Tracing also stops at this time. Set it with `$(($(date +%s) + 7200))` |
| `HERMES_LOOP_WATCH_HEAP_CENSUS_KINDS` (optional) | comma list of process kinds that may arm: `gateway` (`hermes gateway run`), `serve`, `dashboard`, `other`. Default `gateway` only: a `hermes serve`, dashboard or CLI process never arms unless named here (naming `serve` alone excludes the gateway) |

and the process must be the first of its Hermes home to arm for that UNTIL value: arming creates
`<HERMES_HOME>/loop-watch-heap/armed-<until>` exclusively, so a restart (or a second process of the
home) inside the window does not arm again. To arm once more, set a new UNTIL. A process that sees the
frames key but does not arm logs one WARNING `heap census skipped in pid …: <reason>` and traces
nothing; a key forgotten in a persistent `.env` is therefore inert once its window has passed.

Give the ordinary launcher those keys before its next
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
21600 = 6 h; unset, non-numeric, non-finite or <= 0 means the default) or by the wall-clock UNTIL,
whichever is first, the census thread stops
tracing (only if loop-watch started it; tracing that was already on is left alone), writes
`result.json` with `{"schema": 1, "kind": "heap_census", "status": "expired", "ttl_s", "until",
"frames", "tracing_left_on"}` and no scan, and exits. The one request opportunity is then spent; a later
`request` file is never consumed. Send the request well inside the window (a 1 h warm-up fits the
default); raise the TTL in the same launcher env for a longer warm-up. When the census arms, loop-watch
logs one WARNING to agent.log naming `HERMES_LOOP_WATCH_HEAP_CENSUS=<frames>`, the pid, the process
kind, UNTIL and the TTL, and another when it expires, so a forgotten opt-in is visible instead of only
slow. The TTL is counted on the monotonic clock, which stops while a Mac sleeps; UNTIL is the wall-clock
bound. A process that armed on a loop-watch older than the TTL (before 2026-11-13) has no expiry at
all and traces until it exits: restart it.

**Keep it out of shared `.env` files.** hermes-cred-relay copies `.env` keys to every gateway. A
`HERMES_LOOP_WATCH_HEAP_CENSUS` set in one host's diagnostic `.env` once reached two other gateways,
and every process started there traced for its whole life (allocation-heavy paths 5-20x
slower, Desktop polls of minutes). The relay's `env_deny` therefore holds `^HERMES_LOOP_WATCH_`; keep
it there, and set this key on the launcher of the one process you are diagnosing. The UNTIL window,
the gateway-only default, the one claim per home and the TTL are the further guards: a home `.env` is
read by every gateway, serve and CLI process of that home, on every restart.

`tests_heap.py` covers env parsing, default-off, attribution of a held allocation to its line, value
privacy, the one-shot trigger and output cap, the arming rules (an absent, stale or too-distant UNTIL
does not arm; a restarted process with the same env does not arm; serve, dashboard and CLI processes do
not arm unless named; an unwritable claim does not arm; the same through `register()` in real child
processes), the TTL (expiry calls `tracemalloc.stop` exactly once at the TTL with a fake clock, the
wall-clock UNTIL stops tracing when the monotonic clock stands still, a request before the TTL still
reports, pre-existing tracing is never stopped), and that the cache census defaults are unchanged.

## Opt-in owner census (no tracemalloc)

`roots.py` answers "which module global, frame or class holds the heap" for a process that is already
running, without tracing. Also **off by default**. An armed process pays one 1 s file check until the
request; nothing is traced or sampled.

**Arm** with a private file, not an env key (a `.env` key is relayed to other gateways and only read at
process start):

```sh
d="${HERMES_HOME:-$HOME/.hermes}/loop-watch-roots"; mkdir -p -m 700 "$d"
(umask 077; printf '{"until": %d, "kinds": ["gateway"]}' $(($(date +%s) + 86400)) > "$d/arm.new" \
  && mv "$d/arm.new" "$d/arm")
```

- `until`: epoch seconds, in the future and at most 72 h away. Otherwise the file is inert.
- `kinds` (optional): process kinds that may arm, from `gateway` (`hermes gateway run`), `serve`,
  `dashboard`, `other`. Default `gateway` only.
- The file must be a regular 0600 file of the same user with one link, at most 512 bytes, not a symlink.

loop-watch reads it whenever it registers: at process start and whenever the host reloads the plugin,
so a process that is already running arms at its next reload.
loop-watch registers only in `hermes gateway run` and `hermes serve` unless `HERMES_LOOP_WATCH=all`, so
the `dashboard` and `other` kinds need that too. An armed process logs one WARNING `roots census armed
in pid …` to agent.log; an arm file it ignores logs `roots census not armed in pid …: <reason>`.

**Disarm** by deleting the arm file: an armed process re-reads it every 30 s and stops waiting
(`roots census disarmed in pid …`) when it is gone, invalid or no longer names its kind, or at `until`.
Rewriting the file (atomically, as above) moves the window of a waiting process. A process consumes at
most one request in its lifetime: after a request, valid or not, it never arms again; one that disarmed
without a request arms again on a later registration with a valid arm file. Registrations racing on
several threads arm one probe. Request directories of past probes are left in place.

**Request** once per process, the same way as the cache census, under
`<HERMES_HOME>/loop-watch-roots/<pid>-<random-generation>/` with the command `roots-census-v1\n`
(16 bytes):

```sh
D=$(ls -d ~/.hermes/loop-watch-roots/<pid>-*/)
(umask 077; printf 'roots-census-v1\n' > "$D/request.new" && mv "$D/request.new" "$D/request")
```

The request rules (fresh regular 0600 file, atomic consume, one attempt per process) are those of the
cache census. Once disarmed, a later request in that directory is never consumed.

`result.json` (0600, at most 64 KiB) holds `schema: 1`, `kind: roots_census`, `status: ok|unknown`,
`truncated`, `bytes`, `objects`, `gc_objects`, `roots`, `owners`, `bitmap_bytes` (the scan's visited
bitmap), `seconds`, the phase totals
`module_bytes`, `frame_bytes`, `code_bytes`, `instance_bytes`, `unowned_bytes`, and `top`: the 100
largest owners as `[owner, bytes, objects]`. The walk is breadth-first from all owners at once, so a
shared object is counted once, for the nearest owner. Owners, in the order they claim:

| Owner | Holds |
|---|---|
| `module.name`, `module.name.attr`, `module.Class.attr` | a module global or class attribute and what only it reaches. A plain instance gets a row per attribute, two levels deep |
| `<frame file.py:qualname>` | locals of another thread's running frame, or of a suspended generator/coroutine |
| `<code module>` | what the module's classes and functions hold: class dicts, constants, docstrings, defaults, closures |
| `<inst module.Class>`, `<inst module.Class>.attr` | objects nothing above reached, by class. A class with at most 8 such instances gets a row per attribute |
| `<unowned type>` | the rest, by type |

**What the numbers are.** Sizes are `sys.getsizeof` (shallow) sums: Python object bytes, not the
process footprint. Allocator slack, C-extension buffers and freed-but-kept arenas are not in it, so
`bytes` is usually well under `footprint`. ints, floats and None are not counted. Values on a running
frame's evaluation stack are not visible, nor is a container the garbage collector does not track unless
an owner reaches it.

**Privacy.** The report holds names as they appear in code (module, global, class, attribute and
function names, source file basenames) with sizes and counts. No values, dict keys or reprs are read
into it. Every label part must be a short ASCII identifier (module parts may contain `-`, file names
also `.` and `+`); anything else is shown as `?`, and an instance attribute with such a name gets no row
of its own. A program that uses
data as identifiers (a module global or instance attribute named after a user or a session) would show
those names: the file is 0600 in the process's own home, treat it like a log.

**Code it runs.** Of the inspected objects: `__sizeof__` (as `sys.getsizeof` calls it; one that raises
counts 0 bytes, one that blocks would block the scan), finalizers of objects whose last reference the
scan happened to hold, and a data descriptor that a metaclass or a module subclass defines under the
name `__dict__`, `__module__` or `__qualname__`. Otherwise types are compared by identity and attributes
are read through the base `type`/`object` slots, so no `__eq__`, `__hash__`, `__getattr__`, instance
property or lazy import runs. One object that raises is skipped; it never ends the scan.

**Cost.** The one scan touches every live object once (on a host that is swapping, that faults the
compressed heap in, with the GIL held for that object). Measured: 2.1M gc-tracked objects (4.2M
counted, 470 MB) in 4.3 s on a quiet CPU; 1.5M gc-tracked objects in 3.8 s at load 16. While it runs it
needs about 1% of the heap for its bitmap plus 16 bytes per live container; the allocator may keep that
afterwards (a 537 MB test process ended at 593 MB, a 249 MB one at 295 MB). It runs on its own thread
and shares the GIL in the normal 5 ms slices, so the event loop keeps running, slower (longest gap seen
by a 1 ms ticker: 53-89 ms, against 21 ms idle); `gc.get_objects()` itself is one uninterrupted call
(23 ms at 1.5M objects). A 60 s deadline is checked every 16k objects and before each container of more
than 100k items (one such container is finished once begun: a 20M-item list took 2.4 s); past it the
scan skips what is left and reports `truncated: true`. Reading `__dict__` to name attribute rows materializes that instance's dict, so it
is done only for module-level instances and for classes with at most 8 unreached instances.

`tests_roots.py` covers: ownership by module global, class attribute, running-thread frame, suspended
generator and coroutine, closure, unreached class and unowned container; nearest-owner counting of a
shared object; attribute rows and their instance cap; that the report has no values or keys and masks
names that are not identifiers; that the census does not count itself or leave anything behind; a lazy
module is not imported; a raising `__class__` property, a metaclass with raising `__eq__`/
`__getattribute__` and no `__hash__`, and a raising `__sizeof__` do not end the scan (and the metaclass
code never runs); the deadline (every pass over the heap goes through the one deadline-checked
iterator, a passed deadline leaves nothing counted, a big container is not begun after it); the
owner-table and top-list caps; the arm file rules (absent, stale, too far, overflowing, not private,
hard-linked, symlink, oversized, process kinds); disarming by removal and arming again; a rewritten arm
file moving the window or excluding the kind; eight racing registrations arming one probe; a thread
that cannot start releasing the claim; the one-shot request and its cap; and `register()` in real child
processes. Not tested: a `__sizeof__` that blocks, descriptors on a metaclass, the worst-case report
size (computed: 20 KB of the 64 KiB cap), and behaviour on a swapping host.

## Bounded stall-time cost

The stall sampler runs while the backend is already stalled, so its own cost is capped
(`watch.py`): it reads raw frame metadata only (no `traceback.extract_stack`, whose
`linecache.checkcache` stat per frame file is a GIL hand-off against the stalled loop),
skips idle threads by their innermost function without walking their stacks, formats
only after the loop has caught up, spends at most `SAMPLE_BUDGET` (5%) of the stall's
wall time sampling (each pass's duration sets the wait before the next), takes at most
`MAX_SAMPLES` (40) per stall and samples at most `SAMPLED_STALLS_PER_WINDOW` (30) stalls
per 10 min; later stalls are timed only. Each stall report carries `sampling`
(`full`/`capped`/`rate_limited`) and `watcher_cost_s`. `stall_s` ends when the loop ran
the probe, excluding the watcher's report work. Tests: `tests_stall_cost.py`.

## Journal lock never blocks the event loop

The sidecar lock is shared by every loop-watch process on a host. `journal.append` waits at
most `DIAG_LOCK_TIMEOUT_S` (0.5 s) for diagnostics and `DURABLE_LOCK_TIMEOUT_S` (5 s) for
stats (which retry the same record), then raises `ETIMEDOUT`. Slow-request reports, which
`ReqTrace._finish` produces on the event loop, go through `offloop_sink` to a writer
thread with a bounded queue (64, dropped when full). Tests: `tests_journal_lock.py` hold
the lock from another process.

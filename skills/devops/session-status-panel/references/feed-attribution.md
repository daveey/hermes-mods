# Standalone feed performance attribution

Reproduce the deployed caller's enclosing `CoreReaders.pass_scope()` as well as `_compute_all`/batch: the legacy sweep establishes pass scope before computing, and omitting it disables describe memo eligibility. Separate cold remote hydration from warm work and use caller-thread counters.

Fail the experiment immediately if `feed.failures` is nonempty or output coverage is incomplete. Feed can catch an adapter error and return an apparently fast empty result. A packed SQL adapter must preserve cursor consumers such as `.fetchall()`, not only iteration.

Never count denied-write fallbacks as qualified timing; preserve canonical reads and explicitly fence only known persistence sinks. A standalone replay identifies work, not production CPU/context-switch acceptance.

## Context switches are GIL handoffs per fetched SQLite cell

Attribute the feed thread's voluntary switches to SQLite fetches before optimizing CPU: CPython's `_sqlite` releases the GIL around every `sqlite3_column_type` (one per cell), `sqlite3_data_count` and `sqlite3_step` (one each per row), and on the many-threaded serve each release costs about two switches. Model: handoffs = cells + 2 x rows + 7 x executes. CPU-seconds per pass (loop accounting) do not show this, so a CPU win can leave the switch gate unchanged.

Measure it live without ptrace: bpftrace uprobes on the serve's python exe (it links sqlite statically) for `take_gil`, `sqlite3_column_type`, `sqlite3_data_count`, `sqlite3_step`, filtered to the feed TID; find that TID by kernel comm `session-panel-f` in `/proc/<pid>/task/*/comm`, because py-spy thread headers carry no Python thread name. Rank callers with `ustack` on `take_gil`. Do not label GIL takes by a `drop_gil` probe: it is inlined and almost never fires.

Do not pack every read on the feed thread: a generic packed cursor kept exact row parity but doubled to quadrupled CPU, because the materialized CTE and JSON cost more than a one-row point lookup saves. Pack only statements whose cells per execute pay for it (wide `SELECT *` rows, long event lists) and batch the point lookups instead.

Reject a replay tick in which the origin-ledger refresh came due: `kanban_origin_ledger._db` issues `CREATE TABLE IF NOT EXISTS`, which a read-only authorizer denies, so that tick ran a different path. A `time.sleep(0)` contender thread does not reproduce GIL handoffs; do not read switch counts from such a harness.

## Controlled transport comparisons

Measure the calling thread's CPU and `/proc/thread-self/status` voluntary switches. For a controlled GIL competitor, use a pure-Python loop with a bounded lifetime and explicit switch interval, not `time.sleep(0)`: the latter releases the GIL immediately and does not reproduce a waiter convoy. Restore the switch interval and join competitors in reusable tests. Controlled contention is mechanism evidence, not the production acceptance census.

Compare each candidate query on one pinned read snapshot in native/packed/packed/native order, verifying names, types, order and values. Benchmark the actual proposed transport: a single aggregated JSON cell and the core 64-row `read_chunks` helper have different CPU/probe costs. Never extrapolate a generic pack-everything result to narrow one-row lookups; their setup cost can exceed the saved handoffs. Preserve negative results and CPU/wall/switch metrics separately.

Dynamic full-feed sequential A/B runs with different swept sets are not causal speedup proof. Separate source-producer exclusions from sweep coverage, and do not add nested loop-accounting stage counters as exclusive CPU. If a read-only replay denies a real producer write, reject that tick; do not benchmark the swallowed fallback.

Make benchmarks fail fast on raised errors, denied effects, missing watched bundles and parity mismatches as well as `feed.failures`. Preserve cursor contracts (`execute` returns a cursor with fetch/iteration), and reject failed timings rather than comparing them.

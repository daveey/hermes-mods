# Standalone feed performance attribution

Reproduce the deployed caller's enclosing `CoreReaders.pass_scope()` as well as `_compute_all`/batch: the legacy sweep establishes pass scope before computing, and omitting it disables describe memo eligibility. Separate cold remote hydration from warm work and use caller-thread counters.

Fail the experiment immediately if `feed.failures` is nonempty or output coverage is incomplete. Feed can catch an adapter error and return an apparently fast empty result. A packed SQL adapter must preserve cursor consumers such as `.fetchall()`, not only iteration.

Never count denied-write fallbacks as qualified timing; preserve canonical reads and explicitly fence only known persistence sinks. A standalone replay identifies work, not production CPU/context-switch acceptance.

## Ask why chats were admitted before costing statements

Read `session_panel.feed_tick` runs and wall from the loop-accounting delta first. When wall is about the whole window (7 runs, 680 s of wall in 604 s), the thread is never idle: a pass of 25 s or more earns no duty rest, so removing a statement shortens the pass without creating idle time and the switch gate barely moves. The lever is then how many chats each pass admits, not what one compute costs.

Classify the cause of every `kanban_task_change` mark in a window before blaming readers: snapshot the watched tables in one read transaction per sample, diff the rows of each marked card, and join the marks to the held bundles' card ids in the published feed snapshot (`~/.hermes/session-panel/feed-serve-*-default.json`, second line). A mark whose only movement is an `external_watch` event repeating the previous (state, health), or a `task_chat_links.released` flip, changes no card state, yet it recomputes every chat that holds the card. Diff two published snapshots by JSON path to see what such a recompute changes (poll clocks, the newest-events window, `activity_at`).

Read `reason_sizes` in a replay as overlapping sets: a reason that executes many statements per tick (`diagnostic_stale`) can admit zero chats.

## Context switches are GIL handoffs per fetched SQLite cell

Attribute the feed thread's voluntary switches to SQLite fetches before optimizing CPU: CPython's `_sqlite` releases the GIL around every `sqlite3_column_type` (one per cell), `sqlite3_data_count` and `sqlite3_step` (one each per row), and on the many-threaded serve each release costs about two switches. Model: handoffs = cells + 2 x rows + 7 x executes. CPU-seconds per pass (loop accounting) do not show this, so a CPU win can leave the switch gate unchanged.

Measure it live without ptrace: bpftrace uprobes on the serve's python exe (it links sqlite statically) for `take_gil`, `sqlite3_column_type`, `sqlite3_data_count`, `sqlite3_step`, filtered to the feed TID; find that TID by kernel comm `session-panel-f` in `/proc/<pid>/task/*/comm`, because py-spy thread headers carry no Python thread name. Rank callers with `ustack` on `take_gil`. Do not label GIL takes by a `drop_gil` probe: it is inlined and almost never fires.

Do not pack every read on the feed thread: a generic packed cursor kept exact row parity but doubled to quadrupled CPU, because the materialized CTE and JSON cost more than a one-row point lookup saves. Pack only statements whose cells per execute pay for it (wide `SELECT *` rows, long event lists) and batch the point lookups instead.

Reject a replay tick in which the origin-ledger refresh came due: `kanban_origin_ledger._db` issues `CREATE TABLE IF NOT EXISTS`, which a read-only authorizer denies, so that tick ran a different path. For subsequent caller attribution, copy that ledger with SQLite backup from an explicit read-only source into a new owned directory, redirect every connection to that exact ledger to the copy, and allow writes only there. Force all other database routes read-only, retain ATTACH/network/subprocess/file-write fences, and fail on any denied effect, missing bundle or feed failure. Test copy/source isolation and writable-URI coercion first. This is an isolated-ledger replay, not one atomic snapshot of all live stores or proof of source-reconciliation behavior. Separate the cold bootstrap and second-tick reference expansion from steady admission: newly hydrated card refs can add external-capture rows on tick2 without an ongoing wake defect. A `time.sleep(0)` contender thread does not reproduce GIL handoffs; do not read switch counts from such a harness.

## Qualify natural-deployment accounting

Preserve interrupted census cohorts as NO_PROOF, even when their shortened adjacent rate is below target. A route metadata change can precede a real natural backend rollover; retain the exact interruption record and independently resolve successor PID/birth/loaded code before replacing the incomplete acceptance window. Do not weaken the original collector's identity predicate to manufacture completion.

Check thread-family membership at every census sample and accounting boundaries before subtracting family totals. `loop_accounting._residual` sums live threads only and folds departed work into the separate `exited` aggregate, so family CPU/accounted totals can decrease during plugin-reload churn despite an unchanged process birth. Reject regressions and mismatched/short accounting windows; never clamp them or sum nested loop scopes to repair attribution. A stable successor window can qualify later acceptance, not retroactively repair the interrupted cohort.

## Controlled transport comparisons

Measure the calling thread's CPU and `/proc/thread-self/status` voluntary switches. For a controlled GIL competitor, use a pure-Python loop with a bounded lifetime and explicit switch interval, not `time.sleep(0)`: the latter releases the GIL immediately and does not reproduce a waiter convoy. Restore the switch interval and join competitors in reusable tests. Controlled contention is mechanism evidence, not the production acceptance census.

Compare each candidate query on one pinned read snapshot in native/packed/packed/native order, verifying names, types, order and values. Benchmark the actual proposed transport: a single aggregated JSON cell and the core 64-row `read_chunks` helper have different CPU/probe costs. Never extrapolate a generic pack-everything result to narrow one-row lookups; their setup cost can exceed the saved handoffs. Preserve negative results and CPU/wall/switch metrics separately.

Dynamic full-feed sequential A/B runs with different swept sets are not causal speedup proof. Separate source-producer exclusions from sweep coverage, and do not add nested loop-accounting stage counters as exclusive CPU. If a read-only replay denies a real producer write, reject that tick; do not benchmark the swallowed fallback.

Make benchmarks fail fast on raised errors, denied effects, missing watched bundles and parity mismatches as well as `feed.failures`. Preserve cursor contracts (`execute` returns a cursor with fetch/iteration), and reject failed timings rather than comparing them.

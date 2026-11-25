# Bounded feed computation

Cold `POST /changes` still computes missing bundles synchronously. Tick and changes share
in-flight work per session, not a global computation mutex. A session is reserved immediately
before its computation, not while queued behind unrelated sessions; other requests can therefore
compute a later session while an earlier session is blocked. Joiners wait outside publication
locks, emitting due heartbeats. Results remain joinable until publication. Exceptions release
ownership, and subsequent requests retry. A tick joining older reads retains dirty interest for
the next tick even after consuming its change cursor. There is no arbitrary join deadline: a cold
request must await its owner, not return empty/partial success. A genuinely slow owner can still
exceed the unchanged client HTTP deadline; this fix reduces duplicated work, not that deadline.

`CoreReaders.batch` retains only thread-local, pass-scoped work. It shares local enriched rows per
board/status set and replica inventory before lineage-dependent origin-obligation deduplication.
Core's membership selector still chooses creator/subscriber membership and `via`: it runs once per
board/status set over the whole pass's ids (`select_listed_local_task_set`), and each lineage takes its
cards from those (session, card) pairs in the selector's own order with creator precedence
(`_lineage_members`), not one selector call per chat.
Successful projected local rows populate the ordinary core busy memo. A failed union read falls
back once to core's ordinary per-lineage reads; execution-evidence errors also trigger that fallback.
Card details are deep-copied from a pass memo; unavailable/missing cards are not negative-cached.
File identity changes invalidate pass memos, and the feed thread releases its own connections
on exit. There is no cross-request trusted snapshot. Large union lineages fall back to ordinary
reads. No queue/detail fields, deadlines, scheduling policy, or JSON keys are removed/changed.

Compute sequence and trust age are assigned before the pass's first snapshot reads. Older
snapshots cannot replace newer publications. Heartbeats do not vouch failed, known-dirty,
source-degraded (`stale`, `partial_source_unknown`, `_execution_error`), or older-than-full-refresh
bundles. Source degradation is tracked internally without new JSON keys and retries on the next
tick even if stamps do not change. The epoch/revision/full-refresh contracts remain intact.
Review found the original age-stale regression missed successful core busy-fallback rows: no
exception meant they were classified as fresh. Task/done flag regressions and a real-reader
busy→fallback→heartbeat→recovery regression now exercise that detection gap, including newer
publication winning over older source-stale work.

## Failure analysis

Problem: cold catch-up and background tick can rebuild the same watched sessions concurrently;
production sampling implicated both callers in repeated local/replica/queue reader graphs.
Cached/empty changes controls do not reproduce cold computation.

- Why 1: both entry points call `_compute_all`; the original implementation independently calls
  `compute` for each session. Proof: the same-session regression performs three linked reads on
  the unchanged implementation, one with coalescing.
- Why 2: `_store`'s sequence guard prevents an older result from replacing a newer one but does
  not prevent either computation. Proof: the original `feed.py` compute/store split and the
  concurrency tests.
- Why 3: each `linked`/`done` call independently invokes `session_tasks`; each chain rebuilds
  details. Proof: the real-reader fixture performs 46 local and 46 replica hydrations and 215 queue
  detail calls for 23 sessions, versus 2, 2, and 24 after pass reuse.
- Root (happened): publication-order correctness was enforced without matching work ownership
  and pass-scoped shared-reader lifetime.
- Root (not caught): the introducing feed and review commits (`a0e43cad`, `04a15559`, `0fd0187a`)
  tested wire/publication ordering, heartbeat trust and merged catch-up replies, but their reader
  fixture did not measure overlapping same-session computation or multi-session real-reader
  amplification. The unchanged canonical backend suite collects 32 cases, not just the source
  function-definition counts. New concurrency and real-reader tests supply those missing inputs.
- Recurrence search: the feed history includes review follow-ups for sequence order, long-pass
  heartbeat/vouch semantics and merged catch-ups; none introduced shared computation ownership.
  This change preserves those prior safeguards and adds regressions instead of increasing timeouts.
- Countermeasures: per-session flights remove duplicated concurrent work; pass hydration reduces
  repeat local/replica/queue reads; deterministic overlapping requests and actual core-backed
  reader fixtures catch recurrence. A revised-draft review exposed reserved-but-unstarted flight
  head-of-line blocking; its regression failed with TimeoutError before lazy reservation and passed
  after. Additional regressions cover retry cursors, origin aliases, busy memo recovery, scope
  cleanup, thread-owned connection cleanup, and file replacement.

## Reproduction and limits

Run the whole backend suite with `scripts/test-all -k session-panel` (includes repo guards).
The real-reader tests require Hermes core; public core-free CI intentionally skips them. A core-free
CI pass is not evidence of the integration cases.

Run `tests/benchmark_readers.py --output <private-result.json>` with the managed core test Python,
core plus the canonical runner's test site on `PYTHONPATH`, and `HERMES_DISABLE_LAZY_INSTALLS=1`.
To compare before/after, `git archive` the baseline plugin into a separate directory and copy
`benchmark_readers.py` and `test_reader_batch.py` there; do not stash or modify the live checkout.

One paired Linux worker run, Python 3.14.9, core
`d912ee96dd38edbaad1c455f6899bca4a8437a71`, used 23 synthetic sessions, 24 local cards,
24 replica cards, 69 replica historical runs, shared/subscribed cards and distinct dependency chains:

| Metric | Original | Changed |
|---|---:|---:|
| Cold HTTP POST `/changes` | 2.908 s | 0.707 s |
| Full tick | 3.239 s | 0.394 s |
| Local enriched hydration calls | 46 | 2 |
| Replica inventory reads | 46 | 2 |
| Queue detail calls | 215 | 24 |
| Returned bundles / failures | 23 / 0 | 23 / 0 |

Both runs are below the existing 15-second deadline in this small synthetic environment; the
baseline here does **not** reproduce the production timeout. This exercises real core SQLite,
replica/run and queue computation through an in-process ASGI HTTP route, not stubbed latency.
It excludes network transport, large production inventories, origin-ledger population and live
host contention. These are computation/amplification measurements, not deployed incident recovery.

## Rollback and deployment

Revert the backend change with a reviewed revert and the same canonical tests/landing guard.
There is no schema migration or persistent new state to undo. A disk update alone does not reload
already-imported dashboard routes. Loaded-source activation and fresh production observation must
be verified separately; this implementation's synthetic benchmark is not that proof.

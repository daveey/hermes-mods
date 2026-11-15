# Revision-gated reclaim sub-sweeps keep clocks and process death live

Mirrored entries: 3886, 3887, 3888.

## Behavior

Dispatcher maintenance can reuse structural work while board content is unchanged, but due continuations/blocker probes still run on time and local process death/claim expiry cannot disappear behind a revision gate. Partial indexes reduce the empty candidate cost of always-live sweeps.

Tests additionally pin release-on-feed guards: unrelated failure commands, foreign claims, replaced claim identity and cards not locally running must not have their claim cleared.

## How it works

`kanban_sweep_gate` keys gates and memos by file identity, covered content revision/schema and optional gateway/config inputs. Keys are read before execution so concurrent or self-writes force a subsequent run. Unknown keys always run; failed runs erase prior proof. A per-tick due callback handles bookkeeping clocks, and a run-derived due time may be cached alongside the content key. Transaction-local results are not stored because rolled-back revision values can recur.

Continuation parks return their earliest pending wake; blocker release/probe lanes report lookback expiry and deferred probe time. Foreign-card ids are memoized only structurally; archive candidates use event-kind-driven SQL while per-card retry clocks stay active. `crash_sweep_due` includes any locally claimed worker PID, not only running status or pending neutral effects. Partial task/run indexes cover liveness/rate-limit/blocker candidates. A follow-up defers task_runs partial-index creation if a legacy TEXT-primary-key table lacks needed columns, allowing rebuild to install them later. The benchmark operates on a copy with external effects stubbed; it is not itself a test result.

## Code

- `hermes_cli/kanban_continuation.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_dispatch_claims.py`
- `hermes_cli/kanban_idle_candidates.py`
- `hermes_cli/kanban_placement_forward.py`
- `hermes_cli/kanban_shared_blockers.py`
- `hermes_cli/kanban_sweep_gate.py`
- `hermes_cli/kanban_wake_check.py`
- `hermes_cli/kanban_wake_episode.py`
- `scripts/kanban_reclaim_replay_bench.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/hermes_cli/test_kanban_sweep_gate.py`: Checks unchanged skips, content invalidation, live/run-derived due times, unknown counters and failed-run retries, foreign-id memo invalidation, partial-index query plans and metadata fast-path equivalence. Real claim expiry and a killed child worker must reclaim on a write-free board; quota recovery, continuation wake and stranded-blocker probe must still fire.
- `tests/hermes_cli/test_kanban_wake_episode.py`: Adjusts config fixtures to the readonly load path and preserves configuration/default-outage coverage.
- `tests/hermes_cli/test_kanban_watcher_idle_work.py`: Requires empty unchanged blocker lanes to run once, then rerun when content changes, rather than invoking handlers every tick.
- `tests/hermes_cli/test_kanban_release_claim.py`: Checks another task’s queued failure command and foreign-host claim do not release this claim, a newly replaced lock/PID survives compare-and-swap, and nonrunning local cards retain claims despite feed status changes.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

Never memoize process liveness or time-derived decisions as structural state. A claimed PID can die without any revision write. The legacy-index follow-up adds no dedicated test file in this batch; the documented query-plan tests cover modern schema indexes, not a newly added legacy migration assertion. The replay benchmark was not executed by this pass and no CPU reduction is claimed.

## Plugin?

No. These decisions are interleaved with authoritative dispatcher reclaim and database migration paths.

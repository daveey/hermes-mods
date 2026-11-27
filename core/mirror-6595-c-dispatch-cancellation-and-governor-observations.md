# Dispatch storage cancellation and governor observations

**Scope:** Pass-wide cancellation signal; governor snapshots; expiry after I/O.

**Source ordinals:** 6451, 6475, 6490, 6567, 6568

## Behavior

- Typed pass-wide dispatch storage cancellation is now a BaseException outside `Exception` (same idea as asyncio cancellation), consumed only by the dispatch storage runner, so best-effort `except Exception` handlers in a pass (wake ticks, supervisor draining, worker placement, owner-chat backfill, spend-review start) cannot swallow it and keep working. Write transactions still roll back when the commit boundary is cancelled.
- Governor/why-fast snapshots judge expiry after reader I/O (clock sampled after reads, fast reader rechecked after its indexed read), so a genuine concurrent publication is not rejected as future-dated and an expired decision is not returned with a pre-I/O age.
- A bounded preclaim abort now records current storage-admission evidence through a tracked, guarded read-only path without reentering the writer, so governor snapshots stop expiring across active contention retries. The record is a file-only snapshot of a new `storage_admission` governor (phase `preclaim_aborted`, scheduling not admitted, `triage` listed as an excluded status): it is skipped on dry runs, reads through a `mode=ro` connection with a 0.2 s deadline, never reuses stale per-card decisions, and any failure to observe is swallowed so the real storage refusal is unchanged. Why-fast snapshot answers now also carry `phase`.
- `DispatchWriteContended` no longer subclasses `sqlite3.OperationalError`; the commit boundary and the storage runner name it explicitly. Whether the census still covers rows beyond the retained card limit when review dispatch is disabled is only exercised by the last commit of the series (a test-only change) and was not verified from production code here.

## How it works

- `kanban_dispatch_storage.run` is the single place that converts the cancellation signal; handlers in `kanban_db_dispatch.py`, `kanban_dispatch_maintenance.py`, `kanban_supervisor_requests.py` and `kanban_wake_check.py` let it propagate.
- `kanban_governor_storage.py` and `kanban_dispatch_owner.py` record the observation; `kanban_governors.py` and `kanban_why_fast.py` do the post-I/O expiry judgement.

## Code

Files touched by the series (paths relative to the source tree):

- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_dispatch_maintenance.py`
- `hermes_cli/kanban_dispatch_owner.py`
- `hermes_cli/kanban_dispatch_storage.py`
- `hermes_cli/kanban_governor_storage.py`
- `hermes_cli/kanban_governors.py`
- `hermes_cli/kanban_supervisor_requests.py`
- `hermes_cli/kanban_wake_check.py`
- `hermes_cli/kanban_why_fast.py`

## Tests

Source test files changed or added by the series:

- `tests/hermes_cli/test_governor_expiry_boundary.py`
- `tests/hermes_cli/test_governor_storage_observation.py`
- `tests/hermes_cli/test_kanban_dispatch_cancel_signal.py`
- `tests/hermes_cli/test_kanban_preclaim_cancellation.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6451-6568, 5 commits: 6451, 6475, 6490, 6567, 6568) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Budget, timeout and admission thresholds are unchanged; only the propagation path changes.
- Code that catches `Exception` and expects to see the cancellation will no longer see it.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Dispatcher internals.

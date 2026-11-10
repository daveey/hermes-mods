# Cron gateway-shutdown interrupt reporting

Patch **2084**.

## Behavior

When gateway shutdown marks running jobs interrupted, the interrupted outcome clears the run's fire claim; the heartbeat then sees a missing claim. The run now stops with "Gateway shutdown interrupted the run", logged once at WARNING without traceback, instead of ERROR "cron fire ownership was lost". A genuine foreign takeover keeps the lost-ownership wording and ERROR plus traceback; other failures are unchanged. The owner-side forwarded `cron.cancel` (which carries no reason) also reports shutdown when this process interrupted the fire.

## How it works

`ShutdownAwareCancel` wraps a latch with an in-process "interrupted" predicate; its `reason` becomes `SHUTDOWN_INTERRUPT_REASON` and `shutdown_interrupted` is read through `_CombinedCancelEvent`. The scheduler's fire-claim heartbeat latch is shutdown-aware, scoped by execution token; `_abort_if_fire_claim_lost` raises `CronShutdownInterrupted`; `run_job` logs it through `_log_shutdown_stop`. `fire_interrupted_by_shutdown(job_id)` serves the owner gateway's `execute`.

## Code

- `cron/cancellation.py`, `cron/scheduler.py`, `gateway/session_cron.py`.

## Tests

**Source regression coverage; not rerun in this documentation pass.** `tests/cron/test_shutdown_interrupt.py`: a real heartbeat through a real interrupt mark stops as shutdown; foreign takeover still lost ownership; owner execution reports shutdown only when this process interrupted the fire; watchdog raises the shutdown type; one WARNING without traceback; other failures keep ERROR plus traceback. `tests/cron/test_cron_relay_run_forward.py` fakes now accept the new keyword.

## Applying

Apply ordinal(s) 2084 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. Builds on [cron-shutdown-failure-accounting.md](cron-shutdown-failure-accounting.md). No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

The interrupted flag is per process; an interrupt performed by a different gateway process still reads as lost ownership. Shutdown already wrote the job's interrupted status, so no extra bookkeeping is done.

## Could this be a plugin?

No. It threads through the scheduler's claim heartbeat and gateway cron execution.

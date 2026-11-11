# One bounded cron rerun after restart interruption

Patches **2167–2168**. Builds on [cron-gateway-shutdown-interrupt-2131.md](cron-gateway-shutdown-interrupt-2131.md).

## Behavior

The built-in scheduler's first startup recovery for a home can schedule an eligible dead-owner attempt immediately, once, instead of waiting for its next natural slot. Recurring jobs get an extra fire that preserves their schedule; one-shots can be re-armed with the interrupted dispatch's repeat budget restored. The interrupted execution remains classified `unknown`; the rerun is not booked as a failure.

Deleted, paused or subsequently edited jobs, older attempts superseded by a newer execution, and admission-journal-owned fires are left alone. A rerun interrupted again is not automatically rescheduled. Ordinary periodic reaping and a ticker restarted mid-process do not invoke this restart rerun path. Chronos classifies interrupted executions without requeueing; a later built-in fallback must not reinterpret that as startup.

## How it works

`cron.executions.startup_recovery` is thread-local. `scheduler_provider` claims at most one startup pass per home per process, respecting profile ownership gates. Only owner-gone records from this scope reach `restart_rerun`, which checks dead claiming-scheduler ownership, newest execution, operator `edited_at` and a 30-minute restart window measured from the latest claim/run bookkeeping. It applies the change under the job fire fence.

A `restart_rerun` marker records execution ID and reschedule time; a real subsequent outcome clears it, a shutdown interruption does not. Definition/lifecycle edits, rearming and changed imported definitions acquire edit stamps; unchanged imports and ordinary bookkeeping do not. 2168 exposes `mark_startup_recovered` as a non-abstract `CronScheduler` method so Chronos can settle its startup pass without importing a new core helper across the plugin boundary.

## Code

`cron/{executions,job_definition,jobs,restart_rerun,scheduler_provider}.py`, `plugins/cron_providers/chronos/__init__.py`.

## Tests

**Upstream-supplied coverage; not executed in this mirror documentation pass.** `tests/cron/test_restart_rerun.py` covers recurring schedule preservation, one-shot budget restoration, manual-run preservation, interrupted rerun suppression, same-execution idempotence, edits/pause/resume/run-now/rearm, changed versus unchanged imports, dead versus live/wedged ownership, restart-window heartbeats, busy fences, journal ownership and newer executions.

Its startup cases include `test_reaps_outside_startup_settle_without_rerunning`, `test_ticker_startup_recovery_reruns_the_cut_off_attempt` (single and multiplexed), `test_ticker_restarted_mid_life_does_not_rerun`, profile-gate acceptance/rejection/error cases, and `test_chronos_fallback_ticker_does_not_rerun`. 2168 adds no test file; the Chronos fallback regression covers its method call. No live scheduler restart was performed for this document.

## Applying

Use the full ordered replay in [README.md](README.md), including its replay plan and bridges. These patches are not independently applicable.

## Gotchas

A rerun is a new execution, not a transaction rollback: external side effects from the unknown attempt may already exist. This is not an exactly-once guarantee. A scheduler stopped beyond the restart window is left to normal missed-run policy. Failure or a busy fire fence is logged and skipped, not proof a rerun occurred.

## Plugin?

The provider participates through the additive base-class method, but job fencing, execution recovery and repeat bookkeeping belong to core. Chronos intentionally retains its no-requeue policy.

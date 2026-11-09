# Cron shutdown interruptions preserve failure accounting

## Behavior

Patch **2066** (`ee2d235b148f075c2baf11506c5a195f23be8c8f`) records gateway-shutdown interruptions as errors with sticky `last_failure` evidence, without incrementing `failure_streak` or changing `last_error_kind`. A later genuine agent failure still increments the streak.

## How it works

`mark_running_jobs_interrupted` passes `shutdown_interrupted=True` through `mark_job_run` to `_record_run_outcome`. The dedicated failure branch records diagnostic evidence but leaves prior accounting intact. Existing owner fencing, interruption reasons and heartbeat handling are unchanged.

## Code

- `cron/jobs.py`: outcome branch and optional flag plumbing.
- `cron/scheduler.py`: only the shutdown caller sets the flag.

## Tests

`tests/cron/test_shutdown_interrupt.py::TestMarkRunningJobsInterrupted::test_shutdown_preserves_failure_accounting` uses real isolated stores with prior streaks 0, 2 and 3 and kinds absent, gate and agent. Each case checks error status, failure detail, preserved accounting and a subsequent genuine agent failure. Registry tests check the flag accompanies owner identity. See [snapshot proof](mirror-2067-verification.md) for tests actually executed in this mirror pass; no live gateway shutdown is claimed.

## Applying

Use the complete [README Applying recipe](README.md), including the upstream bridge before 2066. The patch is not certified independently of its preceding series.

## Gotchas

This is not a success outcome and does not erase earlier job failures. The default flag is false, so ordinary failure paths retain existing accounting.

## Plugin?

No: the change is inside canonical cron outcome persistence and the scheduler shutdown call site.

# Condition-driven scheduled wakes

Patches: 0251, 0270.

## Behavior

Scheduled work can wake before its deadline when a wake_check command succeeds. Schedule, time-block and delayed continuation accept wake_check/check_every/clock_only; long parks require a probe or explicit clock_only.

## How it works

Persisted task_wake_checks are polled off-thread with bounded concurrency and a 30-second command timeout. Exit 0 emits condition_met; exit 1 keeps waiting; three broken checks wake with wake_check_broken. Diagnostics expose clock-only, failing and overdue checks.

Patch 0270 adds a persisted generation to each probe: results from an old in-flight probe cannot mutate a replacement park. Shared-blocker parks enforce the same probe/clock-only requirement through delayed continuation, time-block and dashboard scheduling.

## Code

hermes_cli/kanban_wake_check.py; kanban_db_schedule.py; kanban_timed_park.py; kanban_continuation.py; kanban_diagnostics.py; tools/kanban_tools.py; tools/kanban_tools_schemas.py

## Tests

tests/hermes_cli/test_kanban_wake_check.py covers schedule/time-block/continue tools, CLI flags, actual shell probes, bounded nonblocking dispatch, failure/timeout recovery, diagnostics and worker context.

Patch 0270 verification: wake-check, continuation and timed-park suites ran together: 43 passed. Tests cover old-generation success/failure and shared-blocker long-park guards. Dashboard tests are shipped but were not rerun in this verification.

Test files above are shipped regression coverage, not a claim that every suite was rerun. See [mirror-verification.md](mirror-verification.md) for this export’s execution results.

## Applying

Apply the ordered core series through the last listed patch using `git am -3`, starting at the fork/upstream merge-base; these are not independent patches.

## Gotchas

Probes run shell commands: use cheap read-only checks. API state exposure and Desktop display are not end-to-end tested here. The existing deadline still applies.

## Plugin?

This changes core-owned behavior or its test harness; no disk plugin is required.

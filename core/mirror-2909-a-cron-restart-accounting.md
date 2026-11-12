# Cron drain observation, once-only accounting and restart-cut reruns

Patches: **2754, 2766, 2783**.

Related context: [cron-restart-rerun-2215.md](cron-restart-rerun-2215.md), [cron-shutdown-failure-accounting.md](cron-shutdown-failure-accounting.md).

## Behavior

A draining gateway still answers external firers' cron status/recover/cancel requests while refusing new cron submits. Competing terminal paths book one outcome per execution. A run cut by its owner's restart is shutdown-interrupted rather than failed, and receives at most one rerun; a completed result retains its actual verdict.

## How it works

2754 adds `cron.status`, `cron.recover`, `cron.cancel` to restart-wait WebSocket methods, leaving `cron.submit` excluded. 2766 passes the same `execution_id` through cancellation, ordinary body/tail bookkeeping, ownership-lost and unknown-worker/death accounting, making the second observer's booking a no-op regardless of arrival order. 2783 adds `cron/restart_cut.py`: the owner marks `_restart_cut` when its own drain interrupts/cancels a run (not an operator cancellation), or admission status discovers an interrupted resultless run across restart. The firer's tail or next owner's journal recovery records shutdown-interrupted with suppressed delivery, no failure streak or failure notice, lifts uncertainty pause and requests one restart rerun. A draining refusal of a manual fire is journaled before checking absence so recovery can defer it too. Existing restart rerun machinery restores extra-fire far-future slots and re-arms one-shots; a rerun cut again cannot schedule a second rerun.

## Code

- `cron/cancellation.py`
- `cron/restart_cut.py`
- `cron/restart_rerun.py`
- `cron/scheduler.py`
- `cron/scheduler_authority.py`
- `cron/scheduler_worker_failure.py`
- `gateway/drain_control.py`
- `gateway/run_shutdown.py`
- `gateway/session_cron.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_execution_books_once.py`: `test_worker_death_then_ownership_lost_books_one_failure`, `test_ownership_lost_then_worker_death_books_one_failure`.
- `tests/cron/test_restart_cut.py`: `test_direct_run_cut_by_owner_restart_reruns_once_without_a_failure`, `test_rerun_cut_again_is_not_rerun_a_second_time`, `test_an_ordinary_failure_still_counts`, `test_owner_marks_only_its_own_restart_cut`, `test_status_of_an_admission_lost_across_the_restart_reports_the_cut`, `test_restart_safe_worker_cut_is_rerun_by_journal_recovery`, `test_manual_fire_refused_while_draining_is_deferred_not_failed`, `test_draining_refusal_whose_absence_check_was_lost_is_deferred_by_recovery`.
- `tests/cron/test_run_one_job.py` (fixture/assertion changes; no test definition name appears in this diff).
- `tests/gateway/test_session_cron.py`: `test_draining_owner_answers_an_external_firers_cron_observation_over_the_socket`.

Coverage is limited to the entry points and fixture controls above; live services, deployment and user-interface behavior are not established by this documentation task.

## Applying

Use the [README full replay procedure](README.md#applying). Apply these patches in published ordinal order with the intervening series, replay bridges and mandatory post-replay repair described there. They are incremental changes, not independently certified cherry-picks. This document does not claim that the full mirror replay or feature suites ran during the documentation pass.

## Gotchas

This is not a generic retry of failures: ordinary failures still count, operator cancellations stay cancellations, and completed verdicts win. The owner-cut marker, execution identity and journal recovery must agree; missing admission evidence is not success. Tests include a socket observation path and controlled scheduler/recovery paths, not an actual gateway update killing production cron jobs.

## Plugin status

Core/CI implementation; no plugin is added or required. A plugin cannot replace the shared database, admission, worker or workflow invariants documented here.

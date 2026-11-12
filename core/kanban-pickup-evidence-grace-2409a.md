# Pickup-stall evidence grace and grouped target refresh

Mirror entries: [2322](patches/2322-daveey-fix-kanban-wait-for-pickup-evidence-and-refresh-grouped-alert-targets.patch), [2323](patches/2323-daveey-bound-missing-pickup-evidence-grace-and-group-failed-pass-alerts.patch).

## Behavior

- Do not fabricate a pickup refusal when the detector runs before pickup has recorded its first skip after a drain.
- Persist the first evidence_missing observation and allow one additional configured stall interval; after that bounded grace, file a grouped host alert even if pickup repeatedly fails or the process restarts.
- Refresh an existing same-cause repair thread with newly affected targets only; unchanged ticks do not churn comments and terminal repairs are not updated.

## How it works

2322 initially waits for skip evidence and adds _refresh_targets, which checks the open repair body plus detector comments before appending new target IDs. 2323 bounds that wait: record a durable evidence_missing marker on first observation, compare first_at against stall_seconds, then group it as a host-level cause alongside hold/budget conditions. The alert explicitly says this is missing evidence, not a recorded pickup refusal, and directs inspection of pickup exceptions/timeouts and dispatcher logs.

## Code

- `hermes_cli/kanban_pickup_stall.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/hermes_cli/test_kanban_pickup_stall.py` — `test_first_tick_after_drain_waits_for_skip_evidence_before_filing`, `test_a_full_queue_files_one_card_per_host_naming_the_oldest`, `test_same_cause_burst_refreshes_all_targets_without_comment_churn`, `test_refresh_does_not_append_to_closed_repairs` (additional cases in this file).

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

Do not interpret the first missing-evidence tick as proof of a capability or target-specific failure, and do not wait indefinitely either. Group refresh must not duplicate targets or append to done/archived cards. The source failed-pickup regression covers bounded escalation, not a live worker failure reproduced by this mirror.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.

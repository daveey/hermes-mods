# Cron restart-cut reruns and one-shot admission holds

Patches: **2991, 2992, 3005, 3029, 3041**.

## Behavior
- After a cut (restart-interrupted) rerun is skipped, its pending guard expires, so a later natural fire that was deferred gets its own rerun, while replay of the skipped rerun is still rejected.
- A deferral inherits only a pending rerun marker.
- One-shot jobs awaiting admission settlement are preserved by the due scan instead of being deleted before journal recovery.
- A malformed admission journal fails closed: the exhausted one-shot is kept, the journal is never deleted, and a warning names the journal path.
- A run stopped by gateway shutdown or restart records `last_failure.kind = "lifecycle_interrupt"`; ordinary failures carry no kind.

## How it works
- 2991 expires the pending guard in `cron/restart_rerun.py` after a skipped cut rerun.
- 2992 gives sequential frozen-clock executions in the cut test distinct claim times.
- 3005 adds an authority check in `cron/scheduler_authority.py` so `cron/jobs.py` keeps one-shots with pending admission.
- 3029 stamps the typed kind when `mark_job_run` is called with `shutdown_interrupted=True`.
- 3041 introduces a shared identity-checked journal iterator, raises `CronJournalCorrupt` (a ValueError carrying the path) for damaged journals, and makes `claim_dispatch` hold the fire instead of reaching the crash path in the concurrent exhaustion race.

## Code
- `cron/restart_rerun.py`
- `cron/jobs.py`
- `cron/scheduler_authority.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_restart_cut.py`: `test_later_natural_fire_deferred_after_a_skipped_rerun_gets_its_own_rerun`, `test_oneshot_due_scan_before_journal_recovery`, `test_oneshot_corrupt_journal_holds_and_names_the_journal`, `test_claim_dispatch_race_with_corrupt_journal_holds_without_bookkeeping`.
- `tests/cron/test_restart_rerun.py`: `test_deferral_inherits_only_a_pending_rerun_marker`, `test_skipped_rerun_expires_guard_but_still_rejects_replay`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.

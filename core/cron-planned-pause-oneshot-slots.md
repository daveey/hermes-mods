# One-shot cron slots survive planned update pauses

Patches: 1255

- `patches/1255-fix-cron-preserve-one-shot-slots-across-planned-upda.patch`

## Behavior

A one-shot due during a planned admission hold or gateway drain remains eligible after a successor resumes scanning, even beyond ordinary one-shot grace. A second due scan does not take its live claim. Already-missed slots outside the pause and later stale slots retain normal retirement behavior.

A refused dispatch can retry the protected occurrence. Changing its scheduled instant does not carry the exemption to the replacement slot. The gateway's pause-for-update control request records cron pauses before asking for drain, including eligible profile homes rather than an unowned or deleted profile.

## How it works

`cron/planned_pause.py` atomically publishes separate mode-0600 JSON intervals in the store's `planned-pauses` directory without taking the jobs lock. Suppressed admission and drain ticks call `begin`; the control handler calls `preserve_cron_slots` before marshaling `request_restart` onto the event loop.

Under the jobs lock, recovery closes open interval ends durably before changing jobs. Enabled one-shots due between start minus grace and end receive `planned_pause_slot` equal to `next_run_at`. `covers` compares those exact strings. A failed end write defers the scan; a failed jobs save leaves closed intervals available for replay. After the save, cleanup removes only the scanned snapshot, not a concurrently published interval. `retire_expired` keeps the existing outage exceptions as well as the new pause exception.

## Code

`cron/jobs.py`, new `cron/planned_pause.py`, `cron/scheduler_tick.py`, new `gateway/cron_pause.py`, and `gateway/run_bootstrap.py`.

## Tests

Included tests were inspected, not re-run for this documentation update.

`tests/cron/test_restart_deferred_oneshot.py`:
- `test_oneshot_due_during_long_pause_survives_successor`, parametrized for drain and admission, checks preserved due time, one claim, and retirement of unrelated stale slots.
- `test_refusal_and_schedule_edit_do_not_lose_or_widen_recovery` checks refusal/retry and that rescheduling does not widen eligibility.
- `test_pause_recovery_contains_corruption_and_failed_end_write` injects malformed pause data and a failed interval-end write, then checks recovery, cleanup and unrelated due work despite corrupt pause data.
- `test_pause_publication_never_waits_on_jobs_lock_and_cleanup_is_snapshot_local` holds the jobs lock while publishing and checks that later publication survives earlier snapshot cleanup.

`tests/gateway/test_update_pause_cron_slots.py::test_pause_socket_records_cron_pause_before_drain` uses a real control server and request, asserts pause evidence exists before restart is requested, excludes foreign/deleted profiles, and selects the preserved one-shot after the pause. A deployed updater/service-manager restart is untested here.

## Applying

Apply the complete numbered series at its documented merge-base with `git am -3 --empty=keep`; see `README.md`. This uses the earlier cron admission, store-health and one-shot claim machinery.

## Gotchas

- This preserves specific occurrences, not every old one-shot schedule.
- Pause publication failures go through store-health diagnostics; recovery write failures return no due jobs for that scan.
- Interval ends are pinned before the jobs save so replay cannot extend a recovered pause to later slots. Actual process-crash injection between those writes is untested by the new tests.

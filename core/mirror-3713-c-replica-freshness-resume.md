# Replica-freshness alarm resumes the awake clock across restarts

Patches: **3712**.

## Behavior
- A replica stuck behind across frequent gateway restarts now reaches the stale threshold (1200 awake seconds) and files exactly one card; previously each restart reset the clock, so telemetry read `not advanced for 0 awake s` and no alarm fired.
- An open incident is not filed again after a restart.
- A machine's sleep is never counted; a new boot, an unwatched gap longer than the threshold, or a detached board starts the clock afresh.
- Shadow-mode boards are judged too.

## How it works
3712 writes each observation to `kanban/.replica-age.json` under the kanban home as `{stamp, since, open, seen_at, boot}`. A process with no in-memory observation resumes it only if the boot id matches (monotonic time is shared per boot and stops in sleep; no boot id means no resume), `since <= seen_at <= now`, and the last observation is within the stale threshold. Detaching or unsyncing a board drops its record. Two surviving mutants are also pinned by tests: shadow-mode exclusion and dropping `note_round` from the sync loop's after-round hook.

## Code
- `hermes_cli/kanban_sync_replica_age.py`
- `tests/hermes_cli/test_kanban_sync_replica_age.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_sync_replica_age.py`: `test_a_shadow_mode_board_is_judged_too`, `test_the_sync_loops_after_round_records_the_outcome_the_card_names`, `test_a_stall_across_gateway_restarts_every_4_min_files_exactly_one_card`, `test_restarts_resume_the_awake_clock_but_never_count_sleep`, `test_a_new_boot_starts_the_clock_afresh`, `test_an_unwatched_gap_starts_the_clock_afresh`, `test_an_open_incident_survives_a_restart_without_filing_again`, `test_a_detached_board_forgets_the_remembered_clock`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Without a readable boot id the clock does not resume (old behavior).
- A gap longer than the stale threshold between observations deliberately restarts the clock rather than alarming at once.

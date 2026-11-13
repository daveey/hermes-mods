# Cron: re-arm a restart rerun the host slept through

Patches: **3659**.

## Behavior
- If a restart cuts a cron job and the immediate rerun is also cut because the host slept, the rerun is re-armed instead of dropped. Condition: the cut attempt had under 10 awake minutes, or the host was asleep 10+ minutes for most of the attempt. At most 3 reruns per original cut.
- Attempts with no awake/wall measurement keep the old rerun-once behavior.
- On macOS, the due scan holds a pending restart rerun while the machine is not fully awake (pmset UserIsActive, or lid open on AC); the next scheduled slot settles a held rerun.
- A held one-shot rerun is exempt from the one-shot grace gate, so a long DarkWake no longer retires the redone work as missed.

## How it works
- The owner stamps each cut attempt's awake (`time.monotonic`) and wall seconds (`_run_clock` beside `_restart_cut` in `gateway/session_cron.py`).
- `cron/restart_cut.py` carries the stamps; `cron/restart_rerun.py` decides re-arm vs stop, counts reruns, and implements the full-wake probe; `cron/jobs.py` applies the grace exemption.

## Code
- `cron/jobs.py`
- `cron/restart_cut.py`
- `cron/restart_rerun.py`
- `gateway/session_cron.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/cron/test_restart_cut.py`: `test_owner_cut_of_a_rerun_the_host_slept_through_is_rerun_again`, `test_owner_cut_of_a_rerun_that_was_awake_ten_minutes_stops`.
- `tests/cron/test_restart_rerun.py`: `test_rerun_cut_while_the_host_slept_is_rearmed`, `test_rerun_cut_after_ten_awake_minutes_is_not_rearmed`, `test_unmeasured_rerun_keeps_rerun_once`, `test_slept_rerun_cap_is_three_per_original_cut`, `test_slept_through_reads_the_owner_clock`, `test_pending_rerun_waits_for_full_wake_until_the_next_slot`, `test_oneshot_rerun_held_past_grace_fires_on_full_wake`, `test_full_wake_probe_reads_pmset_assertions`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The hold applies only where the pmset probe is meaningful (macOS); the tests mock pmset output.

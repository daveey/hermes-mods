# Host loop lease: one flock-held owner per Hermes home

Patches: **3405**.

## Behavior
- Infrastructure for host-wide background loops: `HostLoopLease(name, role, interval=...)` gives one owner per Hermes root home (profile processes share it). No loop is moved onto it by this patch.
- Standbys take over within an interval after the holder dies (the kernel frees the lock); a wedged holder (no `beat()` for more than 3x the interval) releases through a watchdog, and standbys log one warning per holder on a stale heartbeat.
- Priority gateway > serve > dashboard: a higher-priority standby registers a `.want` file and hands off at the holder's next `beat()`; lower priorities never preempt.
- `held()` costs one flock syscall when not holder. Kill switch `HERMES_HOST_LOOP_LEASE=0` makes `held()` always true. An unopenable lock (no fcntl, uncreatable file, flock error) fails open and logs once; a read-only existing lock file is still locked.
- Holder metadata (pid, start time, role, priority, core sha, heartbeat, release time) is written to the lock file, and `hermes doctor` lists each lease's holder under "Host Loop Leases".

## How it works
- 3405 adds `hermes_cli/host_loop_lease.py` using `flock(LOCK_EX|LOCK_NB)` on a lock under the root home's `run/host-loops/`, with the fd close-on-exec and a forked child closing its copy without unlocking. `hermes_cli/doctor.py` and `doctor_state.py` show the holders.

## Code
- `hermes_cli/host_loop_lease.py`
- `hermes_cli/doctor.py`
- `hermes_cli/doctor_state.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_host_loop_lease.py`: `test_two_processes_only_one_holds`, `test_holder_killed_standby_takes_over_within_an_interval`, `test_priority_handoff_third_process_does_not_steal`, `test_lower_priority_never_preempts`, `test_wedged_holder_released_by_watchdog`, `test_kill_switch_every_process_holds`, `test_kill_switch_in_process`, `test_unwritable_dir_fails_open_and_logs_once`, `test_read_only_lock_file_still_locks`, `test_key_is_the_root_home_not_the_profile`, `test_stale_heartbeat_warns_once_per_holder`, `test_release_lets_a_standby_in_and_records_release`, `test_release_window_standby_defers_to_live_want`, `test_dead_want_is_ignored`, `test_doctor_lists_holders`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Fails open by design: if the lock cannot be used, loops may run duplicated rather than stop.
- Never call `LOCK_UN` in a forked child; it would free the parent's lock.

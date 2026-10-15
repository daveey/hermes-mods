# Kanban keeps live workers running when the macOS clock shifts

Patches:

- `patches/0079-fix-kanban-tolerate-macOS-start-time-drift-in-worker.patch`: kanban dispatcher. A worker's
  recorded start time now matches its current start time within 2 s instead of exactly.
- `patches/0080-fix-kanban-compare-macOS-worker-start-times-boot-rel.patch`: kanban dispatcher. On macOS the
  worker fingerprint also records `kern.boottime`, and start times are compared relative to boot, so a clock shift of
  any size is not read as a crash.

What you see: restart the gateway (or wake the Mac) while kanban workers are running, and the cards stay `running`.
The workers finish their cards as normal. Stock Hermes could read every live worker's start time as 1 s off on
macOS. It then booked all of them `crashed` in one tick (16 at once in the observed case), blocked the cards as a
"systemic" failure, and spawned a duplicate worker next to each original.

## Behavior

- **Small drift (up to 2 s) is tolerated** for every fingerprint, on any platform. This covers the 1.00 s shift that
  was observed.
- **On macOS, a boot-time re-anchor of any size is tolerated** for workers spawned after 0080. Sleep/wake, a manual
  clock change, or a large NTP step moves every process's start time at once. The boot-relative start time does not
  move, so the worker still matches.
- **A worker that really exited is still reclaimed.** Its PID is gone, so the liveness check fails as before.
- **A recycled PID is still foreign.** A new process that got the same PID started after the worker died. Its start
  time is off by at least the old worker's lifetime, both absolute and boot-relative, so it does not match unless
  that lifetime was under 2 s.
- **A row from a previous boot is still foreign.** The instantiation epoch part of the fingerprint is compared
  exactly.
- Rows written before 0080 (no boot-time stamp) get the 2 s compare only. Linux behavior is unchanged apart from the
  2 s tolerance.

## How it works

- The dispatcher stamps each worker in `tasks.worker_started_at` with a fingerprint `"<epoch>|<start>"`, built by
  `_process_fingerprint` in `hermes_cli/kanban_db_dispatch.py`. `<start>` is the psutil start time in centiseconds
  (from `gateway.status.get_process_start_time`). On macOS that value is `kern.boottime` plus the process's uptime
  offset, so it moves when the kernel re-anchors boot time. On macOS `<epoch>` is empty; on Linux it is set and the
  start ticks are already boot-relative.
- `_pid_recycled` decides whether a live PID is the same process. Before these patches it compared the whole
  fingerprint string exactly.
- 0079: `_pid_recycled` splits the fingerprint on `|`. The epoch must match exactly. The start times are compared with
  `gateway.status.start_time_fingerprints_match`, which allows `START_TIME_DRIFT_TOLERANCE` (200 centiseconds, 2 s).
  The integer-fingerprint path for older rows uses the same comparator instead of `_start_times_agree`. If the current
  fingerprint can't be read, the PID counts as recycled.
- 0080: when the epoch is empty (macOS), `_process_fingerprint` appends `@<boottime>`, read by the new
  `_darwin_boottime_centis` via `sysctlbyname("kern.boottime")` through ctypes. It returns `None` on other
  platforms or on any error, and then no stamp is added. `_start_components` parses `"<start>[@<boot>]"`.
  `_pid_recycled` treats the process as the same when the absolute starts match within 2 s, or, when both sides
  carry a boot stamp, when `start - boot` matches within 2 s. Unparseable values fall back to an exact string
  compare.

## Tests

- `tests/hermes_cli/test_kanban_gateway_restart_liveness.py` (0079), with a 1.00 s start-time shift:
  - `test_gateway_restart_leaves_live_workers_running_and_unblocked`: three running cards stay running, no crash or
    block events, and a full `dispatch_once` tick spawns nothing.
  - `test_gateway_restart_still_reclaims_a_really_dead_worker`: control.
  - `test_pid_recycled_beyond_drift_tolerance_is_still_foreign`: control, with a 60 s shift.
- `tests/hermes_cli/test_kanban_worker_boottime_reanchor.py` (0080). A `macos_clock` fixture models the macOS
  fingerprint on any platform: it fakes an empty epoch and a boot time, and shifts start times and boot time
  together.
  - `test_fingerprint_carries_boottime_stamp`
  - `test_boottime_reanchor_beyond_tolerance_keeps_live_worker`: a 30 s re-anchor. No crash, no breaker, and
    `release_stale_claims` releases nothing.
  - `test_recycled_pid_is_still_foreign_after_reanchor` and `test_dead_worker_is_still_crashed_after_reanchor`:
    controls.
  - `test_restarted_dispatcher_in_another_process_adopts_live_worker`: a dispatcher in a fresh interpreter, under a
    new claimer, sees a live `sleep` worker as alive and spawns nothing. The card then completes with its run id.
- `tests/hermes_cli/test_kanban_worker_pid_fingerprint.py`: one assertion updated to strip the `@<boottime>` suffix.

## Applying

Apply 0079 then 0080. 0080 edits the code 0079 adds, so it does not apply alone. 0079 alone fixes the observed 1 s
case but not larger re-anchors. Both rely on `start_time_fingerprints_match` and `START_TIME_DRIFT_TOLERANCE` already
being in `gateway/status.py`.

## Gotchas

- Workers spawned before 0080 have no boot stamp. They only get the 2 s tolerance until they finish.
- A PID recycled within 2 s of the old worker's start would still be taken for the original. The patches accept this
  as very unlikely.
- If `sysctlbyname` fails, the fingerprint has no boot stamp and the 2 s compare applies.

## Plugin?

No. The fix is inside the dispatcher's private liveness check in `hermes_cli/kanban_db_dispatch.py`, and there is no
plugin hook for it.

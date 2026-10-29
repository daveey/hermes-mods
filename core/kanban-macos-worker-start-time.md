# Kanban keeps live workers running across clock shifts, hostname changes and restarts

Patches:

- `patches/0079-fix-kanban-tolerate-macOS-start-time-drift-in-worker.patch`: kanban dispatcher. A worker's
  recorded start time now matches its current start time within 2 s instead of exactly.
- `patches/0080-fix-kanban-compare-macOS-worker-start-times-boot-rel.patch`: kanban dispatcher. On macOS the
  worker fingerprint also records `kern.boottime`, and start times are compared relative to boot, so a clock shift of
  any size is not read as a crash.
- `patches/0117-fix-kanban-preserve-worker-ownership-and-retry-conte.patch`: kanban claims and retries. On macOS
  the claim lock is keyed to the boot session instead of the hostname, so a hostname change on wake or a network
  switch does not make live workers look foreign. A retried run gets a bounded, read-only tail of the previous
  run's transcript so it continues instead of starting over.

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
- **A hostname change does not orphan workers (0117, macOS).** macOS can rename the host on wake or a network
  change (`foo.local` → `foo-2.local`). Claims are now `boot-<kern.bootsessionuuid>:<pid>`, shared by every
  dispatcher on this boot and stable across gateway and Desktop restarts, so the dispatcher still treats its workers
  as local (it can check, reap and time them out) instead of leaving them unowned. A claim from another host or a
  previous boot never matches. Old hostname-style claims from the current hostname are still read as local. Other
  platforms keep the hostname prefix.
- **A retried run picks up where the last one stopped (0117).** When a card is re-run after a crash, a reclaim or an
  iteration-budget stop, the worker context ends with an "Interrupted worker continuation" section: the last
  assistant/tool messages (and compression summaries) of the previous run's own session, at most 24 messages and
  16,000 characters. The section says it is history, not instructions or proof of completion, and tells the worker to
  verify files and processes before repeating side effects. Nothing is resumed or replayed and the new run gets its
  own claim.

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

- 0117 ownership: new `hermes_cli/kanban_host_identity.py`. `claim_prefix()` returns `boot-<uuid>:` from
  `/usr/sbin/sysctl -n kern.bootsessionuuid` on macOS (cached; any error or a non-UUID → hostname prefix) and
  `<hostname>:` elsewhere. `kanban_db._claimer_id()` uses it. `is_local_claim(lock, prefix)` accepts the prefix, plus
  the current hostname prefix when the local prefix is a boot prefix (legacy claims). Every place in
  `kanban_db.py` / `kanban_db_dispatch.py` that compared `claim_lock.startswith(host_prefix)`
  (`release_stale_claims`, `_terminate_reclaimed_worker`, `reap_terminal_workers`, `enforce_max_runtime`,
  `_reclaim_dead_workers`, `adopt_worker_pid`) now calls `_claim_is_local`.
- 0117 retry context: new `hermes_cli/kanban_worker_recovery.py`. `run_agent.py` calls
  `register_worker_session(session_db, session_id)` when a `kanban`-source session is created (also after
  compression rotates the id). It records `(run_id, task_id, profile, state_db, session_id)` in a new
  `worker_recovery_sessions` table on the board, under `BEGIN IMMEDIATE`, only if the process is a
  dispatcher-owned worker whose env (`HERMES_KANBAN_DB/TASK/RUN_ID/CLAIM_LOCK`) matches the live, unexpired claim,
  the session is in this profile's own `state.db` with source `kanban`, and the run's profile matches. A session
  already bound to another run is never rebound. `build_worker_context` appends `build_recovery_context(conn,
  task_id)`: the latest *closed* run of this task whose registered profile is the run's and the assignee, read from
  its state DB read-only, with SQL-side row and field limits. Missing table, transcript or a retagged session →
  empty string (fail closed; never guessed from cwd, title or the task's origin chat).

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

- 0117 ownership, `tests/hermes_cli/test_kanban_host_identity.py`:
  - `test_boot_identity_survives_hostname_change_and_dispatcher_restart` (macOS-marked): a claim taken under
    `before.local` is still local under `after.local` with a fresh cache; a legacy current-hostname claim is local; a
    foreign hostname and a different boot UUID are not.
  - `test_unavailable_identity_uses_hostname` (macOS-marked): empty or non-UUID sysctl output → hostname prefix.
  - `test_hostname_change_does_not_reclaim_live_run`: `release_stale_claims` releases nothing and the run id is kept
    after the hostname changes.
- 0117 restarts, `tests/hermes_cli/test_kanban_restart_survival.py`:
  `test_worker_survives_entrypoint_restart[gateway|desktop]` spawns a real detached worker through `_default_spawn`,
  kills the parent's whole process group, and the worker still completes its card with its run id. The desktop case
  skips when no `node` is on PATH.
- 0117 retry context, `tests/hermes_cli/test_kanban_worker_recovery.py` (29 cases): crash and budget-exhaustion
  continuations survive a board reopen; an open run is not its own recovery; stale/foreign claims, mismatched env
  pins, unrelated or delegate sessions and a foreign state DB cannot register; registration is idempotent but never
  rebinds; board/task/profile isolation with no fallback to the task's origin session; a missing or retagged
  transcript yields nothing; the tail excludes system/user/reasoning/inactive rows and stays bounded; a real-schema
  lazy agent registration followed by reclaim; compression rotation uses the latest registered session.
- Untested: a real macOS wake that renames the host (covered only with a patched `gethostname`), and the worker
  actually acting on the continuation text (the tests check the context string, not model behavior).
- All three files pass (Oct 17, 2026 PT, `scripts/run_tests.sh`).

## Applying

Apply 0079 then 0080. 0080 edits the code 0079 adds, so it does not apply alone. 0079 alone fixes the observed 1 s
case but not larger re-anchors. Both rely on `start_time_fingerprints_match` and `START_TIME_DRIFT_TOLERANCE` already
being in `gateway/status.py`.

0117 is independent of 0079/0080 in code (different functions) but is the same failure family; apply it after them.
It edits `run_agent.py` and `build_worker_context`, so a rebase conflict usually lands there. 0121
([kanban-soft-deadline.md](kanban-soft-deadline.md)) puts its checkpoint above 0117's continuation tail and its tests
expect that tail. Verified: 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105, 0106, 0107, 0108, 0109, 0110, 0111, 0112, 0113, 0114, 0115, 0116, 0117, 0118, 0119, 0120, 0121, 0122, 0123, 0124, 0125, 0126, 0127 apply with `git am -3 --empty=keep` at merge-base `4957bbd0a0` (Oct 17, 2026 PT).

## Gotchas

- Workers spawned before 0080 have no boot stamp. They only get the 2 s tolerance until they finish.
- A PID recycled within 2 s of the old worker's start would still be taken for the original. The patches accept this
  as very unlikely.
- If `sysctlbyname` fails, the fingerprint has no boot stamp and the 2 s compare applies.
- 0117: after a reboot every old claim is foreign (new boot UUID). That is intended: the old PIDs are gone.
- 0117: registration is best-effort. A worker whose session was created before the patch, or whose env lacks the
  claim pins, gets no continuation tail on retry; it starts from the card body as before.
- 0117: the tail can include tool calls that never finished; it is labelled "execution not guaranteed".

## Plugin?

No. The fixes are inside the dispatcher's private liveness and claim-ownership checks and `build_worker_context`, and
there is no plugin hook for any of them.

# Bounded deleted-WAL holder scan on macOS: one pass in flight, cached clean verdicts

Patches:

- `patches/0565-perf-state-cache-clean-macOS-deleted-WAL-holder-scan.patch` (cache plus a first lock)
- `patches/0567-fix-state-one-deleted-WAL-holder-scan-in-flight-per-.patch` (shared pass, deadline, identity-keyed cache)
- `patches/0580-fix-state-darwin-holder-scan-caches-clean-verdicts-b.patch` (race fix: clean verdicts are cached before the pass leaves flight)

Before every `state.db` open, `refuse_deleted_wal_generation` checks that no process still holds a deleted
`-wal` / `-shm` generation. On macOS that check walks every pid's descriptors through libproc (`ctypes`). Each call
ran the walk on a new daemon thread and gave up on it after `_DARWIN_FD_SCAN_TIMEOUT_SECONDS` (5 s), failing open.
The walk is not kernel-blocked: uncontended it takes about 50 ms for about 11k descriptors. Under GIL pressure,
though, every `ctypes` call waits for the GIL, so walks timed out while their threads kept running. A Desktop
backend on 2026-10-27 had 4–5 abandoned `_scan` threads alive at once (py-spy), and `agent.log` showed
"deleted-WAL holder scan timed out" every few seconds. The main load in that incident was the notification bounce
fixed by [notification-held-handoff.md](notification-held-handoff.md). These patches stop the scan from adding to
it.

0565 cached clean verdicts for 30 s and put a lock around the walk. But the lock was released when the caller's
wait ran out while the thread kept running, so the next caller still started a second walk beside it. The cache
also hid a WAL generation retired inside the window. 0567 fixes both.

## Behavior

- At most one libproc walk runs per process. A caller that arrives while one is alive waits on it and takes its
  result instead of starting another thread.
- A caller that finds the in-flight walk already past its deadline fails open at once. That walk can only end
  expired, so waiting on it buys nothing.
- The walk checks its deadline (start + 5 s) on every descriptor and exits once past it, so a slow walk does not
  outlive the callers that gave up on it. A walk stuck inside one `proc_pidfdinfo` call still cannot be
  interrupted, but it is the only one, and it dies with the process.
- A clean (no-holder) verdict is reused for 30 s only while the database's directory mtime and the `-wal` / `-shm`
  identities (`st_dev`, `st_ino`) are unchanged. Any create, unlink or rename in the directory forces a fresh
  walk. WAL writes change none of these, so a busy database keeps its cache. Positive and timed-out verdicts are
  never cached.
- Fail open is unchanged: a timed-out or expired walk returns `[]`, and `refuse_deleted_wal_generation` never
  refuses because of a timeout.
- Linux (`/proc`) and Windows are unchanged.

## How it works

`_DarwinFdScan` is the shared walk. Its `run` keeps only `-wal` / `-shm` vnodes as `(pid, target, identity)`
candidates, so callers watching different databases can share one walk. It sets `expired` once past its deadline.
In `finally` it clears the module slot `_DARWIN_SCAN_IN_FLIGHT` (under `_DARWIN_SCAN_LOCK`) and sets `done`.
`_join_darwin_fd_scan` takes the lock and returns `None` at once if the slot holds a walk past its deadline. If
the slot is empty, it starts a walk (thread name `hermes-wal-holder-scan`). It counts itself in `waiters` and waits
on `done` for the timeout. If `Thread.start` fails, it clears the slot again.

`_iter_darwin_sidecar_holders` first takes `_darwin_clean_key(base)`: the directory's `(dev, ino, mtime_ns)` and
each sidecar's `(dev, ino)`, or `None` if missing. It returns `[]` if `_darwin_clean_until[base]` holds an
unexpired entry with the same key. Otherwise it joins the walk, filters the candidates by its own watched paths,
and runs `_identity_is_truly_unlinked` in its own thread. It caches `(expiry, key)` only on a clean result. The key
is taken before the walk, so a change during the walk makes the entry miss next time.

## Code

- `hermes_state_dbfile.py`: `_DarwinFdScan`, `_join_darwin_fd_scan`, `_iter_darwin_sidecar_holders`,
  `_darwin_clean_key`, `_darwin_clean_cached`, `_darwin_clean_until`, `_DARWIN_FD_CLEAN_TTL_SECONDS`,
  `_DARWIN_SCAN_IN_FLIGHT`, `_DARWIN_SCAN_LOCK`, `_DARWIN_FD_SCAN_THREAD_NAME`.

## Tests

- `tests/hermes_state/test_deleted_wal_generation_guard.py` (fake libproc enumeration, so these run on every platform)
  - `test_darwin_concurrent_callers_during_slow_scan_share_one_scan_thread`: 5 callers during a blocked walk, one
    scan thread, one enumeration, and all 5 get the walk's holder.
  - `test_darwin_scan_timeout_fails_open_and_starts_no_second_thread`: a blocked walk times out to `[]`. Later
    calls through `iter_deleted_sqlite_sidecar_holders` and `refuse_deleted_wal_generation` (platform darwin) fail
    open without waiting (well under their 30 s budget) and start no second thread.
  - `test_darwin_scan_stops_itself_past_its_deadline`: an endless walk exits on its own after the timeout, and the
    next caller runs a fresh one.
  - `test_darwin_clean_verdict_is_dropped_once_a_sidecar_is_unlinked`: reuse while unchanged, kept across a WAL
    append, and a fresh walk after the WAL is unlinked.
- The real-libproc macOS tests in the same file pass, including
  `test_iter_finds_self_after_wal_unlink_on_darwin`, which failed with 0565 alone (cached clean verdict).
- `tests/hermes_state/test_state_db_darwin_fd_scan_hang.py` (from 0565): hung-vnode fail-open within budget,
  healthy holder reported, clean verdict reused and single-flight across 8 threads, timed-out walk not cached. Its
  autouse fixture also resets `_DARWIN_SCAN_IN_FLIGHT`, because a hung fake walk never leaves the slot.

Untested: behavior under real GIL contention in a running Desktop backend. The tests drive the walk with a fake
enumeration.

## Race fix (0580, source `d563fb1463`)

A caller that missed the clean cache and then found no pass in flight started a second libproc pass: the finished
pass cleared `_DARWIN_SCAN_IN_FLIGHT` before any waiter had cached its verdict. Now each joiner registers its
`(key, watched)` in `scan.watchers`; the pass, still holding `_DARWIN_SCAN_LOCK`, records a clean verdict for every
registered waiter (only if it neither errored nor expired) and then clears the slot. `_join_darwin_fd_scan(base, key,
watched)` re-checks the cache under that lock and returns the `_DARWIN_SCAN_CACHED` sentinel when a verdict appeared.
`_darwin_holders` is the shared holder filter. Test: `tests/hermes_state/test_state_db_darwin_fd_scan_hang.py::
test_caller_arriving_after_pass_but_before_cache_write_reuses_it` (deterministic; the older single-flight test failed 1
in 3 to 2 in 8 runs before, 0 in 15 after). That file passes on the mirror host (68 passed across the three files
checked for 0578, 0579, 0580). Gotcha: a pass that errors or expires still caches nothing.

## Apply

`git am core/patches/0565-*.patch core/patches/0567-*.patch core/patches/0580-*.patch`. Takes effect when the backend or gateway restarts.

## Gotchas

- The deadline check sits in the consumer loop, so it only runs between yielded vnode descriptors. Pids with no
  vnode fds are walked without a check, which is cheap.
- `state.db` lives in `~/.hermes`, where other files are created and removed often, so the clean cache misses more
  than the 30 s TTL suggests. A plain TTL would be faster, but it is not sound: it hid a generation retired inside
  the window.
- `tests/hermes_state/test_hermes_state.py`, `test_state_db_maintenance_conformance.py`,
  `test_state_db_malformed_repair.py` and `test_state_db_repair_non_destructive.py` already fail on macOS on main
  before these patches (39 tests, e.g. `messages_fts already exists`). The counts are the same with them.

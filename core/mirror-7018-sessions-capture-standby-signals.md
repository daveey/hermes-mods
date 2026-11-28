# Sessions capture standby child and exit-signal dispatch

Patch coverage: 6948, 6949, 6950, 6951, 6952, 6953, 6954.

## Behavior

The sessions capture reader (`CaptureProcess`) runs a one-request worker child that renders the session frame. On the measured host the child needed seconds to reach its frame plus about 0.4 s of interpreter teardown that the parent waited for inside one 5 s budget. The series changes the live early-boot path (`early_sessions`) and the worker; budgets, request/response sizes, framing and "one active child, one frame, one snapshot per batch" are unchanged.

**Standby child (6948).**
- `early_sessions` now creates the `CaptureProcess` before importing the heavy session modules, calls `reader.prepare()` (spawning one standby child with the same command, env and `spawn_owned` custody) and only then imports `SessionsRuntime`/`web_routers.profiles`, handing the reader to the runtime (`capture_reader=reader`; that keyword is used by this diff but its definition is outside the series' diff, so I assume it pre-exists). Until the runtime owns the reader, one try/finally and the SIGTERM/SIGINT handler own it.
- `_request` takes the standby only if it is alive, at most `STANDBY_MAX_AGE_S` (30 s) old, and its checkout fingerprint (`read_git_revision_fingerprint`) still equals the current one; otherwise it is moved to retirement and a fresh child is spawned. A used standby serves exactly one request. `close()` retires the active child and every standby, attempts all, and raises the first failure.
- CPU is recorded per child as `sessions.capture.child` (lifetime CPU, so imports count; unused or retired standbys are recorded once as `skipped`). The one-time standby spawn wall is not charged to the duty-cycle batch in `sessions_native_source` (comment-only change there).
- The worker now does its reader imports (`preimport`) before blocking on stdin, under `redirect_stdout(sys.stderr)`. After flushing the frame it runs `released(baseline)`: join the annotation-refresh thread (0.2 s bound), clear the two profile scan caches under their locks, close retained kanban readers, run the profile-resources exit hook, stop the attestation settler, release process dependency leases (`runtime_state.release_process_leases`, new, mirrors the atexit hook), shut logging down and flush; then verify no extra non-daemon thread, no exit-time `weakref.finalize`, and no open fd beyond the start-up baseline plus known probe fds. Only if all of that holds does it `os._exit(0)`; any doubt, a non-main thread, or no `/proc/self/fd` falls back to the ordinary interpreter exit. `HERMES_SESSIONS_CAPTURE_FAST_EXIT=0` disables the fast exit.

**Custody and signals (6949-6954; final state).**
- `_lock` is re-entrant and never held across a spawn or a stop. Every child is in exactly one place: the preparing call, `_standby`, `_child`, or `_retiring` (unusable; leaves only after a verified stop). `prepare` publishes or moves to `_retiring` in one locked step; a stale standby found by a capture is stopped after that request (`_retire_quietly`; a stuck group stays in `_retiring` for `close()`). The caller contract is that `prepare` and `close` are serialized (both run on the main thread).
- `_ExitSignals` (module-level in `sessions_early_boot.py`) owns SIGTERM/SIGINT. The previous handler is recorded with `getsignal` before ours is installed. Each handler invocation is an ordered queue entry keyed by its frame; a handler that interrupts an outer invocation first queues the outer ones. Actions (`close_on_signal`, then the previous handler, or `SystemExit(128+signum)`) run only from depth 0, and `prepare()` and every `close` run inside `deferred()` so a signal arriving in them is queued, never run inside them. A close failure on the signal path is logged and the previous handler still runs; later queued entries are delivered even if an earlier action raises. A pending repeat of a signal collapses into its delivery. A finished invocation is never re-queued.
- `EarlySessions.close(blocking=...)` is locked: the signal path only tries the lock (a close already running on the lifespan's worker thread is left to finish and the previous handler chains on), while the lifespan close waits.
- Residual by the source's own docstring: order is CPython's handler order, not kernel arrival order; a previous handler that blocks keeps later signals queued; after an exit propagates, an empty queue is not guaranteed.

## How it works

`hermes_cli/sessions_capture_process.py` (`_Standby`, `prepare`, `_spawn`, `_retire`, `_retire_quietly`, `_request`, `close`, `_stop`), `sessions_capture_worker.py` (`preimport`, `_open_fds`, `released`, `_cleaned`, `_quiescent`), `sessions_early_boot.py` (`EarlySessions.close`, `_ExitSignals.handler/deferred/_dispatch/_deliver_queue/_act`, `close_on_signal`), `runtime_state.py` (`release_process_leases`).

## Code

`hermes_cli/sessions_capture_process.py`, `sessions_capture_worker.py`, `sessions_early_boot.py`, `runtime_state.py`, `sessions_native_source.py` (comment only).

## Tests

Read from source; **not executed here**; no fresh run and no native or end-to-end green claim. `tests/hermes_cli/test_sessions_capture_standby.py`, `test_sessions_capture_worker_exit.py`, `test_sessions_early_boot.py`, `test_sessions_exit_signals.py` (second signal injected at line/call/opcode boundaries via `sys.settrace`, plus real-signal cases) and `tests/pm/test_runtime_lease_cleanup.py`; they came out of several independent review rounds. Real signal delivery, the real fast-exit path and the measured timings on a production host are **untested** here.

## Applying

Apply in ordinal order; 6949-6954 are successive rewrites of the custody and signal model, so only the final state described above applies.

## Gotchas

- Do not call `prepare` and `close` concurrently from different threads; the contract serializes them.
- The fast exit is skipped on any doubt; the ordinary exit is then just slower.
- A handler invocation must mark itself finished before a nested signal can re-queue it.

## Why core, not a plugin?

Core serve/session capture code.

# Gateway SIGUSR2 stack dump (GIL-safe)

Patches: 1329, 1367.

## Behavior

`kill -USR2 <gateway pid>` appends every thread's Python stack to `<log_dir>/gateway_faulthandler.log` (header `--- SIGUSR2 stack dump <ts> pid=... ---`, footer `--- end SIGUSR2 stack dump ---`) and the gateway keeps running. The old `faulthandler.register(SIGUSR2, all_threads=True)` walked other threads' frames from C signal context without the GIL; a thread popping a frame mid-walk made it read freed memory, and a gateway segfaulted this way, losing in-flight Kanban runs.

Now SIGUSR2 gets a Python-level `signal.signal` handler that runs on the main thread with the GIL held. A second SIGUSR2 within 1 s of the last dump is ignored, and write errors are swallowed so the signal never terminates the gateway. `faulthandler.enable()` for fatal errors is unchanged, as is Windows (no SIGUSR2). 1367 only hardens the stress test.

## How it works

`ThreadStackDumper` (`gateway/sigusr2_stack_dump.py`) snapshots `sys._current_frames()`, names threads from `threading.enumerate()`, uses the interrupted frame for the current thread, lists the main thread first then by name, and writes with a raw `os.write` on the log fd (a buffered writer re-entered from a handler raises). It stamps the rate limit before dumping so a nested signal hits the limit. Startup unregisters any older faulthandler SIGUSR2 registration, then installs the handler. The user-guide messaging page gained a short note.

## Code

`gateway/sigusr2_stack_dump.py`, `gateway/run_startup.py`, `hermes_cli/stderr_timestamp.py` (comment only), `website/docs/user-guide/messaging/index.md`.

## Tests

`tests/gateway/test_sigusr2_stack_dump.py`:
- `test_setup_installs_python_handler_not_faulthandler_register`
- `test_sigusr2_storm_against_frame_churning_threads_never_crashes` (300 signals ~5 ms apart vs 150 churning threads; child exits 0, at most 10 dumps, last dump names every live thread). 1367 adds `time.sleep(0)` in the churn generator so the contenders cannot starve the control thread on throttled runners, and asserts the dump contains `in recurse` and `in gen` frames.
- `test_two_sigusr2_one_ms_apart_produce_one_dump`
- `test_rate_limit_ignores_second_dump_within_interval_and_allows_after` (fake clock)

Untested: Windows.

## Applying

Apply 1329 then 1367. No config.

## Gotchas

The dump runs only when the main thread next executes bytecode; a main thread stuck in a long GIL-holding C call delays it. Fatal-error and shutdown-watchdog dumps are separate and unchanged. No plugin involved.

## Follow-up patch (1651): the native live-test fixture dumps stacks the same way

**1651** (`7c03df5831`, `test(gateway): native live fixture dumps stacks via refcounted frames, not faulthandler
watchdog`). Test harness only.

- **Behavior.** `test_native_gateway_runtime_live` exited -11 on a CI runner. Its fixture `tests/gateway/fixtures/stack_dump_daemon.py`
  armed `faulthandler.dump_traceback_later(repeat=True)`, whose native watchdog walks interpreter frames without the GIL,
  the same hazard 1329 removed from SIGUSR2; a thread retiring a code object mid-walk segfaults the gateway (the old
  fixture crashed in 6 of 6 churn runs). The fixture now runs an `Event`-driven Python thread that every
  `UGW_STACK_DUMP_AFTER` seconds snapshots `sys._current_frames()` (refcounted frames, like `gateway/sigusr2_stack_dump.py`)
  and appends `=== threads @ <ts> ===` / `--- Thread 0x.. ---` stacks to `logs/stacks.txt`; it is stopped and joined
  when `gateway.run` returns. The live harness sets `PYTHONFAULTHANDLER=1` so a future native crash prints a backtrace.
- **Tests** (new `tests/gateway/test_stack_dump_daemon.py`, runs the real fixture in a subprocess with a 0.1 ms interval
  against four threads churning dynamically compiled code; not rerun by this mirror pass):
  `test_stack_dump_survives_retiring_code_objects`, `test_stack_dump_records_thread_stacks` (also asserts the dumper
  thread does not outlive the fixture).
- **Gotchas.** A dump now needs the GIL, so a gateway stuck in a GIL-holding C call produces no dump until it releases it.

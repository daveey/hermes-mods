# `hermes gateway profile`: bounded on-demand thread profiler for the live gateway

Patches: 1694 1695 1696

- `patches/1694-daveey-Add-bounded-gateway-thread-profiler-and-CLI.patch`: the `profile` control verb, the sampler,
  response trimming, the CLI and a benchmark script.
- `patches/1695-daveey-Batch-stable-profiler-snapshots-and-decouple-.patch`: snapshot fast path (batched identical
  ticks) and native CPU reads decoupled to a 0.1 s refresh.
- `patches/1696-daveey-Keep-stable-profiler-histograms-batched-acros.patch`: a CPU refresh no longer flushes an
  unchanged stack run.

Related: [gateway-control-diagnostics.md](gateway-control-diagnostics.md) (1524–1526) covers the same control socket's
slow-request and blocked-loop diagnostics. The upstream-facing developer page is
`website/docs/developer-guide/gateway-profiling.md` (added by these patches; it carries the full measurement history).

What you see: `hermes gateway profile [--seconds 20] [--hz 100] [--top 40] [--json]` samples the **running gateway
process** over its local control socket and prints, per Python thread, native CPU seconds and the most frequent
self/inclusive frames. It does not restart or signal the gateway, need root, or install anything.

## Behavior

- **Control verb `profile`** (`params`: `seconds` 1–60, default 20; `hz` 10–200, default 100; `top` positive int,
  default 40). Booleans, numeric strings, out-of-range and non-finite values are rejected with `ValueError`. Non-object
  `params` (`null`, list, string) is rejected. Only one profile runs per process; a second request fails immediately
  with `a profile is already running`, and the lock is released when the first one finishes.
- **Result:** `seconds`, `hz`, `cpu_refresh_s` (0.1, from 1695), `samples`, and `threads`. Each thread row has
  `name`, `native_id`, `cpu_s`, `cpu_partial`, `cpu_last_sample` (1695), `samples`, `first_seen_sample`,
  `last_seen_sample`, `top_self` and `top_inclusive` (lists of `[frame_key, count]`). Rows are sorted by `cpu_s`.
- **Frame keys** are `co_filename:co_firstlineno:co_name` (the definition line, not the executing line). No locals,
  source lines or argument values are read. A recursive frame counts once per inclusive sample.
- **Stack counts are wall samples, not CPU attribution.** CPU is measured separately from the native per-thread clock:
  Linux `clock_gettime((~tid << 3) | 6)` (kernel thread CPU clock by numeric TID); macOS own-process
  `proc_pidinfo(PROC_PIDTHREADID64INFO = 15)` loaded via `ctypes.PyDLL`; other platforms report `null`.
- **CPU reads (1695):** at a thread's first observation, then at most every 0.1 s on refresh ticks, and once at window
  end. A late tick takes one refresh and does not replay missed ones. A read whose thread is dead afterwards is
  discarded (on Linux the TID may already belong to another thread). If the first read fails and a later one succeeds,
  or the end read is missing (exited thread), the value is a lower bound marked `cpu_partial: true`. Unavailable CPU
  is `null`, never zero.
- **Lifetimes:** rows are keyed by `Thread` object plus native ID, so reused names or idents stay separate rows. On a
  full tick, threads are enumerated before and after `sys._current_frames()`. An ident is accepted only if both
  enumerations show the same `Thread`; an ident reused mid-snapshot is attributed to neither lifetime for that tick.
- **Fast path (1695, 1696):** when the whole snapshot is identical (same frame objects, no generator / coroutine /
  async-generator frame anywhere in any chain, `greenlet` not imported), the tick only extends a run counter that is
  added to every row when the snapshot changes or the window ends. From 1696, a CPU-refresh tick whose registry
  matches on both sides of the sandwich keeps that run open and just refreshes the cached rows' CPU.
- **Response size:** responses stay under the control socket's response cap. Oversized results omit whole threads or
  whole frame entries (never rewriting names or keys) and add `truncated`, `omitted_threads`, `omitted_frames`. The
  request id and envelope are counted in the budget.
- **CLI:** validates parameters locally (exit 2 on invalid), queries with timeout = seconds + 10, exits 1 with
  `gateway profile: no result (...)` when the gateway is unavailable, unsupported or already profiling. Text output
  labels the frame tables "wall samples, not CPU attribution" and marks partial CPU rows.
- **No frame references survive** the window, including through an exception traceback (1695 clears callee frames
  before re-raising).

## How it works

- `gateway/control_profile.py` (new): `validate_params`, `profile` (process-wide non-blocking lock; runs
  `_collect` on a daemon `gateway-profiler` thread while one control worker waits, so other verbs keep answering),
  `_collect` and the `_Window` state (1695), `_cpu_seconds` / `_read_cpu`, and `trim_response`.
- `gateway/control_socket.py`: registers `"profile"` in the default handlers, rejects non-dict `params` for this verb,
  and calls `trim_response` when the encoded profile response would reach `_MAX_RESPONSE_BYTES`.
- `hermes_cli/gateway_profile.py` (new) renders the result; `hermes_cli/subcommands/gateway.py` adds the parser;
  `hermes_cli/gateway.py` maps `profile` in `_GATEWAY_SUBCOMMANDS`.
- `scripts/benchmark_gateway_profile.py` (new) measures whole-process CPU with and without profiling in an isolated
  process with N idle threads; it never contacts a live gateway.

## Code

`gateway/control_profile.py`, `gateway/control_socket.py`, `hermes_cli/gateway_profile.py`, `hermes_cli/gateway.py`,
`hermes_cli/subcommands/gateway.py`, `scripts/benchmark_gateway_profile.py`,
`website/docs/developer-guide/gateway-profiling.md`.

## Tests

`tests/gateway/test_control_profile.py` (1694):

- `test_invalid_profile_parameters` (14 cases), `test_profile_rejects_nonobject_params` (`None`, `[]`, `'bad'`),
  `test_parameter_defaults_and_boundaries`.
- `test_process_wide_concurrent_refusal_and_lock_release`: a second request is refused in under 0.5 s and a third
  succeeds after the first ends.
- `test_profile_verb_reports_busy_thread_self_time_and_cpu` (Linux): a real spinning thread is the top self frame
  with ≥ 25 samples, `cpu_s > 0`, correct `native_id` and the request id echoed.

`tests/gateway/test_control_profile_bounds.py::test_profile_trims_whole_envelope_without_corrupting_shape`: a giant
thread name and frame key plus a long request id → under the cap, giant row omitted, `omitted_threads == 1`,
`omitted_frames == 4`, shapes intact.

`tests/gateway/test_control_profile_lifetimes.py` (1694):

- `test_short_lived_and_sleeping_threads_retained` (Linux, macOS), `test_same_name_successive_thread_lifetimes_remain_separate`
  (Linux, macOS), `test_macos_cpu_reader_tracks_current_thread` (macOS only).
- `test_darwin_reader_queries_thread_id64_flavor` and `test_darwin_reader_holds_gil_with_pydll`: run on every platform
  with a fake `proc_pidinfo` / `ctypes` loader to pin the macOS ABI (flavor 15, 112-byte struct, `PyDLL`).

`tests/hermes_cli/test_gateway_profile.py` (1694):

- `test_cli_profile_and_socket_remain_responsive` (POSIX): the real CLI against a real temporary control socket, JSON
  and text output; `identify`/`status` each answer in under 0.5 s while the profile runs.
- `test_cli_profile_timeout_exceeds_duration`: the query timeout is greater than `--seconds 60`.

`tests/gateway/test_control_profile_fastpath.py` (1695, extended by 1696), driven by a fake clock, frame snapshot and
thread registry:

- `test_identical_safe_snapshots_batch_exact_counts_and_skip_per_thread_work`, `test_ancestor_generator_caller_change_is_rewalked`,
  `test_greenlet_present_forces_rewalk_of_plain_chain`, `test_cpu_cadence_without_catchup_and_exit_lower_bound`,
  `test_failed_first_cpu_read_recovers_but_is_partial`, `test_enumerate_sandwich_rejects_reused_ident`,
  `test_no_frame_references_survive_success_or_error`, `test_real_idle_threads_take_batched_ticks` (Linux, macOS; real
  threads).
- 1696: `test_cpu_refresh_keeps_stable_snapshot_run_open` (flushes are exactly `[0, 99]`),
  `test_refresh_registry_change_does_not_reuse_old_lifetime`, `test_refresh_registry_sandwich_checks_before_as_well_as_after`.

The mirror pass did not rerun these tests. The developer page in the patch reports the author's runs (final:
38 passed, 1 macOS-only skipped across the five profiler files) and that the macOS-only tests ran under a stdlib
pytest shim on a Mac, not real pytest.

Untested / measured only:

- Overhead is benchmarked, not asserted by any test. The patch reports medians for 150 idle threads at 100 Hz of about
  0.69% of one core on Linux and about 2.7–3.0% on a shared Apple Silicon Mac after 1696 (the macOS figure had been
  about 15–16% before 1695). Deep or fast-changing stacks and more threads were not measured.
- No test profiles a real running gateway daemon; the CLI test uses a temporary control server in the test process.
- Windows: the control-socket named-pipe path for `profile` and the `null` CPU branch on non-Linux/non-macOS hosts.

## Applying

Needs the gateway control socket (`gateway/control_socket.py` with `verb_handlers`, `_MAX_RESPONSE_BYTES`,
`query_gateway_control` accepting `params`) and the `hermes gateway` subcommand parser split
(`hermes_cli/subcommands/gateway.py`). Apply 1694, 1695, 1696 in order; 1695 and 1696 rewrite `_collect` / `_Window`
from the previous patch.

## Gotchas

- File and thread names in the output can themselves be sensitive; treat the output as local diagnostic data.
- A thread that starts and exits between two samples is not seen; work before a thread's first observation and after
  its last readable CPU read is not counted.
- Purely native threads without a Python `threading.Thread` are not reported.
- Sampling depends on the GIL; busy threads or native extensions can delay ticks, so check the actual `samples` count.
- While a profile runs, it occupies one control-socket worker for its whole duration.

## Plugin?

No. The profiler is a control-socket verb inside the gateway process and a `hermes gateway` subcommand; plugins cannot
add control verbs or read other threads' frames from inside the gateway.

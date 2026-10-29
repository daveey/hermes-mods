# Gateway exit records carry a level, fewer false errors, and the gateway scan finds spaced interpreters

Patches:

- `patches/0074-fix-gateway-exit-diag-records-carry-a-level-Slack-li.patch`: gateway. Every record in
  `logs/gateway-exit-diag.log` gets a `level`. A Slack channel-list failure logs one line instead of two. A websocket
  that closes while its keepalive waits for a pong no longer logs an asyncio ERROR.
- `patches/0075-fix-gateway-exit-diag-grades-a-non-zero-start_gatewa.patch`: gateway. A non-zero return from
  `start_gateway` is graded `warning`, not `error`, because a normal service stop also returns non-zero.
- `patches/0081-fix-gateway-keep-argv-boundaries-in-the-gateway-proc.patch`: CLI. The gateway process scan keeps
  argv boundaries, so a gateway started by an interpreter whose path has a space in it is found again.

What you see: if a log shipper or alerting tool reads your Hermes logs, routine events stop showing up as errors.
Stock Hermes wrote exit-diag records with no level, so a tool that grades JSON lines by their words filed every
supervised restart (exit 75 after `hermes update` or `/restart`) as an error, because the record holds a traceback.
A Slack `invalid_auth` warning was split across two log lines, and the second line was filed as its own error. Each
Desktop disconnect over an SSH tunnel logged "ConnectionClosedOK exception in shielded future" at ERROR. Separately
(0081), `hermes update` restarts a launchd gateway whose interpreter lives under a path like
`~/Applications/Hermes Python.app/...`. Before, the scan did not see that gateway, so it was never restarted.

## Behavior

### Exit-diag levels (0074 + 0075)

Each JSON line in `logs/gateway-exit-diag.log` now has `"level"` next to `"tag"`:

| Tag                              | Level                                                                       |
|----------------------------------|-----------------------------------------------------------------------------|
| `gateway.start`                  | `info`                                                                      |
| `asyncio.run.returned`           | `info`                                                                      |
| `asyncio.run.KeyboardInterrupt`  | `info` (default; not named in the patch)                                    |
| `asyncio.run.SystemExit`         | `info` when `code` is `None`, `0` or `75`; `error` for any other code       |
| `asyncio.run.exception`          | `error`                                                                     |
| `gateway.exit_nonzero`           | `warning` (was `error` in 0074; 0075 lowers it)                             |
| `gateway.exit_clean`             | `info`                                                                      |
| `atexit.hook`                    | `info`                                                                      |
| any other tag                    | `info`                                                                      |

- `75` is `GATEWAY_SERVICE_RESTART_EXIT_CODE` from `gateway/restart.py`, the exit code a supervised gateway uses to
  ask launchd/systemd for a restart.
- `gateway.exit_nonzero` is a warning because a SIGTERM stop (`launchctl bootout`, `systemctl stop`) also makes
  `start_gateway` return False. Only an escaped exception or a failing `SystemExit` code is an error.
- `HERMES_GATEWAY_EXIT_DIAG=0` still turns the log off.
- A failed write to the diag log is now logged at debug level. Before, it was silently dropped. It still never
  raises.

### Slack channel-list failure (0074)

- When `users.conversations` raises in `gateway/channel_directory.py::_slack_team_channels`, the warning is one
  line. With a Slack error code, the detail is `users.conversations failed: <code>`. Without one, the exception text
  has its whitespace and newlines collapsed to single spaces.

### Websocket keepalive close (0074)

- The asyncio loop filter in `tui_gateway/loop_noise.py` drops "... exception in shielded future" when the exception
  is a `websockets` `ConnectionClosed`. It logs a debug line instead: `ws closed while keepalive awaited a pong
  (suppressed)`.
- Any other exception behind a shield, and a `ConnectionClosed` outside a shield, still go to the previous handler.

### Gateway process scan (0081)

- `find_gateway_pids(all_profiles=True)` finds a gateway run as `<path with a space>/python3.14 -I -c "import sys,
  runpy; ...; sys.argv = [... 'gateway', 'run', '--external-supervisor']; runpy.run_path(...)"`. Before, it
  returned `[]` for that process, so `hermes update` did not restart it through the manual/external-supervisor path.
- An arbitrary `-c` program that only passes `gateway run` as its arguments is still not a gateway.

## How it works

- 0074 moves `_make_exit_diag` out of `hermes_cli/gateway.py` into a new module, `hermes_cli/gateway_exit_diag.py`,
  as `make_exit_diag()`. `run_gateway` imports it from there. `_record_level(tag, extra)` picks the level. 0074 used
  an `_ERROR_TAGS` set; 0075 replaces it with a `_TAG_LEVELS` dict (`asyncio.run.exception` → `error`,
  `gateway.exit_nonzero` → `warning`). `asyncio.run.SystemExit` checks `extra["code"]` against `None`, `0` and
  `GATEWAY_SERVICE_RESTART_EXIT_CODE`. Everything else is `info`. The unused `from datetime import UTC` import in
  `hermes_cli/gateway.py` is removed.
- Slack: `SlackApiError.__str__` is two lines ("The request to the Slack API failed. ..." then "The server responded
  with: {...}"). Logged as-is, the second line has no timestamp or level, so a line-based shipper treats it as a new
  record. The fix builds the `detail` passed to `_report_slack_failure` from `_slack_api_error_code(e)`, or from
  `" ".join(str(e).split())`.
- Keepalive: websockets' legacy protocol has `ping()` return `asyncio.shield(pong_waiter)`. On close,
  `close_connection` cancels the keepalive task first, and then `abort_pings` fails the inner waiter with
  `ConnectionClosed`. asyncio logs that as an exception in a shielded future. The new
  `_is_closed_websocket_in_shield(context)` checks the message text and the exception type. It returns False when
  `websockets` is not installed. `install_loop_noise_filter` checks it after the existing `_is_benign_teardown`.
- Scan: `ps -o command=` and a `/proc/<pid>/cmdline` read with NUL replaced by space both join argv with spaces. A
  path like `.../Hermes Python.app/...` then becomes two tokens, and the inline-source walk in the matcher stops at
  the second half. 0081 adds `argv_to_command_line(parts)` to `gateway/status.py`. It double-quotes any arg that has
  whitespace and no double quote, and drops empty parts (the trailing NUL). `_read_process_cmdline` uses it for both
  the `/proc` and the psutil paths. The psutil path already had the same quoting inline, which the helper replaces.
  `hermes_cli/gateway.py::_iter_proc_cmdlines` uses it too. In `_scan_gateway_pids`, `_consider` now has a fallback:
  if a `ps` row fails the matcher, contains `gateway` (case-insensitive), and its pid is not excluded, it re-reads
  the exact argv with `_read_process_cmdline(pid)`. If that differs and matches, the row counts and the exact
  command line is used for the profile check. Only candidate rows pay for the extra read.

## Tests

- `tests/hermes_cli/test_gateway_exit_diag.py` (0074, updated in 0075):
  - `test_planned_system_exit_is_info` for codes `None`, `0`, `75`.
  - `test_failing_system_exit_is_error` for codes `1`, `2`, `"fatal"`.
  - `test_lifecycle_records_carry_their_level` for the start, return, clean exit, atexit, non-zero (`warning` after
    0075) and exception tags.
  - `test_opt_out_writes_nothing` with `HERMES_GATEWAY_EXIT_DIAG=0`.
- `tests/gateway/test_channel_directory_slack_errors.py::test_slack_api_error_is_logged_on_one_line`: a fake
  `SlackApiError` with a two-line message gives exactly one warning, with no newline, naming `invalid_auth` and the
  team id.
- `tests/tui_gateway/test_tui_gateway_loop_noise_ws_keepalive.py`: replays the keepalive sequence on a real loop.
  `test_closed_websocket_in_shielded_keepalive_is_not_forwarded`. Controls:
  `test_other_exception_in_shielded_future_still_forwarded` and
  `test_closed_websocket_outside_a_shield_still_forwarded`. Skipped if `websockets` is not installed.
- `tests/hermes_cli/test_gateway_scan_spaced_interpreter.py` (0081):
  - `test_exact_argv_of_runpy_bootstrap_under_spaced_interpreter_is_a_gateway`
  - `test_argv_to_command_line_drops_trailing_nul_part_and_keeps_plain_argv`
  - `test_non_gateway_inline_source_under_spaced_interpreter_stays_rejected` (control)
  - `test_ps_scan_rereads_exact_argv_for_space_split_gateway_rows` (linux, macos)
  - `test_ps_scan_does_not_reread_rows_without_gateway_token` (linux, macos; cost control)
  - `test_proc_scan_keeps_argv_boundaries` (linux)

## Applying

Apply 0074 before 0075: 0075 edits `hermes_cli/gateway_exit_diag.py`, which 0074 creates. 0081 is independent of
both in behavior. It touches `hermes_cli/gateway.py` too, but in different functions (`_iter_proc_cmdlines`,
`_scan_gateway_pids`). Its `gateway/status.py` hunk replaces an inline psutil quoting block marked `[daveey]`, so it
needs patch 0027 (`gateway status: quote spaced argv`), which added that block. Without 0027, that hunk will not
apply cleanly.

## Gotchas

- Levels only help if your log shipper reads the `level` field. A shipper that still greps for words like
  "Traceback" will keep flagging `asyncio.run.SystemExit` records, which still carry the traceback.
- `gateway.exit_nonzero` as `warning` means a real non-zero return that is not a stop is also only a warning. A
  crash shows up as `asyncio.run.exception` or a failing `SystemExit` instead.
- The keepalive filter matches on asyncio's message text ("exception in shielded future"). If asyncio changes that
  wording, the noise comes back.
- The scan fallback runs one extra argv read per `ps` row that contains `gateway` and fails the matcher. That is
  cheap on a normal machine but not free.

## Plugin?

No. The exit-diag recorder is called from `run_gateway` before any plugin loads and after they unload, the Slack
directory and the asyncio loop filter have no plugin hooks, and the gateway process scan is used by `hermes update`
itself, which runs outside the plugin system.

## Follow-up patches (0611, 0629)

**0611** (`7784b69330`): `start_gateway()` returns False for a SIGTERM with no planned-stop marker (launchctl bootout, kickstart -k, systemctl stop); `run_gateway()` used to write `gateway.exit_nonzero` with no signal or parent, so supervisor bounces looked like crashes (defect gateway-3cf2256311). The signal handler now records signal + parent on the runner, `run_shutdown.last_signal_exit()` exposes it, and `run_gateway` logs `gateway.exit_signal` (info). Exit code stays 1. Tests: `tests/hermes_cli/test_gateway_exit_signal.py`, `test_gateway_exit_diag.py`.

**0629** (`002087476f`): a refused start (live duplicate `already_running`, lost runtime-lock/PID-file race `pid_claim`, host owner refusing a second gateway `host_refused`) logs `gateway.start_refused` with reason and owner pid/liveness instead of a bare `exit_nonzero` (defect gateway-6efc15a270). `gateway/run_exit_reason.py` records the reason; `gateway_exit_diag.nonzero_exit_record()` picks exit_signal / start_refused / exit_nonzero. Exit codes unchanged. Test: `tests/hermes_cli/test_gateway_start_refused.py`.

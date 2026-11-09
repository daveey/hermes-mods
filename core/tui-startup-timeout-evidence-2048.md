# TUI startup timeout process evidence

Patch **2052**.

## Behavior

PTY and tmux TUI test startup failures include the captured screen and owned session process commands when waiting for raw mode times out.

## How it works

`PtyHermes.wait_ready` catches the polling assertion and appends `dump()` plus a PID-to-command map from `session_members(self.sid)` and `cmdline`. `TmuxTui.wait_raw` does the same using `pane_pid`. Both preserve the original deadline message and suppress chained exception noise.

## Code

- `tests/e2e/core/terminal/_pty.py`
- `tests/e2e/core/tui_pty/_helpers.py`

## Tests

**Not rerun this pass.** `tests/e2e/core/terminal/test_pty_process_lifecycle.py::test_startup_timeout_reports_screen_and_owned_processes` is parameterized over **pty and tmux**, injects a polling timeout and checks the deadline, screen and both process commands. It tests formatting/collection wiring with mocks, not a newly exercised real TUI startup timeout.

## Applying

Apply 2052 within [the core series](README.md), retaining the existing PTY/tmux lifecycle helpers.

## Gotchas

This is diagnostic improvement, not a larger startup timeout or a fix to an application startup hang. The process inventory is session-owned, not a machine-wide dump.

## Plugin eligibility

Not applicable: the change belongs to test fixtures, not the production plugin surface.

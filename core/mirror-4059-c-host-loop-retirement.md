# Retire host loops before a replaced backend finishes draining

Mirrored entries: 3840.

## Behavior

A superseded backend releases all of its host-loop leases immediately and starts no new automation wake while existing HTTP connections drain. A backend with confirmed code skew also retires its host loops. If the supersede is aborted or skew clears, only that retirement reason is removed.

A build failure from stale code does not reserve a long quota-reset delay for replay or close-out delivery; current-code failures retain the existing reset behavior.

## How it works

`host_loop_lease` records keyed retirement reasons under a lock, releases registered leases on first retirement, and refuses both `held()` and `exclusive()` while any reason remains. A handoff that acquires a lock after retirement gives it back. This refusal overrides the lease kill switch.

`StaleCodeLoops` requires two consecutive skew observations; the superseded watchdog rate-limits skew reads and retires immediately on first supersede. `kanban_unloaded.run_pass` returns `retired`, and the session wake entry refuses before resume/build. Replay logic moves into `automation_replay.replay_failed_wake`; `trusted_resets_at` ignores the reset from a retired or code-skewed process.

## Code

- `hermes_cli/host_loop_lease.py`
- `hermes_cli/web_server_skew_exit.py`
- `tui_gateway/automation_replay.py`
- `tui_gateway/credential_failure.py`
- `tui_gateway/kanban_unloaded.py`
- `tui_gateway/notice_guarantee.py`
- `tui_gateway/session_notifications.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/hermes_cli/test_superseded_host_loops.py`: Checks real flock release and reacquisition, multiple retirement reasons, kill-switch refusal, supersede while a busy fence still drains, aborted-roll resumption, two-read skew confirmation versus a single blip, retired-pass/wake refusal before resume, and short replay/close-out delays only for stale code.
- `tests/hermes_cli/conftest.py`: Adds teardown of process-global retirement reasons so one watchdog test cannot contaminate another; this is test isolation rather than an additional behavior assertion.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

Retirement is process-local and does not terminate already open connections or an already running turn. The watchdog’s best-effort lease-module import logs errors; it is not an OS-level execution fence. The tests require POSIX flock and skip without it.

## Plugin?

No. Lease ownership, watchdogs and wake admission need a shared core retirement signal.

# Recovery probe waits for dispatcher admission

Patch 1375; source `93843e3a493cdd647b59f65d6a29967801feb621`.

## Behavior

The recovery integration fixture retries only uneventful admission deferral and exercises a deliberately contended restart case. A failed or guarded spawn is still an assertion failure.

## How it works

dispatch_started validates the precise empty DispatchResult plus ready/no-run state; wait_until bounds recovery claim/spawn at 50 seconds. The retry websocket allows 120 seconds for opening.

## Code

tests/gateway/fixtures/kanban_recovery_probe.py; tests/gateway/test_kanban_recovery.py

## Tests

Source regression inventory: tests/gateway/test_kanban_recovery.py::test_owner_board_recovery: custom, cross_profile, restart and restart-contention.

These runtime suites were not rerun in this mirror pass. Executed mirror inventory/replay/tree checks are recorded in [mirror-1375-verification.md](mirror-1375-verification.md); they do not certify runtime behavior.

## Applying

Apply the complete snapshot using the [README replay recipe](README.md#applying), not this patch independently.

## Gotchas

This patch changes tests only. Linux strace is required; it is not a production dispatcher policy change.

## Plugin?

This patch mirrors the source implementation; no separate public mod installation is required.

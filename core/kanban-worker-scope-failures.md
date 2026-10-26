# Diagnose worker OOM and signal deaths

Patches: 0267.

## Behavior

Linux systemd scope OOM or signal evidence outranks a misleading worker clean-exit trailer. Retry context reports the cause, memory cap/peak when available, and an OOM remedy instead of a protocol violation.

## How it works

Failed scopes are retained until classification; systemd properties and run-qualified log snapshots provide evidence. Snapshots cannot cross task/run boundaries. Missing or successful scope evidence preserves existing clean-exit handling.

## Code

hermes_cli/kanban_worker_scope.py; hermes_cli/kanban_db_dispatch.py; hermes_cli/quiet_single_query.py

## Tests

tests/hermes_cli/test_kanban_worker_scope_failure.py has seven parameterized cases for OOM/signal precedence, retry context, argument retention, absent/success scopes and snapshot isolation. The implementation handoff reports a real 64 MiB OOM proof; that live stress test is not repeated by this mirror.

Test files above are shipped regression coverage, not a claim that every suite was rerun. See [mirror-verification.md](mirror-verification.md) for this export’s execution results.

## Applying

Apply the ordered core series through the last listed patch using `git am -3 --empty=keep`, starting at the fork/upstream merge-base; these are not independent patches.

## Gotchas

Scope diagnostics are Linux-specific. Kernel regressions do not prove Desktop rendering; no UI verification is claimed.

## Plugin?

This changes core-owned behavior or its test harness; no disk plugin is required.

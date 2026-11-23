# Isolated source fixtures and stable timing assertions

Source patch ordinals: 5750, 5807, 5813, 5816, 5826, 5829.

## Behavior

These test-only changes strengthen fixture realism/isolation and reduce dependence on unrelated wall-clock scheduling or fixed calendar dates. They do not change production lifecycle, signal policy, resource budgets or pacing policy.

## How it works

The replica bounded-read fixture exercises the real detached projector after closing its reader and observes raw transport before scalar consumption. Symbolic-home gateway/ACP suites opt into `authority_cwd_in_tmp`. Thread instrumentation overhead uses process CPU across parent/child threads instead of idle wall time. The synthetic signal worker periodically re-enters Python and covers helper-thread delivery while retaining its stuck non-daemon thread. Pacing fixtures pin the dated branch and use an active future hold instead of an expiring fixed epoch.

## Code

- `tests/tui_gateway/test_replica_bounded_reads.py`
- `tests/acp_adapter/test_gateway_submit_retry.py`
- `tests/gateway/test_bot_mailbox_cancellation.py`
- `tests/hermes_cli/test_loop_accounting_thread_targets.py`
- `tests/hermes_cli/test_signal_handler_kanban_worker.py`
- `tests/hermes_cli/test_kanban_pacing_queue.py`
- `tests/plugins/test_kanban_pacing_detail.py`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

Source assertions cover bounded replica transport with malformed-payload rejection after reader close; gateway/ACP submit/cancel/rearm/retirement fixtures in isolated cwd; the unchanged instrumentation CPU budget under an injected idle gap; process and helper-directed SIGTERM with the original termination bound; and CLI/live/replica pacing detail independent of calendar rollover. The symbolic-home markers touch seven gateway/ACP modules without changing their existing behavior assertions.

## Applying

Apply these fixtures at their corresponding source-series positions. Run them only through the normal project isolated test environment when later validating the replay.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

Fixture controls are not proof of the cause of a particular historical failure. CPU time is not elapsed latency. POSIX helper-signal coverage is skipped when unsupported. This mirror pass only reads the source tests and does not rerun reported historical results.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.

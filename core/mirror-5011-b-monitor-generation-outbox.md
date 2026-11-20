# Durable monitor-change delivery

Mirror entries: 4536.

## Behavior

- **4536** — Monitor observations remain pending in a durable per-job outbox until a successful agent run acknowledges that exact generation.

## How it works

After the source succeeds, monitor staging takes the jobs-store lock and appends changed observations behind the acknowledged snapshot. The oldest pending generation carries stable ID, old/new output, hash, first-run state and attempt count. Snapshot and queue persist in the same atomic jobs save, preventing detection from masquerading as handling. New observations chain behind pending ones, preserving their original diffs.

The owner run acknowledges immediately after a successful agent answer, before output saving or notification delivery. Only the head generation with that exact ID is consumed, so a delayed acknowledgment cannot drop a newer observation. Pre-agent quota refusal, blank/incomplete/interrupted results and failure-marked answers retain pending work; a silent successful answer is still success. Source failure stages nothing. Explicit discard is generation-bound and requires an audited reason.

Legacy hash-only state migrates only with a readable, hash-matching snapshot. Corrupt state, broken staging saves and missing jobs fail visibly rather than silently resetting the baseline.

## Code

- `cron/monitor.py`
- `cron/monitor_outbox.py`
- `cron/scheduler.py`

## Tests

Source tests cover quota failure followed by retry, unsuccessful/successful answer classes, restart and crash-after-stage persistence, multiple generations and stale acknowledgments, legacy/corrupt state, explicit discard, stable retry IDs/attempt logs, stale per-tick IDs and an external-worker path where notification delivery fails after successful handling. These source suites have not been rerun in this pass.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/agent/test_redact_telegram_boundary.py`
- `tests/cron/test_gated_tick_clears_gate_streak.py`
- `tests/cron/test_monitor_kind.py`
- `tests/cron/test_monitor_outbox.py`
- `tests/cron/test_monitor_outbox_worker.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

Delivery is at-least-once, not exactly-once across a crash after handling but before acknowledgment. Handlers should use the stable change ID for idempotence where needed. A notification failure after acknowledgment does not rerun an agent that already handled the change. Pending monitor state is persisted job data, not merely an output-file sidecar.

# Retryable aggregate worker memory setup

Mirror entries: 4466.

## Behavior

- **4466** — Aggregate memory setup retries transient systemd failures, reports bounded stderr and caches only successful configuration.

## How it works

The aggregate-scope helper attempts the existing user-systemd slice configuration three times, sleeping 1 then 3 seconds between failures. Each command retains its 10-second timeout and the existing `MemoryHigh=70%` / `MemoryMax=80%` settings.

Each failed attempt logs host identity, return code, attempt number and at most 500 decoded stderr characters. Byte stderr uses UTF-8 replacement decoding. OSError, timeout and subprocess errors participate in the same bounded failure path. Exhaustion raises `RestartSafeScopeUnavailable` instead of admitting a worker without the required aggregate budget.

## Code

- `hermes_cli/kanban_aggregate_scope.py`

## Tests

The source aggregate-scope suite adds transient second/third-attempt recovery, bytes/text stderr, truncation, host/return-code diagnostics, failure noncaching and OSError/timeout cases. These tests have not been newly run here; no systemd resource setting was changed.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/hermes_cli/test_kanban_aggregate_scope.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

Retry does not make systemd optional. Persistent failure remains fail-closed and will be retried by a later caller because failure is not cached. A successful setup is cached; diagnostics do not change the underlying resource policy.

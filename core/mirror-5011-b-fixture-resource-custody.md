# Deterministic process, socket and receipt fixtures

Mirror entries: 4469, 4476, 4480, 4500, 4501, 4547.

## Behavior

- **4469** — Update refusal tests select only update-run receipts, independent of sidecar JSON ordering.
- **4476** — Silent-resume scenarios await all replacement children before fixture homes/configuration change.
- **4480** — The fake transport atomically publishes its descendant PID only after initialization and owns teardown independently of that readiness file.
- **4500** — Proxy hold/EOF tests retain a bound nonlistening backend port until scenario cleanup.
- **4501** — Outbound-image fixtures bound local TCP buffering and join their listener thread before restoring socket construction.
- **4547** — The proxy hold-then-splice sibling test retains the same dead-port custody and uses a one-shot contender.

## How it works

Receipt fixtures filter the production `update_*.json` namespace and require exactly one receipt, then deliberately vary sidecar ordering. Child-process fixtures keep ownership until durable completion, including deterministic held-model tests that detect an unawaited extra child.

The transport PID fixture writes a closed pending file, installs its signal handler, then atomically replaces the ready filename. Teardown snapshots owned processes rather than reparsing a possibly incomplete file and preserves the original readiness exception. Independent process-group and exit assertions remain.

A published bind-and-close port is not a refused backend: a foreign listener can acquire it and correctly trigger proxy splice behavior. Both proxy scenarios instead keep a nonlistening socket bound, exercise a real competing listener and close/cancel/join their own resources. Image tests likewise establish their kernel-buffer premise with real socket readback instead of assuming a large response dwarfs TCP capacity.

## Code

These entries change regression fixtures only; production behavior is unchanged.

## Tests

All listed source changes are test fixtures/regressions. Coverage includes both receipt orders, held replacement children, paused PID publication and readiness failure cleanup, port takeover and uncontended proxy scenarios, aiohttp/HTTPX image paths, socket-buffer readback and thread cleanup. Production assertions, hold/cap bounds and retry policies are retained. None of these suites was freshly executed for this documentation pass.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/agent/transports/test_codex_app_server_runtime.py`
- `tests/gateway/test_outbound_image_download_cap.py`
- `tests/hermes_cli/test_always_on_rollover.py`
- `tests/hermes_cli/test_update_contract.py`
- `tests/tools/test_delegate_auto_resume_silent.py`
- `tests/tools/test_delegate_silent_fixture_lifetime.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

Fixture cleanup must not manufacture the EOF or process exit being asserted. Deterministic race reproductions prove the fixture flaw, not ownership of a historical competing listener or the exact scheduling of a prior CI failure. These entries do not change runtime behavior or justify broadening timeouts.

# Worker session-context read RPC (mirror 5526–5529)

## Behavior
- Dispatched workers read session context through authenticated `worker.sessionContext` on the existing owner transport, without queueing/acknowledging a durable journal entry or writing an owner receipt (the commit title and docs describe this as avoiding durable write receipts and sequence changes; no write-cost measurement is in the diff).
- The owner verifies live producer/adoption, current serving epoch, execution generation, status and readable lineage; assignment and context come from one read snapshot. Worker reads are serialized behind acknowledged writes and reject pending or failed journals.
- Bounded transport retries are fresh reads, never a write fallback. Only an explicit JSON-RPC `-32601` negotiates the old receipt path (rolling upgrades). The legacy read-lineage precheck is preserved, and owner fault codes survive exhausted reads.

## How it works
`agent/runtime_session_compression.py` (client path), `runtime_session_store.py`, `gateway/session_controls.py`, `gateway/session_worker.py`, `hermes_state_worker_observations.py`; the gateway contract (`apps/shared/src/gateway-contract.openrpc.json`, generated TS) and serve handler ledger are updated.

## Code
Files listed above plus `apps/shared/src/gateway-contract.*`, `tests/hermes_cli/serve_handler_ledger.json`.

## Tests
Source: `tests/gateway/test_worker_session_context_query.py` (by test names: fresh read without journal, receipt or revision writes; invalid scope; invalidated execution; new epoch and old serving connection refused; producer capability, principal and liveness required; lineage authorization and rotation; cannot overtake failed or pending writes; legacy fallback only for method-not-found; other owner errors do not downgrade; transport retry is a fresh read; single read transaction; authenticated wire contract; legacy refused target does not poison the store), `tests/agent/test_worker_session_context_rpc.py` (exact wire error code preserved; read holds the scope lock; a failed read never falls back to a write), `tests/worker_store_rpc.py` helper, and shared fixtures (`tests/hermes_state/conftest.py`, `test_runtime_worker_*`, `test_runtime_session_store.py`, `test_managed_class_fallback_sidecars.py`) touched without further named tests. Not executed in this mirror pass.

## Applying
Apply in order; clients and owners should be upgraded together or rely on the `-32601` downgrade.

## Gotchas
No latency or write-cost measurement appears in these patches.

## plugin?
No.

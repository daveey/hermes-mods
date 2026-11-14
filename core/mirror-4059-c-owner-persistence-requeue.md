# Requeue proven owner persistence failures without spending the card budget

Mirrored entries: 3844, 3894.

## Behavior

A bound worker’s exact terminal persistence failure can be charged to an unreachable owner rather than the task: the card returns to ready with an `owner_unavailable` run outcome and no consecutive-failure increment. This covers initial owner rediscovery failure and a final resend that still times out or loses its owner link.

The existing neutral-streak breaker still converts repeated owner stalls into a counted failure; unrelated worker/card failures remain counted.

## How it works

`kanban_owner_storage_refusal._proof` requires exit 1 and the complete `Worker failed: WorkerPersistenceError: <code>` envelope. Storage refusal and unreachable-owner codes map to distinct host-refusal kinds. Existing claim-lock/PID and authoritative-receipt selection checks remain in force.

`runtime_session_store.exhausted_resend_code` preserves a receipt-timeout code and maps owner-gone link failures to `owner_link_closed`. WebSocket close codes that may reject the particular frame (such as policy or size) keep their raw error text. The normalized code is latched by the worker store and exposed through the existing terminal receipt; the dispatcher does not infer owner fault from a substring in ordinary output.

## Code

- `hermes_cli/kanban_host_refusal.py`
- `hermes_cli/kanban_owner_storage_refusal.py`
- `hermes_cli/kanban_owner_unavailable.py`
- `agent/runtime_session_store.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/hermes_cli/test_kanban_owner_unreachable_midrun.py`: Checks ready status, zero failure charge, neutral run outcome and notice wording for exact bound owner faults; rejects inexact envelopes, later card faults, wrong PID/claim/exit code, and verifies the neutral-streak limit. Adds the same assertions for exhausted-resend timeout/link codes.
- `tests/agent/test_runtime_session_store_receipt_timeout.py`: Pins normalization of owner-gone errors, preservation of frame-specific close errors, exact exhausted-resend envelopes and failure latching, including recognition of that envelope by the dispatcher proof helper.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

A transport exception alone is not neutral proof. The bound worker’s final receipt and exact envelope are required. This does not promise that an unknown-outcome persistence write never committed; resend identity/custody remains the store’s responsibility.

## Plugin?

No. The wire failure code, persistence latch and dispatcher accounting must agree across core processes.

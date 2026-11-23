# Administrative stop-only protection and frozen courier custody

Patch coverage: **5859–5866**.

## Behavior

- `kanban_protect_stopped` lets an admitted administrative requester preserve an ended target under a stop without claiming provider attestation, execution authority, dependency success or completion of the caller run.
- A requester must be an existing direct parent or the exact current decision producer, never the target itself.
- Unsynced boards apply the local kernel; synced requests remain requested/pending until exact canonical proof is reconciled. Transport acknowledgement alone never confirms protection.
- A historical receipt after separate authorized renewal settles as recorded history, not proof of a current hold. Requester settlement preserves that distinction and outcome-specific details.

## How it works

The closed request schema binds source event/run uids, expected status/clock/episode, null current run, named owner, evidence, next step, recovery trigger and a fixed future deadline within 14 days. The local writer transaction checks the ended `failure_finalized` provider-refusal evidence and requester scope, preserves stronger restrictions, records `decision_protected_stopped`, and leaves the actor running. SQL triggers protect source/episode/restriction rows, runs, edges and target custody across reopened connections. Lazy null-to-uid bootstrap is allowed only without changing evidence coordinates; insert fencing prevents replacement of protected event rows and indexes both id/uid probes.

Synced operator calls bind the request to the actual caller run and freeze canonical envelope bytes plus digest in a durable command outbox. Capability negotiation precedes delivery. Exact request, source/run/target, home and receipt evidence must agree; unknown replies retain original bytes. Only enumerated exact semantic rejections settle refused. Rotating bounded reconciliation backs unresolved rows off in memory from 15 to 900 seconds. Confirmation pulls authenticated changes with observers and the home partition, then distinguishes `held_confirmed` from `recorded_hold_unconfirmed` after renewal. `recorded` settles courier administration as done without changing target execution semantics. Logs and requester results carry the same canonical detail.

## Code

- `hermes_cli/kanban_protect_{stopped,guards,evidence,transport,operator}.py`: local validation, immutable custody, canonical proof and reconciliation.
- `hermes_cli/kanban_operator_call.py`: special operator action, pending lifecycle and requester results.
- `tools/{kanban_protect_stopped_tools,kanban_stop_registrations,kanban_tools}.py`, `toolsets.py`, `agent/transports/hermes_tools_mcp_server.py`: registered tool and exposure.
- `hermes_cli/observability/schemas/hermes.shared_metrics.v4.schema.json`: tool enum (5862).
- `scripts/run_tests.sh`: forwards composed local Board URLs (5863).

## Tests

Source regression coverage listed below was inspected, **not executed in this mirror pass**. No live deployment or performance result is claimed.

- `tests/hermes_cli/test_kanban_protect_stopped_local.py`: local stop-only adoption, caller continuity, scope/source/CAS/active-run refusal, immutable edges/evidence, archived dependency blockers, lazy uid bootstrap and replacement/identity escape controls.
- `tests/hermes_cli/test_kanban_protect_tool.py`: actual registered local tool, closed operator fields, refusal/applied/recorded detail, integrity errors and unresolved-row backoff.
- `tests/hermes_cli/test_kanban_protect_producer_board.py`: composed registered synced tool through claim, push and home execution.
- `tests/hermes_cli/test_kanban_protect_stopped_board.py`: composed canonical proof corruption, same/foreign home, lost replies, reopen/replay identity, one terminal courier result and post-renewal recorded history.
- `tests/hermes_cli/test_kanban_protect_partition_board.py`: observer-aware home-partition pull does not leak foreign cards; same-home positive control.
- `tests/hermes_cli/test_kanban_protect_requester_board.py`: production requester settlement distinguishes applied/recorded and preserves full protection/detail.
- `tests/hermes_cli/test_kanban_protect_insert_cost.py`: indexed trigger query plan and insert-cost regression structure; no measured timing claimed here.
- `tests/hermes_cli/test_kanban_source_receipt_sites.py`: 5861 classifies local stop adoption as a non-running status exit. The metrics enum change adds no dedicated test in 5862.
- Composed Board tests require their service prerequisites. No direct MCP client invocation or live observability ingestion was exercised in this pass.

## Applying

Apply the covered patches in the full ordered core series using the [core applying recipe](README.md#applying), including its replay bridges and required repair overlay. These entries depend on earlier core changes; they are not advertised as an independent cherry-pick set. Reimplementation should preserve the invariants above and run the named source suites in a disposable source checkout.

## Gotchas

This is administrative adoption of historical evidence, not provider clearance or an execution-release API. Never regenerate a frozen request or extend its deadline to resolve delivery uncertainty. An unsupported canonical capability cannot be bypassed with local fallback. A confirmed historical receipt does not promise the target is still held; `done` on the courier request is not target completion. Per-request backoff resets after restart. Evidence protection must permit strictly constrained uid bootstrap without permitting row-id/origin escape or SQLite replacement.

## plugin?

No: although exposed as an agent tool, it depends on transactional storage guards, actor binding, canonical operator transport, sync partitioning and requester settlement. A UI-only plugin cannot provide those guarantees.

# Origin-local forwarding, native attestations, and adopted-worker custody

Patches: **4639–4652** (5011 snapshot).

## Behavior

Worker-delegated creation requires an exact accepted local origin binding and a live authoritative creator lease. Bounded paginated lease reads have no replica fallback. Missing or malformed ledgers do not authorize.

Authenticated forwarding decisions freeze before courier enqueue. Oversized or conflicting payloads refuse before storage. Authority failure produces no outbound. Foreign placement CLI arguments carry qualified authority; old courier refusal cannot fall back to local create. Immediate and deferred callers preserve the discovered origin gateway; unknown owners never become the executing worker profile.

Native chat creation records a machine-local attestation inside `create_task`'s transaction. Synced ancestry cannot authorize. Receipts revoke on task replacement, identity change, and non-primary/shadow stores.

Capture uses revocable in-process turn custody, never environment or tool arguments. Worker inheritance intersects a one-shot private claim with the exact agent, adopted RPC store, and active facade lease. Managed interactive turns (RPC store, no local state db) mint private capability only after owner adoption, authorized by a fresh owner-fenced `worker.nativeOrigin` observation.

## How it works

`kanban_origin_forward` / `_decision` / `_adapter` persist immutable envelopes. Placement provenance requires evidenced owner namespace and portable run identity. Native receipts live in local primary scope with delete/replace triggers. `origin_turn_custody` revalidates the exact holder. Gateway adds owner-fenced `worker.turnLease` and `worker.nativeOrigin` queries (single read transaction, no write, no receipt replay). `origin_worker_custody` and `origin_managed_custody` bind at execute seams. Facade health keeps forwarding hooks inside size limits.

## Code

- `hermes_cli/kanban_origin_forward.py`
- `hermes_cli/kanban_origin_forward_adapter.py`
- `hermes_cli/kanban_origin_forward_decision.py`
- `hermes_cli/kanban_origin_native.py`
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_placement_forward.py`
- `hermes_cli/kanban_placement_provenance.py`
- `hermes_cli/kanban_creator_chats.py`
- `agent/origin_turn_custody.py`
- `agent/origin_worker_custody.py`
- `agent/origin_managed_custody.py`
- `agent/turn_facade.py`
- `agent/turn_facade_lease.py`
- `agent/managed_worker.py`
- `agent/runtime_session_store.py`
- `gateway/session_controls.py`
- `gateway/session_worker.py`
- `gateway/session_kanban.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_placement.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_origin_forward_authority.py`
- `tests/hermes_cli/test_origin_forward_abuse.py`
- `tests/hermes_cli/test_origin_forward_adapter.py`
- `tests/hermes_cli/test_origin_forward_decision.py`
- `tests/hermes_cli/test_placement_forward_argv.py`
- `tests/hermes_cli/test_placement_provenance.py`
- `tests/tools/test_kanban_cross_board_creator_chats.py`
- `tests/tools/test_placement_qualified_callers.py`
- `tests/origin_transport/test_forwarding.py`
- `tests/hermes_cli/test_origin_native.py`
- `tests/hermes_cli/test_origin_native_lifecycle.py`
- `tests/hermes_cli/test_origin_turn_custody.py`
- `tests/hermes_cli/test_origin_worker_custody.py`
- `tests/hermes_cli/test_origin_worker_refusals.py`
- `tests/hermes_cli/test_origin_worker_executor_custody.py`
- `tests/gateway/test_worker_turn_lease_query.py`
- `tests/gateway/test_worker_native_origin_query.py`
- `tests/agent/test_origin_managed_custody.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Environment, launch labels, and replayed receipts grant nothing. A copied thread context is not a lease. Shadow/symlink primaries cannot mint native authority. Untrusted forwards never enqueue owner create. Three-hop transport tests bind only the authenticated origin host. Queries observe current assignment, not journaled lease receipts. Ordinary lease queries must not break when publication lineage validation fails.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.

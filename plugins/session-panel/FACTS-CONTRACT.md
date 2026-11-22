# Panel facts integration inventory

Status: inert consumer work in progress. This is the panel serializer/source
inventory for the framework's exhaustive v1 manifest, **not** certification of
production capture, materializers, host transport or frozen source parity.
Framework owns the shared schemas and registration API. No core import of this
plugin is authorized: plugin startup must supply loaded callable adapters through
the published API once the coordinator supplies that registration seam.

## Canonical callable boundaries

Paths below are relative to `plugins/session-panel/`.

| Projection | Canonical implementation | Required treatment |
|---|---|---|
| Full held panel bundle | `dashboard/feed.py:panel_bundle` | Assemble captured lineage/tasks/done/cards and extra inputs with captured `computed_at`, `computed_seq`, `canonical_seq`; inject the already loaded reducer. Called by legacy `Feed.compute` too. No reads/reconciliation inside assembly. |
| PanelModel v1 | `model.py:reduce_panel`, with `wire=True` | Preserve section order, row/action fields, null/absent, diagnostics, relative-time tokens and revision hashing. Do not replace with a simplified classifier. |
| Desktop bundle | `dashboard/feed.py:wire_bundle` | Omit only `cards`, `done`, `requests`, `outcome_json`; retain all other fields in insertion order. Owner preencodes the wire projection in ViewFact. |
| Decision-refresh bundle | `dashboard/feed.py:decision_projection` | Required session/lineage/model; tasks keep only present id/gateway/profile/board, in that order. No new request reconciliation. |
| Push frame | `dashboard/plugin_api.py:push_payload`, `json_safe` | Existing feed/profile envelope, nonfinite numbers to null; oversized frames announce sorted session names and empty bundles, preserving catch-up. |
| Status | `dashboard/feed.py:Feed.status` | Process-local panel epoch/rev/watch count/bundle count/failures/timing/decision failures; facts bridge preencodes this off-request, excluding stopped for HTTP. Do not substitute owner epoch for panel epoch. |
| Terminal history | `dashboard/feed.py:with_terminal_history` | Preserve authority-qualified dedup, explicit unfinished=False and origin coordinates. |
| Observation carriage | `dashboard/plugin_api.py:CoreReaders.external_semantic/advance_external` | Canonical pull time can advance independently of semantic change; never renew execution evidence or unrelated board read time. |

`ChatWaitingFact.value.panel` is the **full held bundle**, not wire_bundle. The
consumer needs its `tasks`, `done`, `cards`, `lineage`, `computed_at` for publisher
state and recovery. It also needs the already computed model; request handlers must
not load a reducer. `ViewFact(panel_bundle, {session})` establishes materialization
readiness. The consumer currently subscribes to ChatWaitingFact for publication.
ChatWaitingFact's waits/outcome/annotation/dependencies/deadline remain the exact
canonical shared projections, not aliases invented from model sections.

## Captured input ownership

| Input family | Current canonical reader(s) | Invalidation / parity obligation |
|---|---|---|
| Own session and compression lineage | `CoreReaders.lineage`, `panel_stamp(s)`, `decision_inputs` | Session row existence, lineage receipts, outcome changes, deletion and restore. Successful absence is not failed capture. |
| Linked task rows and completed history | `CoreReaders.linked`, `_session_tasks`, `done`, `_remote_inventory`, `_primary_absence` | Local+shadow+origin membership, owner identity, policy, old/new chat linkage, terminal receipts and visibility. Preserve `_calm` heartbeat removal only. |
| Dependency detail | `CoreReaders.card/_card/_detail`; `feed.chain_refs` | Sorted parents, nonheartbeat events, last run, wake-check state, ready_since, queue fields, remote owner. Existing traversal: six hops, eighty cards, stop expansion at terminal/running; preserve these bounds and ordering. |
| External owners and observations | `CoreReaders.external_capture/_external_refs` | Hub/origin/replica rows and remote parents matched by authority, not bare IDs. Preserve evidence timestamps; stale capture is not empty success. |
| Canonical requests and outcomes | `CoreReaders.user_step_requests/_user_step_requests/decision_inputs`; `inputs.read` | Requests/revisions plus current lifecycle/resolution and owner verification, including ancestor lineage. Canonical source writer must finish/defer independently. |
| Reconciliation actions | `CoreReaders.reconcile_sources`; user-steps `panel_reconcile` | Canonical writer remains a separately scheduled idempotent executor. Neither pure reducers nor request threads may run it. Deferred/failed work remains outstanding even with no later write. |
| Process/subagent evidence | `inputs.read` using core subagent/process activity annotators | Lifecycle publications, missing identity diagnostics, title changes and evidence expiry. Preserve observations and owner coordinates. |
| Rentals | `inputs.read` using compute-rentals service inventory | Reuse service snapshot, ownership predicate and lifecycle events; do not add a collector. Failure contributes `rentals unavailable`. |
| Identity and effective profile | `inputs.read`, core identity/config readers | Serving gateway aliases only; foreign ownership never repaired into local authority. Resolve at authorized startup/source publication, not requests. |
| Time | `model.reduce_panel` and canonical evidence projector | Frozen wall time + deadline invalidation for status freshness, stuck state, overdue, leases and decisions. Relative wire tokens do not authorize dropping evidence time. |

`inputs.read` emits outcome_json, requests, subagents, processes, rentals,
input_errors and profile/gateway; gateway_aliases is conditionally present. Do not
fill every absent field with null: absence is part of parity. All nested values
must come from canonical projections; the table is not an unrestricted schema.
The coordinator must generate exhaustive typed fixtures for these projections,
including every CoreReaders detail/queue field supplied by each supported core.

## Lifecycle and protocol

- Global legacy default and five-consumer readiness latch; no standalone activation.
- Old cores remain on legacy lanes. Facts cutover must drain f/c/d/b and board
  workers before publishing facts; timeout retains predecessor custody.
- Rollback closes facts subscription and resumes old lanes once, preserving panel
  epoch/snapshot and watch membership. Restored bundles remain carried/stale until
  validated by actual publication.
- POST /changes remains immediate catch-up with epoch/rev/reset/bundles. Push uses
  plugin.session-panel.changed and the existing prev chain. TTL/cap and heartbeat
  remain panel-owned. Heartbeat proves transport only, never source freshness.
- Failure/missing/expiry retains last good data as unavailable, never fresh empty.
- Host transport owns bounded pinned reset paging. The panel's 64-envelope/4 MiB
  inbox coalesces overflow to a memory-only coherent snapshot of that replica;
  it does not repeatedly re-subscribe to an oversized reset or invent paging.
  Cross-epoch reset callbacks are reconciled against the current replica so an
  old callback in flight cannot rewind a snapshot catch-up.

## Verification boundary and remaining gates

`tests/test_panel_projection.py` compares the extracted assembly to its previous
inline implementation at a fixed clock, including nulls, ordering, model revision,
wire and decision projections, with module discovery forbidden for the injected
reducer. These are controlled captured inputs, not source-to-owner parity.
Existing fact subscription/HTTP tests exercise transport and warmed no-I/O paths.
`tests/test_fact_transport.py` additionally spawns a real owner, feeds canonical
controlled bundles through its scoped IPC replica into the panel publisher, and
checks a 5 MiB reset, wire equality, no request I/O/encoding, source failure,
quiet expiry, disconnect and new-owner recovery with the panel epoch preserved.
This is real transport proof, **not** native source capture or production startup.

Still required: exhaustive coordinator manifest; native source mutation fixtures
through owner materializers; finite ViewFact expiry readiness through authorized
startup; final complete two-core suites; independent exact-head review and CI.
Inert landing follows shared compatibility only. Combined live acceptance belongs
to the coordinator.

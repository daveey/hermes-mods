# Opt-in board projection (UNACTIVATED)

This is the memory-only consumer seam over [BOARD_SNAPSHOT.md](BOARD_SNAPSHOT.md).
It does not change acquisition/certification, start threads, schedule sources, or
cut over any production route. PanelModel remains version 1. Production full,
board and catch-up consumers must be explicitly migrated by the coordinator;
merely importing Feed does not activate a snapshot consumer.

## Composition

```python
# Acquisition owner registers exact Watch/ScopeDemand and accepts SourceParts.
# Canonical owner separately acquires current requests/outcome for exact lineage.
feed.watch([chat.session_id])
projection = feed.project_board(store, chat, canonical_base=canonical_bundle)
results = feed.publish_projections(store, {chat.session_id: projection})
```

`project_board(..., pin=(snapshot, admission), seq=...)` supports a caller-owned
immutable pin; without a pin it calls `store.pin(chat)`, never source I/O.
`project_boards(store, {ChatKey: canonical_bundle}, pins=...)` is the same seam for
full/catch-up batches. It shares the immutable source materialization; each chat
selects its own memberships and canonical component. A canonical bundle requires
`session`, exact `lineage`, and independently ordered `canonical_seq`,
`outcome_json`, `requests`, plus any canonical `input_errors`.

Projection is frozen, with recursively frozen bundle/scopes/versions/dependencies.
It retains the exact B1 Admission, covered and missing scopes, source-key
closure, Feed epoch/owner/watch token and an internal publication token. The
consumer does no SQLite/network work. `computed_at` is the earliest actual
covered acquisition receipt, not projection completion; `computed_seq` is not
source authority. Keep separately acquired canonical input clocks independent.

## Partial is not certified empty

Strict `SnapshotReaders._scopes` is unchanged and still rejects any uncovered
source. The explicit PartialReaders adapter selects only exact-scope shared
materialization, retaining last-known memberships as stale/unknown where
available. An absent source has no invented rows; its diagnostic remains even
when every visible known source is empty. Neither partial projection nor
publication certifies, acknowledges, retires or clears a missing ScopeDemand.
Only acquisition/acceptance can establish coverage; the source coordinator keeps
and retries missing work. A successful partial local publication is not full
coverage or execution freshness. Unrelated reader/capture/decision failures are
not cleared by publication.

Unknown-source task/detail fields retain `stale` and `partial_source_unknown`;
core/reducer policy remains authoritative. A board is not a canonical consent
source. Current request state, resolution, permission limits, owner and choice
bindings come only from canonical inputs. Legacy ID-keyed `cards` cannot represent
colliding owner IDs: partial projection suppresses ambiguous detail rather than
borrowing one owner's authority for another. Owner-qualified rows/actions remain.

## Publication and lifetime

`publish_projections(store, ...)` serializes writers, then atomically rechecks the
B1 vector and current exact ScopeDemand/watch/lineage/generation/store epoch
under `store.publication(admission)`. Lock order is Feed compute writer -> Feed
interest lock -> store lock. Acquisition must release the store before calling
Feed. Hashing/reduction may run inside that critical section; wire serialization
and callback run after the store borrow ends. Reserved scheduling and measured
publication/component budgets are separate integration/acceptance obligations.

An older source vector cannot overwrite a completed/archived current source,
even with a later computed sequence. An unchanged per-scope receipt may survive
unrelated covered-source progress. Scope expansion with unchanged file stamps,
forget/rewatch, lineage change, source reset/removal/failure, stop, resume and
restore invalidate held work through the store and Feed lifetime fences. The
coordinator must register/forget both store and Feed watches as one owned
lifecycle; do not reuse a prior pin for a new Feed watch. A restored snapshot is
carried/unvouched, not restored source authority.

Once a bundle opts in, a legacy/full result without a source vector can update
only a newer exact-lineage canonical component, never its board component. The
newest canonical sequence independently wins, including current canonical source
errors. Canonical merges retain board computation/execution clocks. The wire
omits internal board diagnostics from raw fields but retains reducer diagnostics.

Each result distinguishes `stored`, `no_change`, `superseded`, `stopped`,
`changed`, callback `emit_succeeded` and revision. `stored` is not a transport
receipt; successful callback is not authenticated model inclusion. This API does
not acknowledge the Admission: downstream inclusion receipts must retire only
exact current source work. Never acknowledge superseded/transport-failed work.

## Observation-only carriage

An acquisition owner can retain the canonical raw external capture on an initial
projection with `external_capture=tuple_of_encoded_rows`. For an unchanged
semantic capture, use:

```python
observation = feed.carry_board_observations(store, previous_projection, captured)
feed.publish_projections(store, {chat.session_id: observation})
```

This reuses CoreReaders' pure `external_semantic` and `advance_external` helpers:
only known pull clocks move, with producer/owner/session/origin/binding-qualified
identity. Stale/status/version changes require normal source acceptance and new
projection. Legitimate clock regression is preserved, never clamped. Computation
sequences/age, card detail/execution evidence, and carried/reissue state are not
renewed. Exact parent-publication CAS rejects concurrent old captures, repeated
observation publication and older full projections of the same Admission.
Parent tokens do not retain historical projection chains. The coordinator owns
raw-capture provenance and source-reset detection; clock carriage grants no
coverage or source freshness. The same-admission observation-parent veto applies
only to a held projection from this Feed owner, epoch and exact watch. A foreign
predecessor's carried projection cannot veto a successor's fresh projection; it
is presentation history, not a current observation-parent token. Current-owner
parent CAS and store/source-vector/watch fences still reject stale writers.

## Activation and rollback boundary

All APIs above are UNACTIVATED. No new source service or scheduler exists here;
existing production routes still use their original acquisition. Integration
must explicitly inject every board/full/catch-up consumer, coordinate source and
watch lifetime, preserve canonical failures, implement reserved service and exact
inclusion receipts, and prove source-to-painted-model latency/CPU under load.
Rollback before integration is removal of the unused seam. After integration,
stop the owned coordinator, fence/close its store and restore the prior production
route through normal reviewed rollout; never mix unfenced legacy board writes
into an opted-in bundle or treat a disk snapshot as fresh evidence.

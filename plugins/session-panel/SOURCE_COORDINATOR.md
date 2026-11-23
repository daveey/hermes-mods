# Reserved source coordinator (UNACTIVATED)

`Feed.compose_board_sources(reader_factory, gateway=..., profile=...)` is an
explicit disposable-composition seam. No normal plugin startup, REST handler,
configuration flag, saved restart entry or deployed Feed calls it. Installing
these bytes does not activate shared-source scheduling. PanelModel stays v1.

## Ownership and routes

One `SourceCoordinator` owns one B1 `SnapshotStore` and replaces that Feed's
BoardLane only after explicit composition. Its four work slots replace, rather
than supplement, four per-chat hydration slots:

- one local acquisition slot: lineage registration and canonical, set-based
  `CoreReaders.capture_local` over the configured boards;
- one strict external acquisition slot: canonical `capture_external` with frozen
  ScopeDemands, never joined by local admission or full/catch-up consumers;
- two pure projection slots, shared by board, `compute`, `_compute_all`, `_tick`,
  full and catch-up routes. These call B2a `project_board` / `publish_projections`,
  not SQL, external capture or per-chat detail hydration.

The optional completion service is a scheduler, not a fifth hydration slot.
Manual construction and composition start no thread. `step()` starts requested
acquisition; `full()` drives the same slots and waits for its owned local capture
and projection tickets; `start_service()` additionally permits event-driven
completion/refill. Readers obtained for acquisition are released on their owner
thread. The canonical reader remains independently owned by Feed.

Pending projections have one coalesced entry per chat, retaining the first
unserved queue age. Source completions enqueue only the store's affected
admissions. Completion-driven refill cannot wait for the next strict external
capture. A local write after strict-entry, including a newly watched chat, can
acquire and publish its known component while external coverage remains unknown.
Unknown coverage is diagnostics and unserved custody, not known empty input or
permission to vouch via heartbeat.

## Source and canonical authority

B1 immutable snapshots, exact source/key dependencies and ScopeDemand APIs and
B2a publication fences remain authoritative. A `BoardBundle` only transports the
exact in-process projection back to Feed's writer; `_publish` validates it again.
A stale full result with a greater `computed_seq` cannot regain board authority.
Feed membership tokens are additionally checked before projection. Exact
owner-qualified expansion is `owner.require(session, refs)`; it delegates to
`SnapshotStore.require`, never an ID-only fallback.

Canonical `panel_inputs`, decision fast reads and reconciliation are NOT snapshot
reads. The composed tick captures the reconciliation source stamp before reads
and producer writes and calls Feed's shared selective admission helper, including
owed/deferred scopes, lineage changes, finite canonical recovery and the existing
60-second real-source ceiling. Acquisition completion generations prevent a
same-chat event arriving during reconciliation from being consumed by that older
pass. Canonical component sequence/overlay remains independently ordered: a board
projection cannot reopen a retired request, perform consent or clear a canonical
read failure. Missing session rows retain the real adapter's semantics.

Source retries are finite per source and exact scope: initial plus three bounded
retries via the existing Feed backoff. A changed source or changed exact demand
renews only the corresponding budget. Adding another chat does not reset an
exhausted peer. Partial/failed acquisitions keep held known rows and explicit
unknown coverage. Registration and pure-projection failures have finite retries
as well; full/heartbeat do not clear failed strict capture custody. A source
retry deadline is bounded by its reader role's eligibility; an exhausted role
cannot busy-spin on an older source deadline. Reader-role generations include
current exact ScopeDemands as well as source/membership stamps, so a changed
dependency scope can re-admit an exhausted recovered factory without requiring
an unrelated file write or resetting unchanged peers' source-owned budgets.
Unchanged full/catch-up reads
settle their tickets with a retry error, rather than spending extra pure attempts
while backoff is pending or exhausted. A genuinely changed acquired source or
exact demand can renew that projection budget. Basic Feed
resume retains the failed-capture exclusion from `fresh`.

## Lifecycle, rollback and next acceptance

`close()` marks this owner closed, stops/wakes service, cancels pending work,
joins owned workers within bounds and retires the store owner. In-flight external
I/O cannot be forcibly cancelled; its late result cannot publish through a stopped
Feed or retired store. Release owned fixture barriers before teardown. Do not
compose this owner on top of a running legacy BoardLane.

This milestone supplies explicit composition, not production lifecycle migration.
The downstream lifecycle milestone must prove genuine predecessor entry and both
registration orderings, hot-reload owner establishment, forget/re-watch/lineage
races, complete source/observation/inclusion custody and the maximum-population
matrix before activation. Basic `resume` failure exclusion is not proof of those
successor-owner cases.

Rollback before activation is simply leaving the seam uncalled. After a future
approved activation, rollback must stop the composed Feed/owner and start a clean
uncomposed Feed under the approved lifecycle procedure; deleting its attribute
on an emitting Feed is not a supported rollback. Do not carry a retired store's
publication authority into a legacy writer.

The later activation receipt must identify the installed mods/core heads, exact
Feed/reader generation, source/store owner epoch, live role/service thread owners,
watch/demand/source vectors, inclusion publication/emit receipts, target
connection/profile/session and scoped model revision. Native paint, end-to-end
latency, CPU/contention and final performance acceptance remain separate. Earlier
projection maxima above 50 ms are not waived by these correctness tests.

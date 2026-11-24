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
  full, catch-up and keyed refresh routes. These call B2a `project_board` / `publish_projections`,
  not SQL, external capture or per-chat detail hydration.

`Feed.queue_keyed` admits through the composed owner's `_admit` seam before
recording `_keyed_pending`. A refusal leaves that pending set unchanged. The
coordinator queues only registered watches, retaining first-unserved monotonic
age and existing projection/source retry budgets; its reserved local slot
registers new watches and reacquires changed input. `refresh_keyed` uses the same
`full` tickets and publication fences, while Feed retains canonical producer
ownership. The actual file watcher's fixed recovery branch uses this seam too;
composition must not terminate that watcher or silently use legacy hydration.
Keyed admission retains Feed's lock through membership filtering and successful
pending mutation. Projection base selection must therefore never acquire
`Feed.lock` while holding the coordinator lock: snapshot the coordinator's base,
release its lock, then read the Feed base. The immutable pin retains its original
Feed watch token through publication, so a forget/re-watch during this unlocked
interval cannot publish an old projection into a new same-name watch. Store
source/demand/lineage CAS and Feed's writer membership fence still run at commit.

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

## Single strict-capture observations (UNACTIVATED)

The external role first calls `board_capture.capture_external_raw` once for the
whole exact demand union. Its frozen `ExternalCapture` binds the source, demands,
CaptureHints (owner/version), pre-read stamp, read-start clock, raw encoded rows
and inventory. `capture_external(..., raw=...)` materializes that same input; it
validates provenance and never performs a second union read. Existing callers
omitting `raw` retain the strict acquisition path. This is an internal acquisition
input, not another SnapshotStore/PanelModel schema or a source certificate.

The coordinator retains per-scope raw rows against the accepted scope receipt
version and supplies them to every pure projection. Only an exact current,
covered, published parent with equal canonical `external_semantic` is eligible for
clock carriage. All scopes in the union must qualify. Missing/changed coverage,
watch/epoch or semantic rows take strict materialization instead. Raw provenance
is never inherited across owners; compatible store transfer alone cannot attest
that a current owner performed a strict capture.

Strict membership does not include every detached ancestor's task/result. The raw
capture therefore also attests semantic shadow-board inputs for its member/ref
boards (including prior hydrated closure boards). This source-owned SQL work is
once per board, not per chat. It hashes the canonical activity tables excluding
core-defined lease/HLC/sync-version/heartbeat bookkeeping; file mtime, WAL/SHM churn
and activity-counter advances alone are not semantic content. A reverted value
can still match the published parent while an older strict capture is held.
Changed dependency content must materialize rather than carry the old detail.
This conservative whole-board attestation is budgeted inside acquisition; its
SQL/CPU cost is real, not zero or per-chat projection work. Maximum-population
and production performance acceptance remain required before activation.

A clock-only pass uses B2a `carry_board_observations` and `publish_projections`,
without hydration, `project_board`, canonical-dirty admission or a second strict
union acquisition. The raw source attestation above still performs SQL reads.
CoreReaders owns the producer/owner/session/origin/binding-qualified identity:
replica rows, source-less origin rows and origin-obligation rows retain distinct
clocks, including legitimate regression. The store accepts an unchanged part with
a new source CAS revision but unchanged scoped receipts; this admits no semantic
work and renews no acquisition/computation/execution clock. A strict capture begun
before that acceptance cannot later acquire authority. A losing CAS never marks
the source failed; unknown scopes still receive the existing finite retry schedule
unless a newer attempt already owns that signature.

A publication-parent race retains captured rows and queues bounded pure retry;
it does not recapture to repair presentation. Local/full retries carry the most
recent raw clocks onto their computed board bundle. Both a delayed full candidate
and a delayed observation candidate lose to a newer exact parent under Feed's
existing publication fences. Canonical inputs are overlaid independently.

`tests/test_coordinator_observation.py` executes real replica SQL, actual
CoreReaders linked/done -> session_tasks origin selection in hub and replica
modes, regressing clocks, single-capture/no-hydration controls, owner resume,
local-after-observation, semantic title/status controls, immutable provenance,
finite lost-CAS retry and both older projection branches with event barriers.
These are source behavior tests, not authenticated inclusion or activation proof.

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

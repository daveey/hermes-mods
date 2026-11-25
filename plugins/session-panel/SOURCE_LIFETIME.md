# Explicit snapshot-owner lifetime (UNACTIVATED)

This is an in-process contract, not a PanelModel wire change or permission to
activate composition. Normal startup never opts in. The activation owner must
separately establish all end-to-end gates.

## Registration and reader ownership

`Feed.compose_board_sources(factory, gateway=..., profile=...)` explicitly opts in.
Manual composition remains inert until driven or started. A genuine saved API's
board entry establishes only an already opted-in service without another HTTP read.

`Feed.resume(predecessor)` fences predecessor store authority before any join,
carries the revision space unvouched, and preserves explicit intent. It never
borrows the predecessor reader factory. A saved API may construct the successor
with obsolete canonical readers. Resume releases that unused object on its caller
thread and uses current registered readers for canonical and source inputs.
Saved decision loops cannot inject old readers into an opted-in successor refresh.

`plugin_api.register_snapshot_readers(factory)` publishes current capability under
the loader lock, then snapshots the current Feed under the process lock and calls
`ensure_snapshot_service(feed, api_generation, factory)` outside those locks.
Resume independently consumes registration, covering both orderings. Older Feed
classes lacking the seam retain the published factory for natural succession;
registration does not opt them in.

`Feed.ensure_snapshot_service` is idempotent for an installed owner and does
nothing for normal Feeds. While any predecessor role remains occupied, even
candidate construction/source-coordinate discovery is deferred. A pending handle
is not a hydration lane. Existing saved board loops retain that handle and forward
to the installed owner without starting a second board loop or adding slots.

## Unsupported readers and races

No registration means a pending, carried/unvouched owner, not authoritative empty
inputs, legacy per-chat hydration or a new revision space. Full/tick/decision
routes do no old-reader work while pending. Registration recovers without traffic.
Capability failure is attempted once for the exact `(api_generation, factory)`;
beat/full/step do not reset it. A replacement registration is the recovery boundary.

Only one installation candidate is allowed. Construction and reader release run
outside the Feed lock. Installation rechecks pending identity, registration,
stop and close after discovery and release. Stop, close or supersession cannot
publish the candidate or start workers. Supersession wakes the existing board
entry for its current candidate instead of creating another thread.
Consecutive resumes close the superseded pending handle under its Feed lock and
carry every still-installing ancestor independently of source-role retirement.
The successor cannot construct a candidate until each ancestor's construction,
capability discovery and reader release has completed. The existing board wake
and bounded pending-loop revisit provide recovery without HTTP traffic or a
waiter thread; a pending handle never contributes hydration capacity.

## Role custody and release

There remain exactly four work roles: one local acquisition, one strict external
acquisition and two pure projections. The scheduler is not another hydration role.
`RetiringOwner` fences the genuinely older coordinator without assuming new
`retire`/`drained` methods. Its completion queue marks work after source-reader
release. All occupied roles and older retirement ancestors drain before successor
eligibility. Old immutable pins retain data but no publication authority.

Source workers release their per-acquisition readers. Canonical reader objects
use thread-local connections released after each owned canonical pass. Reader
objects may persist; handles and connections never transfer between owners.

## Compatible custody transfer

After every predecessor role drains, `SourceCoordinator.inherit` consumes the
existing B1 `SnapshotStore.transfer` exactly once. Same-schema content-qualified
module generations rebind immutable dataclass keys/values into the current
snapshot module; different field layouts fail closed without consuming the old
store. Store epoch, exact versions/receipts/demands, watch identities, missing
coverage, failures and first-unserved admission ages remain. Acquisition owner
tokens rotate: an old capture cannot acquire authority on the new store.

FIFO pending ages (including current active projection ages) and source/scope,
reader-role and projection retry signatures/counts/deadlines carry independently.
Late queued reader-role failures are included before installation. An interrupted
capture retains its already consumed attempt and gets a finite backoff; reload
is not a new source event or fresh retry budget. A real changed stamp or changed
exact demand continues to be the retry recovery boundary.

Canonical input bases/readers are not transferred. Carried bundles remain
unvouched; metadata/current scope checks and current canonical reads still own
subsequent publication. Transfer does not renew capture, computation, execution
or authenticated-inclusion clocks, and does not acknowledge any admission.

## Rollback and downstream receipt

Before activation, rollback is leaving composition uncalled. After authorized
activation, stop the service normally and verify all four roles and retirement
ancestors drained before installing a clean legacy Feed. Never delete a live
owner attribute or revive a retired store.

The activation receipt names serving process/birth, mods/core revisions, current
API/reader generation, Feed/store epochs, retired ancestors, thread/role identities
and current source/watch/demand vectors. Source tests, callbacks and cached hashes
do not establish activation or authenticated exact inclusion.

The same UNACTIVATED source now implements single strict-capture observation
carriage ([SOURCE_COORDINATOR.md](SOURCE_COORDINATOR.md)) and exact authenticated
response-write custody ([SOURCE_RECEIPTS.md](SOURCE_RECEIPTS.md)). Their fixtures
do not establish live activation, native pixels, latency, performance, CPU or
contention acceptance. The original population/race and downstream gates remain
unchanged. See [SOURCE_ACTIVATION.md](SOURCE_ACTIVATION.md) for B3's receipt contract.

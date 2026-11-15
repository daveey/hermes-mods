# Worker admission and recovery test contracts

Patches: 3790, 3807, 3812, 3826.

## Behavior

Test-only changes make admission fixtures independent of live host load and pin pickup-stall and workspace-launch recovery guards. They do not introduce a new dispatch policy.

## How it works

Admission fixtures pin the actual `_pressure` inputs: effective cores and `os.getloadavg`. High pressure must win before unknown occupancy, without recording an unknown-resource streak; low pressure exposes the leaked-board alarm.

Pickup-stall fixtures align `stranded` with pickup's truthy-hold interpretation and exercise repair resolution against leases, repinning, imports, registry uncertainty and a claim racing archive. The source-launch fixture forces workspace-path persistence to raise, then checks that dispatch never spawns or consumes capacity, releases the run/claim and records infrastructure failure without incrementing task failures.

## Code

Test-only changes; production code is not modified by these mails. The targeted core contracts are described above.

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/hermes_cli/test_worker_resources_unknown_alarm.py` (patch 3790).
- `tests/hermes_cli/test_kanban_pickup_stall_held.py` (patch 3807).
- `tests/hermes_cli/test_kanban_pickup_stall_resolve.py` (patch 3812).
- `tests/hermes_cli/test_kanban_source_launch.py` (patch 3826).

- **3790:** With 16 effective cores, pinned load 24 returns worker_load_pressure before leaked-board occupancy and records no unknown streak; pinned load 1 returns worker_resources_unknown and records it.
- **3807:** Parameterized truthy/falsy hold values require stranded listing to agree with the actual pickup eligibility gate.
- **3812:** Hold/registry uncertainty keeps repairs open; live Board leases preserve owner work, expired target leases still wait, moved/imported/repinned targets resolve as appropriate; no-card repairs and racing claims stay open, and resolve exceptions do not stop detection.
- **3826:** A set_workspace_path OSError prevents spawn/capacity use, restores ready/no claim/current run, ends the run as released and records infrastructure metadata without consecutive task failures.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

An unreadable or missing registry is not proof a hold lifted. An expired lease is not an active lease. A repair with no named targets is not automatically complete. The launch-failure acceptance case is explicitly a non-primary/local board; other launch routes are not newly established by that fixture.

## Plugin?

No. These tests exercise core admission, pickup-stall detection and dispatch transaction boundaries.

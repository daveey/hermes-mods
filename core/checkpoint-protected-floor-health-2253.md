# Checkpoint health recognizes the protected floor

Patches: **2239–2243**.

## Behavior

Successful compaction that retains only final project snapshots is a protected floor, not failed maintenance. For other over-cap stores, either exceeding twice the cap or lacking a successful prune for 48 hours can trigger the daily health error. Busy maintenance defers observation without consuming the daily health marker.

## How it works

An error-free prune writes `.last_prune_success`. When trimming cannot meet the cap, a floor receipt records the exact ref listing and store size only if garbage collection is settled, refs are exclusively in the checkpoint namespace and no commit has parents. Health revalidates that receipt under the maintenance lock; changed refs/size or pending collection invalidate it. Namespace validation requires the slash boundary, and receipt JSON accepts a UTF-8 BOM.

## Code

`tools/checkpoint_health.py` owns floor receipts. `tools/checkpoint_maintenance.py` classifies size outcomes and performs locked health checks separately from the prune-attempt marker.

## Tests

Source coverage, **not run in this mirror**:

- `tests/tools/test_checkpoint_health_floor.py`: independent health thresholds, successful floor, failed-prune timestamp control, invalid/changed receipts, foreign near-prefix refs and lock-busy deferral.
- `tests/tools/test_checkpoint_size_pending.py`: health checks use successful-prune timing and retain daily reporting controls.

Patch 2240 extracts size classification without adding a test. Patch 2243 changes the receipt decoder; no dedicated BOM regression test is added in this group.

## Applying

Use the [README full replay procedure](README.md#applying), including namespace and encoding follow-ups.

## Gotchas

A verified floor is an informational exception, not permission to delete final snapshots. Missing, corrupt or stale receipts fail closed. The old last-attempt marker is not evidence that maintenance succeeded. Health errors remain daily-gated rather than reporting on every tick.

## Plugin

Core checkpoint maintenance and health reporting; no plugin required.

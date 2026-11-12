# Pickup-stall detector: hold windows and time pinned to this machine

Patches: **2624, 2639**.

## Behavior

- A machine hold whose reason states `release by HH:MMZ` (also `release at`, optional seconds), or whose owner is the core-update rollout (`rollout:worker-core-rollout@<host>`), files no "Pickup held" repair card until that release plus `kanban.sync.pickup_stall_seconds` (default 600) has passed. A rollout hold with no stated release is expected for 90 minutes from `hold_at`. Past the window, with no known `hold_at`, or with no stated release and a non-rollout owner, the hold card is filed as before.
- The stall clock for a ready card starts at the later of `ready_since` (else creation) and the newest placement event in the replica that names `machine:<this machine>`. A card that sat ready elsewhere for 10 minutes and was moved here seconds ago no longer raises "waiting 10 min" on arrival. With no such event the behavior is unchanged; thresholds are untouched. The alarm text's "ready/waiting N min" now reports the time pinned here.

## How it works

- 2624: `kanban_pickup_stall` gains `_release_s` (first UTC instant at or after the hold start; invalid times such as hour 24 give none), `expected_hold(entry, now_ms, stall_s)` (returns the release instant while the hold is expected, else None; `hold_at` accepts seconds or milliseconds) and `_hold_expected`, which reads the machine's registry entry. The `hold` group is skipped when it returns true; the skip is logged at debug level.
- 2639: `_pinned_here_since_ms(replica, task_id, own)` reads the newest `created_at` among `moved`, `placement_repinned`, `pool_pinned` and `pool_unpinned` events whose valid-JSON `payload.runs_on` contains `machine:<own>`; `stalled()` takes `max(ready_since, that)`.

## Code

`hermes_cli/kanban_pickup_stall.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_pickup_stall.py`. Entry path: the pickup-stall detector module API against a fake machine registry and a temporary replica database, plus `expected_hold` directly. Covered: a hold inside its stated window (and a rollout hold with no stated release) filing nothing, then filing once expired; an operator hold with no release still filing; the release rule across times and a missing start; a card moved here (`moved` and `placement_repinned`) not stalled yet, stalling from the move and reporting that wait; an older move, a move naming another machine, a non-placement event and a malformed payload not moving the clock. `pool_pinned` and `pool_unpinned` events, the real gateway scheduler and a live registry are **untested**.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- A stated release time is trusted as written: a hold that says "release by" a late time suppresses its repair card until then plus the threshold.
- The rollout-owner prefix and 90-minute cap are constants in the module, not configuration.
- A move event that is missing from the replica leaves the old clock in force.

## Plugin

Core change; no plugin required.

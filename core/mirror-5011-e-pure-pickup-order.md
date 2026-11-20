# Pure pickup gate decisions and whole-set lane ordering

Mirror entries: 4795, 4805.

## Behavior

Pickup gate decisions and lane ordering expose pure helpers for controlled observation and replay while preserving the live dispatcher short circuits.

## How it works

The pickup evaluator requests ordered facts and distinguishes absent facts from explicit null. Immutable bounded observations exist only in an explicit caller-owned context and retain partial/unknown state after reader failure. Lane preparation partitions the whole scored candidate set into front, ordinary and paused queues. Ordering reuses observed running counts only when multiple queues need them, preserves default-lane fallback and charges each ordinary candidate a pick even if a later claim refuses it. Helpers do not mutate partitions or observed loads.

## Code

- `hermes_cli/kanban_lane_order.py`
- `hermes_cli/kanban_lanes.py`
- `hermes_cli/kanban_pickup_gate.py`
- `hermes_cli/kanban_worker_node.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Pickup-gate tests cover missing and malformed facts, short circuits, exceptions, overflow and real caller capture. Lane-order tests include frozen-algorithm differential cases, alternate pending subsets, repeated ordering, missing loads and live lazy-reader/identity controls.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/hermes_cli/test_kanban_lane_order.py`
- `tests/hermes_cli/test_kanban_pickup_gate.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

A complete pickup-gate decision covers skip-reason evaluation only, not quota, import, pacing or claim. Lane ordering is not eligibility evaluation, policy-identity attestation, a complete dispatcher trace or fairness certification.

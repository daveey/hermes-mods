# Bounded shared observation follower waits

Mirror entries: 4780.

## Behavior

Callers sharing a remote-observation snapshot cannot wait indefinitely for a stalled leader, and failed leaders do not make every follower independently repeat persistence.

## How it works

The public observe function delegates to a private election helper. Followers wait on the existing completion event for at most 30 seconds with no lock held. A completed failed flight retries through normal election so followers coalesce. A timed-out follower falls back independently. Leader finally-signaling and generation fencing remain intact.

## Code

- `tui_gateway/kanban_remote_observations.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Observation-retry tests deterministically expire the real flight wait and hold retry publication until all followers choose a path. Existing singleflight tests retain successful coalescing and generation behavior.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/tui_gateway/test_kanban_observation_retry.py`
- `tests/tui_gateway/test_kanban_observation_singleflight.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

The bound applies only to the shared wait, not the fallback ledger write. It is not a general persistence timeout or an assertion about live remote latency.

# Fresh bounded observation of overdue home transitions

Mirror entries: 4837, 4838, 4839.

## Behavior

The central pickup-stall observer can identify overdue home-owned scheduled/todo transitions even when its own local pickup is disabled or drained, without automatically transferring work.

## How it works

Scheduled candidates use their current canonical wake/backstop; todo candidates require all parents done with dated completion. The transition must be more than 30 minutes overdue and the home held, revoked or without recent Board events. Fresh completed replica pulls and fresh registry evidence are required. Scans are limited to once a minute per replica identity. Existing authenticated pickup-alarm provenance creates or reuses one open incident per home; elapsed age and held-home heartbeat changes do not churn comments. Checkpoints conservatively distinguish possible machine-only work from proven portable custody. Observation failures are isolated from local ready-card alarm production.

## Code

- `docs/kanban-home-stall.md`
- `hermes_cli/kanban_pickup_observation.py`
- `hermes_cli/kanban_pickup_stall.py`
- `hermes_cli/kanban_pickup_stall_home.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Home-stall tests cover threshold boundaries, stale/missing evidence, parent clocks, parks, checkpoint custody, grouping/reuse, closed incidents, replaced replica files, deduplication and actual dispatcher ordering. Source notes distinguish controlled tests from unavailable historical pre-transfer replay evidence.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/hermes_cli/test_kanban_pickup_stall_home.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

The detector does not revoke/unhold a home, invent death proof, transfer cards or auto-archive active incidents. Transfer eligibility and custody checks remain authoritative. Missing checkpoints mean unknown portability, not permission to move work.

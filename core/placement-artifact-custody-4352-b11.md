# Placement, artifact and registered-probe custody

Patches: **4262, 4272–4273, 4279, 4291, 4297, 4346–4347** (4352 snapshot).

## Behavior

Collection-root hints produce actionable placement guidance without erasing artifact custody. Automatic pickup-gate returns can record creator-owned origin authority.

Terminal custody release preserves independently persisted jobs instead of signalling them. A registered local wake probe does not by itself pin ready execution to that host.

## How it works

Placement hit-filtering snapshots mutable configuration while preserving text audit identity. Explicit worker-self language distinguishes intended local system operations from ambiguous unit/config references.

The latest park must match both the exact wake probe and a nonempty current gateway identity before probe custody can be separated from execution placement. Later parks cannot borrow older custody.

Shipped artifact regressions verify prestaging before the review write transaction, source-mutation rejection with copy cleanup, and display-only filtering of legacy executor-local attachment paths.

## Code

- `hermes_cli/kanban_home_marker_placement.py`
- `hermes_cli/kanban_hub_admission_claim.py`
- `hermes_cli/kanban_hub_probe_custody.py`
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_requeue_custody.py`
- `hermes_cli/kanban_worker_placement.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_artifact_custody_bounds.py`
- `tests/hermes_cli/test_kanban_home_marker_nits.py`
- `tests/hermes_cli/test_kanban_hub_probe_custody.py`
- `tests/hermes_cli/test_kanban_receipt_artifacts.py`
- `tests/hermes_cli/test_kanban_requeue_custody.py`
- `tests/hermes_cli/test_kanban_review_artifact_prestage.py`
- `tests/hermes_cli/test_kanban_worker_first_pickup_gate.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Unknown or legacy probe custody remains local and conservative; other structural and repository placement restrictions still apply. A legacy path is display data, not permission to fetch from another executor. Artifact and terminal-release tests ship, but no real job or artifact was manipulated here.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.

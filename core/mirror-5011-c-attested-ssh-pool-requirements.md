# Exactly attested SSH pool requirements on the Board allowlist

Patches: **4633** (5011 snapshot).

## Behavior

Cards that declare the Board-supported SSH access requirements `ssh:mini` or `ssh:mbp` are placeable on the worker pool when another worker carries the matching operator attestation. Claim still refuses expired, held, or revoked attestation. Alarm moves keep attested SSH access and must not drop it or move an unlisted card back onto the stalled machine.

## How it works

Core's Board access allowlist adds those two exact SSH entries. Structural pool admission may queue while stale; claim-time freshness still refuses execution. Tests exercise the production allowlist rather than concealing it with monkeypatches.

## Code

- `hermes_cli/kanban_pickup_stall_repin.py`
- `hermes_cli/kanban_placement_coordination.py`
- `website/docs/user-guide/features/kanban-worker-lanes.md`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_coordination_placement.py`
- `tests/hermes_cli/test_kanban_declared_access.py`
- `tests/hermes_cli/test_kanban_pickup_stall_repin.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Malformed requires/skills still cannot be placed. Other `ssh:*` names remain unsupported. Pool admission is not execution eligibility. Each host's SSH access still needs its own operator attestation.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.

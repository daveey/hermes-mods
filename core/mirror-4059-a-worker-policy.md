# Worker policy-root isolation and held-worker diagnostics

Mirror ordinals: 3736, 3752, 3757.

## Behavior

- A durable `TMPDIR` cannot redirect `HERMES_TEST_POLICY_ROOT` away from real worker CPU policy.
- Relative test-policy paths remain refused even when the current directory is temporary.
- Pickup-stall diagnostics and resolution use the same truthy `hold` semantics as pickup eligibility; a truthy non-boolean hold must not be reported as lifted.
- Unknown-resource alarm sweeps are confined to a primary board.

## How it works

`worker_compute_capacity._override_temp_roots` admits only fixed system temporary roots, a recognized macOS per-user temporary directory, or the account's pruned scratch area. Ancestors containing the account or HOME are never added. The override itself must already be absolute. `kanban_pickup_stall`, `kanban_pickup_stall_held` and `kanban_pickup_stall_resolve` replace identity comparisons against `True` with truth-value checks. The final patch adds isolated tests of pre-existing relative-path and primary-board guards.

## Code

- `hermes_cli/kanban_pickup_stall.py`
- `hermes_cli/kanban_pickup_stall_held.py`
- `hermes_cli/kanban_pickup_stall_resolve.py`
- `hermes_cli/worker_compute_capacity.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/hermes_cli/test_kanban_pickup_stall_lifted.py` — A truthy string hold remains held, its repair card remains ready with no lifted comment, while an empty-string hold is not expected.
- `tests/hermes_cli/test_worker_compute_capacity.py` — A parametrized root matrix admits known ephemeral roots and rejects durable roots, the macOS cache/parent rather than its temporary leaf, and relative values even under a temporary cwd.
- `tests/hermes_cli/test_worker_resources_unknown_alarm.py` — Shadow and off board modes return without filing or sweeping an unknown-resource repair alarm.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

An accepted empty test-policy root can disable policy for that test process, so this override must stay narrowly test-scoped. A truthy string is held intentionally; the diagnostic code does not normalize registry data into booleans.

## Plugin?

No. These changes belong to core lifecycle, persistence, or admission boundaries; a plugin cannot reliably replace those internal invariants.

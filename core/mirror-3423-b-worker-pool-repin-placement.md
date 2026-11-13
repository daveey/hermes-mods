# Worker pool repins: live-registry checks and hub:read placement

Patches: **3042, 3043, 3046**.

## Behavior
- A pool repin is refused when no eligible machine matches the full coordination-label target in the live registry; policy and events are retained on `no_machine` and eligible machines are reported.
- A repin never uses a stale registry fallback; a failed registry refresh preserves the reservation and refuses.
- `hub:read` declared access is now Board-placeable alongside `hub:write`; each needs its own attestation. SSH requirements remain unsupported.

## How it works
- 3042 resolves the target against the live registry before the Board compare-and-swap in `kanban_worker_repin.py` and drops the unused `origin_intent` from `queue_decision`.
- 3043 refuses when the registry came from the stale fallback.
- 3046 extends `BOARD_PLACEABLE_ACCESS` to include `hub:read`.

## Code
- `hermes_cli/kanban_worker_repin.py`
- `hermes_cli/kanban_worker_placement.py`
- `hermes_cli/kanban_placement_coordination.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_worker_repin.py`: `test_cli_started_pool_requires_live_complete_label_match`, `test_pool_registry_unavailable_refuses_without_write`, `test_pool_failed_registry_refresh_preserves_reservation`.
- `tests/hermes_cli/test_kanban_coordination_placement.py`: `test_declared_hub_access_is_board_placeable`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
Deploy Board support before extending the placeable set; otherwise enrolled cards queue forever as unsupported.

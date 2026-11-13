# Test hygiene: shared helpers, deflakes and pins

Patches: **3198, 3199, 3202, 3206, 3229**.

## Behavior
No runtime behavior change; test-only patches.
- Placement ROUTE/decide helpers moved into `tests/hermes_cli/_placement_helpers.py` (3198).
- The hosted-room cancel test observes the interrupt on receipt rather than racing (3199).
- A test pins that the CI premise ignores `run_heads` order (3202).
- Sibling group peer suites set member config through the fixture and boot via `_member` (3206).
- The lockguard stranded-fd scan matches only the board's own sidecars (3229).

## How it works
Each patch edits tests only; 3198 extracts a helper module, 3199 swaps a sleep/poll for an observation of the interrupt, 3229 narrows the deleted-sidecar match.

## Code
- `tests/hermes_cli/_placement_helpers.py`
- `tests/hermes_cli/test_kanban_placement.py`
- `tests/hermes_cli/test_kanban_placement_myapp_marker.py`
- `tests/tui_gateway/test_hosted_room_driver_runtime.py`
- `tests/hermes_cli/test_kanban_awaiting.py`
- `tests/gateway/test_session_group_peer_approvals.py`
- `tests/gateway/test_session_group_peer_daemons.py`
- `tests/gateway/test_session_group_peer_setup_daemons.py`
- `tests/hermes_cli/test_kanban_lockguard.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_awaiting.py`: `test_ci_premise_ignores_head_order`.
- `tests/hermes_cli/test_kanban_lockguard.py`: `test_deleted_sidecars_ignores_unrelated_deleted_files`.
- 3198, 3199 and 3206 modify existing tests without adding new names.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.

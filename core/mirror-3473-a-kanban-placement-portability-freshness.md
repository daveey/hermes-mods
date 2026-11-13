# Kanban placement: portable forward footer and registry clock-skew tolerance

Patches: **3436, 3439**.

## Behavior
- Cards forwarded with parents carry a parent-handoff footer spelled as the `cross_task` read call (`action='read'`), which needs only the coordination-read capability. The target gateway no longer keeps the card home because the footer looked like an unknown coordination action.
- Registry freshness tolerates 5 s of future clock skew between the Board stamp and the local clock; a stamp further ahead stays stale.
- Named machines (heavy routing) use the same freshness window as pool and label-set liveness. A stale or unstamped named machine is not pinned; legacy routing stands.
- Docstrings record the Board-outage behavior: after the freshness window every entry is stale, so deny-route cards go worker-first and portable cards stay local.

## How it works
3436 changes the footer text in `kanban_placement_forward.py`. 3439 adds `SKEW_S` to `_fresh` in `kanban_placement_coordination.py` and routes `_machine_picks_up` for named machines through `label_machines` with an empty label set; related placement modules and fixtures were adjusted to stamp `last_seen`.

## Code
- `hermes_cli/kanban_placement_forward.py`
- `hermes_cli/kanban_placement_coordination.py`
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_hub_admission_placement.py`
- `hermes_cli/kanban_worker_placement.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_placement_forward_footer.py`: `test_parent_handoff_footer_is_a_portable_read`, `test_footer_names_the_read_action_of_the_cross_task_tool`, `test_card_without_parents_has_no_footer_and_no_coordination_need`, `test_forwarded_card_with_parents_is_portable_to_an_attested_pool`.
- `tests/hermes_cli/test_kanban_placement_freshness.py`: `test_fresh_allows_small_future_skew`, `test_skew_allowance_is_small_next_to_the_window`, `test_queue_liveness_and_attestation_share_the_skew_allowance`, `test_named_machine_uses_the_same_freshness_window`, `test_stale_named_heavy_route_keeps_legacy_routing`.
- Fixture updates only in `test_kanban_placement.py`, `test_kanban_pool_carried_inputs.py`, `test_kanban_sync_schema_guard.py`, `test_kanban_worker_node.py`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Registry fixtures feeding named-machine routing must now stamp `last_seen`, otherwise the machine reads as stale.

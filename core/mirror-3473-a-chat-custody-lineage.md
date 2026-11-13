# Chat custody: lineage ledger reads every row; managed-worker orphan wording

Patches: **3434–3435**.

## Behavior
- The lineage ledger status read sees every non-terminal row of every id. An `unknown` admission beside an earlier `queued` row on the same id is no longer masked, so the gateway does not claim a chat it must not own.
- The gateway reads the same parent chain as the legacy leg, so an `unknown` or `queued` row on an intermediate compaction parent yields the same decision on both legs.
- Documentation/test cleanup for managed-worker recovery: keyed orphans wait for a later run of their route or their owner's keeper; keyless orphans wait for a gateway restart only (a Desktop-only host never re-adopts them).

## How it works
3435 replaces the one-row-per-id query with `_LINEAGE_PENDING_SQL` (no `LIMIT 1`): any status outside the forwardable set answers (sorted); otherwise the least forwardable status of the first sorted id. `_gateway_holds_work` expands its ids through `kanban_leg_custody.with_parents` (same walk and limit as the legacy leg). 3434 is a comment-only fix in `managed_worker.py` plus an explicit `if recover is None:` default in a test helper.

## Code
- `gateway/kanban_tui_owner.py`
- `tui_gateway/kanban_leg_custody.py`
- `agent/managed_worker.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_kanban_tui_sibling_custody.py`: `test_nit1_one_unknown_on_tip_or_root_answers_one_rule_on_both_legs`, `test_remote_owner_collects_for_a_chat_both_legs_hand_to_the_gateway`, `test_an_unknown_beside_a_queued_row_on_one_id_is_never_masked`, `test_nit3_an_unknown_on_an_intermediate_parent_keeps_the_chat_on_both_legs`, `test_nit3_forwardable_work_on_an_intermediate_parent_alone_is_claimed_by_the_gateway_once`, `test_the_gateway_walks_as_many_parents_as_the_legacy_lineage`.
- `tests/gateway/test_managed_worker_route_key.py`: helper edit only; the comment change in 3434 is untested.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.

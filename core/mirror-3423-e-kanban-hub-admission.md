# Kanban hub admission: refusal reasons, claim-time denies, home routing

Patches: **3182, 3207, 3215, 3221**.

## Behavior
- Hub-admin lookalike names no longer open a hub write quote; a path-qualified hub-admin stays home.
- A deny with no worker able to claim is kept home with the action reason; the show line names the home override; the home route returns the normalized declared `requires` list (a string `requires` stays home as a list).
- Each hub final-gate refusal branch has its own reason and stage, and the queue line names the branch. Census SQLite reads no longer scale with column count (one-row `table_columns`).
- A claim-time hub admission deny no longer strands a card on the hub: a deny with a portable fleet probe moves to the worker queue, hub-local probes still keep the card, and an empty portable probe list makes a bin probe hub-local. The worker-first self-check re-places denied cards left ready on the hub, skips cards that changed while checked, and still screens unjudged cards after a failed check.
- A re-pickup prior-deny skip names its home reason.

## How it works
- 3182 tightens the hub-admin regex and returns the declared list from the home route.
- 3207 splits refusals in `kanban_admission*.py` / `kanban_queue_reason.py`, adds a one-row column read in `machine_metrics.py`, and updates `docs/worker-first-v1.md`.
- 3215 adds the claim-deny placement logic and the self-check repin pass.
- 3221 makes the pickup skip text carry the home reason.

## Code
- `hermes_cli/kanban_hub_admission_claim.py`
- `hermes_cli/kanban_hub_admission_pickup.py`
- `hermes_cli/kanban_hub_admission_placement.py`
- `hermes_cli/kanban_placement_coordination.py`
- `hermes_cli/kanban_admission.py`
- `hermes_cli/kanban_admission_origin.py`
- `hermes_cli/kanban_admission_pools.py`
- `hermes_cli/kanban_queue_reason.py`
- `hermes_cli/machine_metrics.py`
- `hermes_cli/kanban_worker_first_selfcheck.py`
- `docs/worker-first-v1.md`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- Added across `tests/hermes_cli/test_kanban_coordination_placement.py`, `tests/hermes_cli/test_kanban_hub_admission_claim.py`, `tests/test_kanban_hub_admission_placement.py` (3182): `test_hub_admin_lookalike_names_do_not_open_a_hub_write_quote`, `test_a_deny_no_worker_can_claim_is_kept_home_with_the_action_reason`, `test_home_route_returns_the_normalized_declared_requires`, `test_decision_card_with_string_requires_stays_home_with_a_list`, `test_show_line_names_the_home_override`.
- `tests/hermes_cli/test_kanban_hub_gate_convoy.py` and related admission/sampler/CPU-gate tests (3207): `test_each_refusal_branch_names_its_reason_and_stage`, `test_census_sqlite_steps_do_not_scale_with_column_count`, `test_table_columns_reads_every_column_in_one_row`, `test_lock_held_lookups_read_columns_through_table_columns`, `test_final_gate_refusals_each_have_their_own_reason`, `test_queue_line_names_the_branch`.
- `tests/hermes_cli/test_kanban_claim_deny_selfcheck.py` (3215): `test_a_claim_deny_with_a_portable_fleet_probe_moves_to_the_worker_queue`, `test_hub_local_probes_still_keep_a_denied_card`, `test_an_empty_portable_probe_list_makes_a_bin_probe_hub_local`, `test_self_check_repins_a_denied_card_left_ready_on_the_hub`, `test_self_check_leaves_other_cards_on_the_hub`, `test_a_failed_self_check_still_screens_the_unjudged_cards`, `test_self_check_skips_a_card_that_changed_while_checked`.
- `tests/hermes_cli/test_kanban_coordination_placement.py`, `tests/hermes_cli/test_kanban_hub_admission_pickup.py` (3221): `test_path_qualified_hub_admin_stays_home`, `test_apply_skip_text_names_the_home_reason_when_no_queue_takes_it`, `test_denied_text_without_home_or_once_re_placed`, `test_a_re_pickup_prior_verdict_skip_keeps_the_home_reason`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.

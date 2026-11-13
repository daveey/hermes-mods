# Kanban parks and requeued publishers

Patches: **3117, 3118, 3139, 3141**.

## Behavior
- A requeued publisher's earlier PR needs a recorded handoff to the requeued claim; otherwise it fails closed. A granted continuation's own requeue chain (same profile, only skipped runs between) hands the PR on.
- A ci/deploy awaiting park's premise includes the 40-hex head SHAs named in its ref and wake_check, so each new head after a red CI is a new premise while a stuck head still escalates.
- A worker re-parking a hub card with a wake_check that reads the board relays the probe host to the hub instead of reading the worker's own board.

## How it works
- 3117 requires a handoff event (kinds the respawn guard honours), probes the schema once per call and skips the scan with no requeue; 3118 adds the same-profile skipped-run chain rule.
- 3139 extends `premise_hash` with the sorted SHAs (hashed only for run kinds); `gate()` passes the wake_check.
- 3141 adds `kanban_wake_check_hub_relay.relay_host`; the worker skips registering the probe and the dry run, and the hub restores it from the synced park event. Own cards, non-hub homes, ssh/hub-admin probes and worker-local probes are unchanged.

## Code
- `hermes_cli/kanban_requeued_publisher.py`, `hermes_cli/kanban_awaiting.py`
- `hermes_cli/kanban_wake_check_hub_relay.py`, `hermes_cli/kanban_cli_park.py`, `hermes_cli/kanban_timed_park.py`, `hermes_cli/kanban_continuation.py`, `hermes_cli/kanban_db_schedule.py`, `hermes_cli/kanban.py`
- `tools/kanban_tools.py`, `tools/kanban_tools_park_args.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_core_update_requeue_handoff.py`: `test_earlier_pr_needs_a_handoff_before_the_requeued_claim`, `test_which_events_hand_an_earlier_pr_to_the_claim`, `test_earlier_real_run_with_unreadable_end_holds`, `test_earlier_pr_with_an_unreadable_first_start_holds`, `test_held_card_without_a_requeue_is_decided_before_any_scan`, `test_schema_is_probed_once_per_call`, `test_granted_continuation_that_restates_its_pr_and_is_requeued_again_resumes`, `test_requeued_chain_breaks_at_a_real_run`, `test_requeued_chain_needs_the_same_profile`, `test_requeued_chain_does_not_carry_an_earlier_crashed_publisher`.
- `tests/hermes_cli/test_kanban_awaiting.py`: `test_ci_parks_on_new_heads_are_new_premises`, `test_ci_parks_on_one_head_still_escalate`, `test_ci_premise_hashes_heads_only_for_run_kinds`.
- `tests/hermes_cli/test_kanban_wake_check_hub_relay.py`: `test_a_probe_that_reads_a_board_on_the_host_that_runs_it`, `test_a_probe_with_no_unrouted_board_read_stays_local`, `test_only_a_hub_home_takes_the_relay`, `test_a_worker_reparking_a_hub_card_on_a_board_probe_relays_it_to_the_hub`, `test_a_worker_local_probe_on_a_hub_card_stays_on_the_worker`, `test_the_workers_own_card_keeps_its_board_probe`, `test_a_relayed_probe_skips_the_dry_run_on_the_worker`, `test_the_cli_skips_the_dry_run_only_when_every_card_relays`.
- `tests/tools/test_kanban_park_hub_relay_entry.py`: `test_the_tools_relay_a_hub_cards_board_probe_without_a_dry_run`, `test_the_cli_relays_a_hub_cards_board_probe_without_a_dry_run`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A real run between the earlier PR comment and the claim still consumes the handoff, and a crashed publisher's PR is never carried.

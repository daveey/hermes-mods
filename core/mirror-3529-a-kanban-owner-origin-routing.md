# Kanban capability-owner requests: placeholder exemption and origin-link fixes

Patches: **3506, 3508**.

## Behavior
- A capability-owner create no longer refuses as `create_overlap` against the requester's own bound origin placeholder or cards parked on the waiting card; genuine same-title twins on the origin still refuse.
- The holder side also skips a local card forwarded to, or awaiting, the waiting card, even from older requesters that send no `distinct_from`.
- A rejected owner request makes the wait check exit 0 immediately with the refusal text (code, matches, error) so the card wakes to re-plan, instead of exiting 2 as a broken probe; one rejected request wakes even while another is pending.
- The origin-link owner answer means "sync enabled and not recorded stopped", and survives the done-row merge back to the origin.
- `OWNER_WRITTEN` has a single source of truth.

## How it works
3506 adds `bound_cards()` to `kanban_capability_routing.py` feeding `overlap_exclusions`, extends `kanban_overlap.py` for capability-owner creates, and changes `wait_check` for failed terminal requests. 3508 makes `kanban_origin_creation.py` import the owner's constant from `tools/kanban_tools_board_delivery.py`, corrects the docstring and comment wording, and pins the behavior with tests.

## Code
- `hermes_cli/kanban_capability_routing.py`
- `hermes_cli/kanban_overlap.py`
- `hermes_cli/kanban_origin_creation.py`
- `tools/kanban_tools_board_delivery.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_capability_owner_origin_mirror.py`: `test_placed_card_routes_host_need_to_its_origin_and_the_owner_is_created`, `test_genuine_twin_on_the_origin_gateway_still_refuses_the_owner`, `test_bound_cards_reads_the_forward_footer_and_local_parks`, `test_holder_skips_its_placeholder_even_without_distinct_from`, `test_rejected_owner_request_wakes_with_the_refusal_not_a_broken_probe`, `test_one_rejected_request_wakes_even_while_another_is_pending`.
- `tests/hermes_cli/test_kanban_capability_routing.py`: `test_rejected_owner_request_wakes_now_with_the_refusal`.
- `tests/hermes_cli/test_kanban_origin_notify.py`: `test_owner_answer_means_enabled_and_not_recorded_stopped`, `test_owner_answer_survives_the_hubs_done_row_to_the_origin`.
- `tests/tools/test_kanban_board_delivery.py`: `test_owner_written_wire_value_has_one_source`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- An enabled sync that has not yet started still answers owner-written; the link row waits in the outbox and goes out with the first round.
- `owner_status` still refuses a park on a dead request.

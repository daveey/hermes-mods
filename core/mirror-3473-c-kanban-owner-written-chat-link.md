# Kanban: placed create reports `chat_link: owner_written` only when the owner says so

Patches: **3466–3468**.

## Behavior
- A placed Kanban create reports `chat_link: owner_written` only when the owning gateway answers that it wrote the origin chat link. Previously this was inferred from the chat id's shape, which was untrue for an older owner or one with Board sync off or stopped.
- With no answer yet (queued request) or an older owner, the note falls back to `queued_local` ("the link waits here").
- A desktop-hash chat proven by an ancestor's `created` link now gets `owner_written` rather than `queued_local`.
- The note keeps the gateway id verbatim ("owner gateway worker-4 ...", not "Worker-4"); only the fixed words "owner gateway" are capitalized.
- `_link_origin` still skips every chat on the owner's gateway whatever its profile; the reason is now documented, and a test pins the skip.

## How it works
- 3466: the owner's courier result carries `origin_chat_link: "owner_written"` (`kanban_origin_creation.origin_link_fields`) only when it holds the origin chat's unreleased `created` link and its sync is enabled and running. `await_task_id` returns the owner's result through `result_out=`, `kanban_placement.owner_chat_link` turns it into `placed.chat_link`, and the board-delivery note reads only that.
- 3467: capitalization fix in `_chat_link_by_owner`.
- 3468: docs-only. `_link_origin` comments name `_owner_hub_links`; the owner_written note names the owner's core/sync requirement and the queued case.

## Code
- `hermes_cli/kanban_origin_creation.py`
- `hermes_cli/kanban_placement.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_board_delivery.py`
- `tests/hermes_cli/test_kanban_origin_notify.py`
- `tests/hermes_cli/test_kanban_placement_owner_link.py`
- `tests/tools/test_kanban_board_delivery.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_origin_notify.py`: `test_courier_create_tells_the_origin_it_wrote_the_link`, `test_courier_create_on_an_owner_whose_sync_is_stopped_does_not_claim_the_link`, `test_courier_create_on_an_owner_without_board_sync_does_not_claim_the_link`, `test_courier_adoption_tells_the_origin_it_wrote_the_link`.
- `tests/hermes_cli/test_kanban_placement_owner_link.py`: `test_the_owners_answer_from_hub_outcomes_reaches_the_caller`, `test_the_owners_answer_from_the_done_replica_row_reaches_the_caller`, `test_an_owner_answer_without_the_field_claims_nothing`, `test_await_task_id_without_result_out_is_unchanged`.
- `tests/tools/test_kanban_board_delivery.py`: `test_create_placed_on_an_owner_that_did_not_write_the_link_says_the_link_waits_here`, `test_create_queued_on_a_target_while_stopped_says_the_link_waits_here`, `test_create_note_wording_and_its_stopped_sync_rewrite_share_one_source`, `test_queued_rewrite_lands_after_the_exact_head_for_any_separator`, `test_create_placed_from_a_proven_desktop_hash_chat_says_the_owner_wrote_the_link`.
- 3467 and 3468 add no new test names (3467 adjusts an existing assertion; 3468 is docs-only).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- During a mixed-version fleet, an owner on older core never reports `owner_written`; callers see `queued_local`.
- The `_link_origin` skip of the owner's gateway exists because the owner's reconcile releases any `created` row it does not derive itself on its next pass.

# Kanban placement markers and pool queue

Patches: **3144, 3145, 3157**.

## Behavior
- A required-check name such as "macOS state required" in a review card body is no longer read as Mac work, so ship-filed review cards join the pool queue instead of staying on the filing worker or going through the hub admission judge.
- On a gateway whose registry entry carries `linux` and is not the hub, a built-in local-marker hit (keychain, Chrome, a Desktop chat, the macOS check, a device skill) joins the worker queue with `capability:tailnet-exec` for Mac/Desktop markers and a note naming the route to the real need. Operator patterns, other portability reasons and an unreadable registry are unchanged.

## How it works
- 3144 adds a negative lookahead to `MACOS_MARKER` (the OS word followed by " state required"); `APPLE_CONTEXT_MARKERS` reuses it.
- 3145 moves the product-marker cases to their own test file because `test_kanban_placement.py` hit its line cap.
- 3157 makes built-in marker hits queue for `worker_queue` on such gateways, via CLI create, direct `create_task` and the tool, with marker groups.

## Code
- `hermes_cli/kanban_placement.py`, `hermes_cli/kanban_placement_diagnostics.py`, `hermes_cli/kanban_worker_placement.py`
- `tools/kanban_tools_placement.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_placement.py`: `test_ship_review_fixture_quotes_the_required_check_name`, `test_ship_review_card_on_a_pool_worker_joins_the_pool_queue`, `test_ship_review_card_on_the_hub_is_no_marker_hit_and_never_consults_admission`, `test_the_required_check_name_is_not_a_macos_marker`, `test_macos_followed_by_other_words_still_matches`.
- `tests/hermes_cli/test_kanban_placement_myapp_marker.py`: `test_myapp_skill_or_repo_in_the_body_does_not_pin_the_card`, `test_myapp_title_with_linux_ci_only_work_goes_to_the_target`, `test_myapp_title_with_apple_or_device_work_stays_local`, `test_replaced_marker_list_keeps_product_name_pinning`.
- `tests/hermes_cli/test_kanban_pool_queue.py`: `test_a_keychain_card_from_a_pool_worker_joins_the_pool_with_the_mac_reach_label`, `test_a_chrome_card_from_a_pool_worker_joins_the_pool_and_names_the_hub_route`, `test_a_desktop_chat_card_from_a_pool_worker_carries_the_mac_reach_label`, `test_a_device_skill_card_from_a_pool_worker_carries_the_mac_reach_label`, `test_a_ship_review_card_naming_the_macos_check_joins_the_pool`, `test_cli_create_of_a_keychain_card_on_a_pool_worker_joins_the_pool`, `test_direct_create_task_of_a_keychain_card_on_a_pool_worker_joins_the_pool`, `test_every_built_in_local_marker_has_a_group`, `test_markers_are_unmeetable_only_on_a_linux_non_hub_registry_entry`, `test_hub_keychain_card_still_asks_the_admission_judge`, `test_pool_worker_keeps_a_marker_card_when_the_registry_is_unreadable`, `test_pool_worker_keeps_an_operator_marker`, `test_pool_worker_leaves_other_keeps_unchanged`.
- Also in that file by name: a laptop keychain card stays local.
- Fixture: `tests/hermes_cli/fixtures/ship_review_card_*.json` (a real review card text).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Previously a local-marker card on a worker became a local row that only its creator could claim.

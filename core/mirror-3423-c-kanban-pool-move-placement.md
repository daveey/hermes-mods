# Kanban pool-move and pickup-stall placement fixes

Patches: **3054, 3055, 3089, 3101, 3105**.

## Behavior
- Remote-tip refresh for URL-pushed branches works without inheriting stdin and prioritizes branch destinations within the refresh cap.
- A pickup-stall operator move to the worker pool maps declared access requirements (hub write/read, ssh access) to the hyphenated capability labels workers attest.
- The pool move is refused when the declared access cannot be placed by the Board, or when the stored `requires` is not a JSON list of strings (`task_metadata_invalid`). A move to a single machine is unchanged.

## How it works
- 3054 refreshes URL-pushed remote tips with DEVNULL stdin in `kanban_move_pool`; 3055 orders destinations so branches are not starved by earlier heads.
- 3089 adds `requirement_label()` in `kanban_placement_coordination` and uses it at all four need sites (pickup-stall, repin, alarm placement, repin notice).
- 3101 makes `scope_refusal` consult `board_refusal(requires)` before pool eligibility.
- 3105 adds `malformed_requires()` mirroring the Board's string-list reader.

## Code
- `hermes_cli/kanban_move_pool.py`
- `hermes_cli/kanban_pickup_stall.py`
- `hermes_cli/kanban_pickup_stall_repin.py`
- `hermes_cli/kanban_placement_coordination.py`
- `hermes_cli/kanban_repin_notice.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_pool_queue.py`: `test_url_push_moved_tip_without_ancestral_tracking`, `test_remote_failure_pins_without_reading_stdin`, `test_remote_refresh_caps_and_stdin`, `test_url_push_refresh_prioritizes_branch_over_earlier_heads`.
- `tests/hermes_cli/test_kanban_pickup_stall_repin.py`: `test_declared_hub_write_needs_the_hyphenated_label_a_worker_attests`, `test_every_need_site_maps_access_names_to_their_attested_labels`, `test_declared_ssh_access_refuses_the_pool_queue_with_the_board_reason`, `test_a_requires_the_board_cannot_read_refuses_the_pool_queue_only`, `test_a_requires_the_board_reads_still_takes_the_pool_queue`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.

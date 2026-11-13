# Kanban placement: labels shape guard and forward-evidence fixes

Patches: **3522–3523**.

## Behavior
- A malformed registry `labels` value (int, string such as `linux` or `linux-arm`, dict, tuple, or a list containing non-strings) is no longer counted as a linux label and no longer raises; the check fails closed.
- A card body with a stray unclosed evidence open marker no longer has its own text swallowed, so home-bound commands in the body are still seen and the card is not judged portable.
- The forwarded card's footer names the board of the database actually opened, including when only the DB pin is set; a pinned file outside the board layout falls back to the active board.

## How it works
- 3522: `markers_unmeetable_here` uses the same list-of-str guard as `label_machines`; the inline `carries()` closure is hoisted to module-level `registry_carries(entry, wanted)` and reused by both sites.
- 3523: `_own_text` neutralises every open evidence marker after the body's last close marker; complete fences are kept. `opened_board(conn)` reads the board slug from the opened file via `board_slug_for_path` instead of `get_current_board()`.

## Code
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_placement_forward.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_placement_labels_shape.py`: `test_malformed_labels_are_not_linux`, `test_linux_list_label_is_linux`, `test_registry_carries_shape`.
- `tests/hermes_cli/test_kanban_placement_forward_footer.py`: `test_unclosed_open_marker_in_child_body_does_not_swallow_its_own_text`, `test_complete_fence_in_child_body_is_kept`, `test_board_follows_the_kanban_db_pin_alone`, `test_board_pin_outside_the_board_layout_falls_back_to_the_active_board`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Only the strict list-of-str form of `labels` is honoured; anything else is treated as no labels.

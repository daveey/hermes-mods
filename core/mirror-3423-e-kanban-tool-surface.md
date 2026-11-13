# Kanban tool surface: completion receipt schema, wake summary, review wording

Patches: **3219, 3227, 3232, 3235**.

## Behavior
- `kanban_complete` advertises the `metadata.completion_receipt` v1 shape and when to send it; the advertised shape is the validator's shape and the description's example validates (3227).
- `kanban show` states what wakes a scheduled card on the status line (the wake check, with the deadline labeled a backstop; "wakes at" when no check). The JSON has a `wakes` object, unscheduled cards have none, worker context states the wake in one line, and the `kanban_show` tool returns `wakes` (3232).
- An in-review `review_required` card, even with a stale run id, is named pending review and never told it is not in review (3235).
- 3219 moves `_store_attachment` into the attach-url sibling module to stay under a file-line cap; no behavior change.

## How it works
- 3227 expands the schema text in `tools/kanban_tools_schemas.py`.
- 3232 adds `hermes_cli/kanban_wake_summary.py` and wires it through `kanban.py`, `kanban_db.py`, `kanban_db_context.py` and `tools/kanban_show.py`.
- 3235 changes wording in `kanban_review_required.py` and `tools/kanban_tools.py`.

## Code
- `tools/kanban_tools.py`
- `tools/kanban_tools_attach_url.py`
- `tools/kanban_tools_schemas.py`
- `tools/kanban_show.py`
- `hermes_cli/kanban_wake_summary.py`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_context.py`
- `hermes_cli/kanban_review_required.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_kanban_complete_receipt_schema.py`: `test_complete_schema_advertises_completion_receipt_and_when_to_send_it`, `test_advertised_shape_is_the_validators_shape`, `test_example_in_the_description_validates`.
- `tests/hermes_cli/test_kanban_show_wake.py`: `test_status_line_names_the_wake_check_and_labels_the_deadline_a_backstop`, `test_status_line_without_a_check_says_wakes_at`, `test_json_has_a_wakes_object`, `test_unscheduled_card_has_no_wake`, `test_worker_context_states_the_wake_in_one_line`, `test_kanban_show_tool_returns_wakes`.
- `tests/tools/test_kanban_review_required_wording.py`: `test_in_review_card_with_stale_run_id_is_named_pending_review`.
- 3219 has no tests.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.

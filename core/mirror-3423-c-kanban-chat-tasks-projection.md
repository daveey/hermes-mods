# Kanban chat-tasks projection check

Patches: **3061, 3062**.

## Behavior
- A `chat-tasks` command verifies that an origin card is visible through the chat task projection, with lineage, JSON output and qualified-identity expectation checks. A projection read error is reported as an error, not as absence.
- Profile-qualified projection identities are honored, including a named origin profile in the command.
- Create-time binding guidance points at projection verification instead of replica links.

## How it works
- 3061 adds `kanban_chat_tasks.py`, the parser entry and updated tool schema text.
- 3062 passes profile-qualified identities through `kanban_origin` and the new command.

## Code
- `hermes_cli/kanban_chat_tasks.py`
- `hermes_cli/kanban_origin.py`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_parser.py`
- `tools/kanban_tools_schemas.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_chat_tasks.py`: `test_unlinked_card_is_not_visible`, `test_linked_card_visible`, `test_qualified_identity`, `test_projection_read_error`, `test_real_lineage`, `test_local_qualified_identity`, `test_no_expect_and_text_output`, `test_lineage_error_is_not_absence`, `test_tool_guidance_names_projection`, `test_real_profile_qualified_projection`, `test_named_origin_profile_in_command`.
- `tests/hermes_cli/test_kanban_origin.py`: existing assertions updated.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.

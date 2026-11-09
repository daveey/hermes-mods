# Kanban adoption hints and explicit subscription targets

## Behavior

Patch **2067** (`bae8bbe067b0fd44a9a482076b87534da63a5f37`) makes an adoption-refusal hint name the current turn's chat instead of a different chat left in the backend's process environment. CLI `notify-subscribe --platform tui` prints the target chat title and warns when the caller is binding another chat.

## How it works

`adopt_next_step` reads `get_session_env` through `_turn_session_id` unless the caller supplied an explicit environment mapping. `print_tui_target` reads the profile's session database read-only, resolves compression tips and prints a warning for a different lineage. A plain shell or Kanban worker does not receive that warning; missing targets receive an explanatory label. The plain-shell case is tested. Kanban-worker suppression, missing/untitled targets, `HERMES_SESSION_KEY` precedence and compression-lineage equivalence are implemented but untested in this mirror pass.

## Code

- `hermes_cli/kanban_origin.py`: turn-local session selection.
- `hermes_cli/kanban.py`: TUI subscription output call site.
- `hermes_cli/kanban_notify_target.py`: read-only title/tip lookup and warning.

## Tests

`tests/hermes_cli/test_kanban_origin_adoption.py::test_chat_refusal_names_this_turns_chat_not_the_process_env` binds a turn ContextVar while another chat remains in the process environment and checks the refusal hint. `tests/hermes_cli/test_kanban_notify_subscribe_target.py` invokes the CLI parser/handler with real isolated databases to check matching-chat title output, different-chat warnings and plain-shell output. See [snapshot proof](mirror-2067-verification.md) for executed results. Live Desktop, terminal-tool and execute_code end-to-end flows are untested here.

## Applying

Use the full [README Applying recipe](README.md), through patch 2067. Private example chat identifiers and a fixture title are normalized in the public mail; three exact source/applied blob pairs document this privacy-only difference in the frozen policy.

## Gotchas

The warning does not reject or undo a subscription. Reading back a target identifier proves the subscription target, not that it was the user's intended chat or that a live notification was delivered. Compression-lineage equivalence is implemented but not covered by the new three CLI tests.

## Plugin?

No: this modifies core origin-hint construction and the CLI subscription handler.

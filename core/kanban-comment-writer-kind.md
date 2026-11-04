# Kanban comments record writer_kind

Patches: **1915** (`c2f066a6d0`, `[daveey] kanban: comments record writer_kind (agent/human); live notes say when an agent wrote them`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- `add_comment(writer_kind='agent'|'human', writer_session=...)` stores both on the commented event (unset for system writers; other values raise). The `kanban_comment` tool writes `agent` with its session id; CLI, slash command and dashboard write `human`. Injected live comments label agent lines `(agent chat, another session)`.

## How it works

See Code.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1915: The kanban_comment tool is always an agent, but nothing recorded it. David and his agents share one profile, so a chat agent's note reached the worker stamped 'default (another session)' under 'from the operator', and the ask-gate scored 'Please re-check (c) yourself' as David (agent-261aa31f49).

## Code

`hermes_cli/kanban.py`, `hermes_cli/kanban_db.py`, `hermes_cli/kanban_db_comment_ids.py`, `plugins/kanban/dashboard/plugin_api.py`, `tools/kanban_tools.py`, `tools/kanban_tools_notes.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/tools/test_kanban_comment_writer_kind.py`: `test_tool_comment_event_records_agent_writer`, `test_tool_comment_records_the_writing_session`, `test_cli_comment_event_records_human_writer`, `test_dashboard_comment_event_records_human_writer`, `test_unknown_writer_kind_is_rejected`, `test_agent_note_reaches_worker_with_agent_header_and_stamp`, `test_mixed_notes_keep_operator_header_and_stamp_each_line`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.

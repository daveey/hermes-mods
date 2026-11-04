# Outcome: an unviewed completion note is not an interrupted turn

Patches: **1840** (`8e9d24471c`, `fix(outcome): an unviewed completion's note is not an interrupted turn [daveey]`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- Trailing completion notes for finished work no longer make the stored outcome read `unresolved{interrupted}`: the chat's own last turn stays its last word. A note for an unfinished delegation reads `unresolved{subagent_killed}`. The outcome is refreshed after a note is written, off the admission loop.

## How it works

`hermes_state_outcome.py` recognizes completion-note user rows; `gateway/session_completion_notes.py` and `tools/process_registry_notifications.py` refresh the outcome after writing one.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1840: A completion that arrives with no viewer attached is written into the chat as a note and runs no turn by design (#134889 LC-7). The stored outcome read that user row as unresolved{interrupted}, so chat 20261025_073850_55b17f (its delegation cut by a gateway restart) stayed red with no possible resolution, and nothing refreshed the outcome after a note since no turn ended.

## Code

`gateway/session_completion_notes.py`, `hermes_state_outcome.py`, `tools/process_registry_notifications.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/gateway/test_unviewed_completion_notes.py`: `test_a_note_refreshes_the_stored_outcome_without_reading_as_an_interrupted_turn`
- `tests/hermes_state/test_session_outcome.py`: `test_a_finished_completion_note_leaves_the_chats_own_last_turn_as_its_word`, `test_an_unfinished_delegation_note_is_a_stopped_subagent_not_an_interrupted_turn`, `test_an_unfinished_note_under_a_finished_one_still_names_the_stopped_subagent`, `test_a_finished_note_does_not_break_a_declaration_or_hide_a_lost_request`, `test_a_typed_row_that_merely_mentions_completion_note_is_still_a_request`, `test_unfinished_reads_the_producers_own_titles`, `test_unfinished_agrees_with_the_title_producer`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.

# delegate_task steer/stop on a just-finished child

Patches: **1817** (`d1a8293101`, `fix(delegate): steer/stop on a just-finished child answers already_finished, not an error [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- Steering or stopping a subagent that already finished answers `already_finished` instead of "No live subagent".

## How it works

`tools/delegate_tool_registry.py` keeps finished children addressable for control replies.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1817: Problem: delegate_task(action='steer') returned "No live subagent 'sa-N-…' in this conversation's spawn tree" in 3 sessions (20261031_064851_d0f106, 20261031_222913_405ade x2, 20261101_012339_5a8ef0); the coach filed it as a Hermes failure (t_01a19fbe9374afb7).

## Code

`tools/delegate_tool_registry.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/tools/test_delegate_finished_child_control.py`: `test_finished_child_names_running_batch_siblings_and_list_shows_it`, `test_finished_child_resolves_through_durable_session_after_parent_rebuild`, `test_finished_foreign_child_stays_an_error`, `test_model_facing_steer_after_real_child_run_returns_already_finished`, `test_steer_finished_owned_child_is_not_an_error`, `test_steer_while_child_is_finishing_is_not_an_error`, `test_stop_finished_owned_child_is_not_an_error`, `test_unknown_id_stays_an_error`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.

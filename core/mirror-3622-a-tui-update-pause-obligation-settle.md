# TUI update pause: killed notify jobs reported once from the marker

Patches: **3582–3583**.

## Behavior
- When a core-update pause kills a chat's notify jobs, each killed job's receipt is reported once, from the pause marker. After an unclean death (no exit journal) the next Desktop backend no longer re-arms a duplicate notice.
- A job that had already exited before the kill, and a pause that a user Stop beats, keep their delivery obligation so the notice is not lost.
- A managed worker of a Desktop chat intentionally owes its finished notice to the Desktop keeper (docstrings corrected).

## How it works
3582 makes `boundary_pause_hook` in `tui_gateway/update_pause.py` settle the obligation of jobs it actually killed (`kill_state` "killed"), with supporting edits in `completion_obligation.py` and `process_registry_obligations.py`. 3583 corrects the timing: the hook only records killed ids on the session (`_update_pause_settle`, dropped with the pause by `_drop_pause`), and `pause_stands` settles them only if the pause stands or a backend exit keeps the paused marker.

## Code
- `tui_gateway/update_pause.py`
- `tui_gateway/completion_obligation.py`
- `tools/process_registry_obligations.py`
- `tests/tui_gateway/test_tui_completion_obligation.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tui_gateway/test_tui_completion_obligation.py`: `test_managed_worker_of_a_desktop_chat_owes_its_finished_notice_to_the_desktop_keeper`, `test_update_pause_kill_is_reported_once_from_the_marker` (orderly and unclean exit), `test_update_pause_keeps_the_obligation_the_marker_does_not_carry` (already-exited and user-Stop races), `test_update_pause_a_later_stop_beats_keeps_the_obligation`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
A death between the pause hook and the end of the turn can still re-arm a duplicate notice beside the marker report, but not lose the notice.

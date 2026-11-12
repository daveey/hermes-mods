# One user row per hold-queued prompt.submit

Patches: **2575, 2576**.

## Behavior

- A `prompt.submit` accepted while the core-update admission hold is active (`queued_reason=core_update`) is written to `state.db` at accept time as a user row marked `_queued_prompt`. When the hold cleared and the queue drained, a second identical user row was appended at the end and the accept row was deactivated: one submit, one turn, two user rows.
- Now, when nothing landed after the accept row, the dispatching envelope adopts that row in place: it stays the one active row, loses the queued marker, keeps its other display metadata, and the turn uses it as its own user row so the turn flush writes no second copy. A restart after the drain no longer retires it.
- Unchanged fallbacks, which still re-append and deactivate as before: any later active row exists (the busy-queue shape, so alternation stays user, assistant, user), the envelope is still queued rather than dispatching (it keeps its marker so a restart retires it), or the store errors.
- 2576: after a mid-turn compression rotated the session to a continuation, the accept row lives in the parent while the drained turn flushes under the continuation. In that case no in-place adoption happens; the row is re-appended into the continuation and the parent's copy is retired, so the turn's user row is not stranded in the closed parent.

## How it works

- 2575: `hermes_state_message_content.adopt_queued_user_row_in_place(db, session_id, row_id)` runs one guarded `UPDATE` that drops the `_queued_prompt` key (`NULLIF(json_remove(...), '{}')`) only for an active user row that is still marked and has no later active row in the session; it rejects non-int, bool and non-positive ids and returns the rowcount. `session_auto_continue._adopt_queued_user_row_in_place` resolves the live row id (follows a compaction clone via `resolve_active_row_id`), calls it, and returns the adopted slot dict (marker removed from the display metadata). `_replace_queued_user_row_for_turn` tries it first when `is_dispatching`, and falls through to the existing append-then-deactivate path on `None`.
- 2576: `_adopt_queued_user_row_in_place` returns `None` when the row's owner key differs from `_submit_row_target_key(session)`, the session the turn will flush under now.

## Code

`hermes_state_message_content.py`, `tui_gateway/session_auto_continue.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_state/test_adopt_queued_user_row.py` (the SQL guard against a real `SessionDB`), `tests/tui_gateway/test_update_hold_submit_queue.py` (real accept-time persist, claim, dispatch and drain over a real `state.db` with only the turn body recorded: one row, adopted slot, no retire after drain; later-row control; compaction clone; still-queued envelope keeps its marker), `tests/tui_gateway/test_submit_row_session_target.py` (2576: busy-queued prompt accepted before a rotation is re-appended into the continuation, not adopted in the parent). Entry path: `server._persist_queued_user_row`, `_replace_queued_user_row_for_turn` and the hold-timer drain callback, and the SQL helper directly. A real `prompt.submit` RPC through a running server and the busy-queue path beyond the rotation case are **untested** here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Rows already duplicated by the old drain are not repaired.
- Adoption is skipped whenever any later active row exists, including an assistant reply; that case still yields the re-placed row and a deactivated accept row.

## Plugin

Core change; no plugin required.

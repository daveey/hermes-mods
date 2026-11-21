# Gateway turns: a refused submit never clears a live turn's marker

Mirror snapshot 5406; ordinals 5331, 5332, 5333.

Entries:

- **5331**: fix(tui_gateway): a refused submit never clears a live turn's marker or state
- **5332**: fix(tui_gateway): review: live-turn record means running; no stray marker retire
- **5333**: fix(tui_gateway): review r3: concluding stamp only when the retire runs

## Behavior

- A second automatic submit on a chat that already had a live turn was refused at admission, and the refusal's error frame retired the turn marker by session key, deleting the *live* turn's marker (and, across processes sharing a home, a sibling process's marker). Update-pause hooks then saw "no turn" and the pending update stayed blocked by that turn's admission reservation.
- **5331**: the ownership refusal passes `retire_marker=False`; the non-concluding retire deletes only a marker written by this process or by a dead writer; a refusal on a record with an admitted live turn leaves running state, the admission ref, the in-flight turn and the client frame alone.
- **5332**: review fixes: the live-turn record is dropped when running is cleared; missing-agent, agent-init and admission-dispatch failures also skip marker retirement; the "live turn lost its marker" warning skips a turn retiring its own marker.
- **5333**: the concluding stamp is set only when the retire actually runs, so a kept marker deleted by a sibling still warns.

## How it works

- `tui_gateway/turn_marker.py`, `prompt_turn.py`, `turn_admission.py`, `methods_prompt*.py`, `session_auto_continue.py`, `session_notifications.py` and `update_pause.py` carry the ownership checks; `hermes_cli/admission_fence.py` and `backend_retirement.py` are adjusted for the shared reservation label.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/admission_fence.py`
- `hermes_cli/backend_retirement.py`
- `hermes_cli/kanban_sync_quarantine_release_live.py`
- `tui_gateway/prompt_turn.py`
- `tui_gateway/session_auto_continue.py`
- `tui_gateway/session_notifications.py`
- `tui_gateway/turn_admission.py`
- `tui_gateway/turn_marker.py`
- `tui_gateway/update_pause.py`
- `tui_gateway/methods_prompt.py`
- `tui_gateway/methods_prompt_admission.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/hermes_cli/test_kanban_receipt_quarantine_followups.py`
- `tests/hermes_cli/test_reservation_holder_label.py`
- `tests/tui_gateway/test_turn_marker_ownership.py`
- `tests/tui_gateway/test_turn_marker_ownership_admission.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- The spared-refusal branch is defensive: current callers claim `running` before submitting.
- Cross-process protection relies on the writer-pid field recorded in the marker.

## Why core instead of a plugin

Turn markers, admission and the update-pause contract are core gateway state shared by all clients.

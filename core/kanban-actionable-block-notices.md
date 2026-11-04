# Blocked notices show the card's exact human ask

Patches: **1821** (`2050733bc6`, `fix(kanban): blocked notices show the card's exact human ask, not clipped event prose [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- Local (`session_notifications`), remote (`kanban_remote_notices`) and gateway (`kanban_watchers_notifier`) blocked notices render the card's validated `human_request` (action, why, scope) and the exact task link, instead of a 160-character clipped event reason.
- A block with only capability metadata and no `human_request` is shown as agent-owned, not as a false user ask; a resolved block reads "Earlier block resolved".

## How it works

`hermes_cli/kanban_block_notice.py` is the shared formatter; `hermes_state_outcome.py` carries the structured outcome.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1821: A blocked chat showed only an opaque, 160-char-clipped event reason ("... identity boundary ... Initiate HANDOFF ...") and a task id; the validated human_request (action/why/scope) never reached the notice.

## Code

`gateway/kanban_watchers_notifier.py`, `hermes_cli/kanban_block_notice.py`, `hermes_state_outcome.py`, `tui_gateway/kanban_remote_notices.py`, `tui_gateway/session_notifications.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/tui_gateway/test_actionable_block_notices.py`: `test_actionable_block_entry_paths`, `test_deleted_card_is_agent_owned`, `test_outcome_decision_keeps_full_validated_action`, `test_outcome_rejects_legacy_human_wait_without_evidence`, `test_stored_json_request_and_wake_carry_full_ask`
- `tests/tui_gateway/test_kanban_failure_notice_text.py`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- Source PR daveey/hermes#744.

## Plugin?

No: these change core modules that plugins cannot hook.

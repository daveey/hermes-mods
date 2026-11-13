# Owner-forward safety: pause notes, unconfirmed receipts, ledger refusal order

Patches: **3561, 3562, 3564**.

## Behavior
- The pause-replay note prefix has a single source (`update_pause.PAUSE_REPLAY_NOTE_PREFIX`); the gateway's auto-continue stripper recognises every pause note variant and returns the original request, keeping a trailing job report and carried note.
- A paused continuation whose forward to the owning gateway fails for a transient reason (not admitted, draining, stale epoch, admission conflict, queue full, dial failure) is kept for a later try after the 30 s backoff, up to 3 tries per pause, then retired with the attempt count logged. Definitive refusals, owner-not-running and unexpected errors retire at once.
- An owner receipt that names an admission but no confirmed state is "unconfirmed": never resent, the pause retires with "it may be queued there", the Desktop gets `SESSION_OWNER_UNCONFIRMED`. Only a receipt naming no admission stays the transient "did not admit it".
- The legacy turn refusal reads every non-terminal ledger row, non-forwardable first, so a `queued` row beside an `unknown` admission no longer lets a typed prompt be forwarded past the unknown barrier.

## How it works
- 3561 moves the gateway note recognition to `gateway/run_auto_continue_noise.py` (re-exported from `gateway.run`) with a literal `_PAUSE_REPLAY_NOTE_PREFIX` so the gateway never imports `tui_gateway` at load; a test pins it to the tui prefixes. `_paused_turn_to_ledger_owner` returns False on transient failures with `OWNER_FORWARD_TRIES` (3). `OwnerUnconfirmed` is never retried.
- 3562 makes `owner_forward` raise `OwnerUnconfirmed` for a receipt with an admission id but no confirmed state, and rewords the WARNING to "kept for a later try after the 30s backoff".
- 3564 adds `kanban_leg_custody.ledger_status(conn, sid)` (non-forwardable first, then by name) used by the refusal in `session_lifecycle` and by `lineage_ledger_status`; the old LIMIT 1 query stays for existence checks only.

## Code
- `tui_gateway/session_history.py`
- `tui_gateway/update_pause.py`
- `tui_gateway/session_auto_continue.py`
- `tui_gateway/owner_forward.py`
- `tui_gateway/kanban_leg_custody.py`
- `tui_gateway/session_lifecycle.py`
- `gateway/run.py`
- `gateway/run_auto_continue_noise.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_auto_continue_note_prefixes.py`: `test_gateway_prefixes_open_the_tui_notes`, `test_gateway_strips_every_pause_note_down_to_the_request`, `test_stripper_keeps_trailing_job_report_and_carried_note`.
- `tests/tui_gateway/test_update_pause_ledger_owner.py`: `test_transient_forward_failure_keeps_the_backoff_then_retires_after_the_cap`, `test_transient_forward_failure_then_success_hands_over_once`, `test_a_new_pause_gets_a_fresh_forward_budget`, `test_a_receipt_naming_an_admission_is_never_resent`.
- `tests/tui_gateway/test_legacy_submit_forwards_to_owner.py`: `test_a_queued_row_beside_an_unknown_admission_never_forwards_past_it`.

The Desktop `SESSION_OWNER_UNCONFIRMED` surface is untested here.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The backoff only gates the next try; the next resume, boot sweep or refused-turn retry performs it.
- The `SessionAuthority` receipt bug in 3562 was latent: today's receipt reports the freshly inserted row.

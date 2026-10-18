# "Stopped by <login>": a client Stop names who stopped the turn

Patch: `patches/0126-feat-tui-name-who-stopped-a-turn-and-leave-a-visible.patch`.

What you see: when a running turn is stopped from a client, the chat ends with a system line such as
"Stopped by alice" (a remote client logged in as `alice`, for example a phone app) or "turn stopped" (the local
Desktop). Stock Hermes ended a stopped turn on a hidden empty assistant row, so a chat stopped from another device
just went quiet and looked stuck. The gateway log also names who sent each Stop.

## Behavior

- `session.interrupt` on a session with a live turn records the issuer: the transport's login with the provider
  prefix dropped (`basic:alice` → `alice`), or `Desktop` for the local credential (no login).
- Every `session.interrupt` logs one INFO line: `session.interrupt: ui_session=<sid> session_key=<key> by=<label>
  peer=<address> running=<bool>`.
- When that turn then ends with status `interrupted`, the gateway appends and persists a notice row: role `user`,
  `display_kind` `turn_stopped`, content `[Turn stopped by <label> before it finished.]`, metadata
  `{display_text: "Stopped by <label>", stopped_by: <label>}`.
- Desktop renders `turn_stopped` rows as a system line with `display_text`; a row without it shows "turn stopped".
- No notice when: the session was idle at Stop time (nothing queued for a later turn); the turn completed anyway
  (a Stop that raced the end); or the turn was interrupted by the system (reapers, lease loss, shutdown), since only a
  client Stop records an issuer. The recorded issuer is always consumed when the turn ends.
- No config keys.

## How it works

- `tui_gateway/methods_session_interrupt.py`: `_stop_issuer_label(transport)` uses `_transport_auth_user_id`.
  `_note_stop_issuer(sid, session, transport)`, called from the `session.interrupt` handler, sets
  `session["_stop_issuer"]` under `history_lock` only when `session["running"]`, and logs the line.
  `_record_stop_notice(session, status)` pops the issuer; for `interrupted` it appends the entry to the in-memory
  history (bumping `history_version`) and persists it through the agent's `_session_db` (or the session's own DB
  handle) with `append_message(..., display_kind="turn_stopped", message_uid=...)`, marking it DB-persisted so later
  flushes don't write it twice. A persistence error is logged, not raised.
- `tui_gateway/prompt_turn.py::_run_prompt_submit` calls `_record_stop_notice(session, status)` once the turn's
  status is known, for every ending (it is a no-op for any ending that is not a client Stop).
- Desktop: `lib/chat-messages/hydration.ts` adds `turn_stopped` to `NOTICE_DISPLAY_KINDS` and maps it in
  `timelineDisplayContent`; `types/hermes.ts` adds it to `SessionMessage.display_kind`; the e2e oracle
  (`e2e/core/oracle.ts`) lists it as a notice kind.

## Code

- `tui_gateway/methods_session_interrupt.py`: `_STOP_NOTICE_KIND`, `_stop_issuer_label`, `_note_stop_issuer`,
  `_record_stop_notice`, call in the `session.interrupt` handler.
- `tui_gateway/prompt_turn.py`: call in `_run_prompt_submit`.
- `apps/desktop/src/lib/chat-messages/hydration.ts`, `apps/desktop/src/types/hermes.ts`,
  `apps/desktop/e2e/core/oracle.ts`.

## Tests

Python, `tests/tui_gateway/test_stop_notice.py` (8 cases, all pass Oct 17, 2026 PT, `scripts/run_tests.sh`):

- `test_stop_from_a_login_names_that_login`: `session.interrupt` over a transport with a `basic` login records the
  bare login and logs `by=<login>`.
- `test_stop_without_a_login_is_attributed_to_desktop`.
- `test_stop_on_an_idle_session_records_no_issuer`.
- `test_interrupted_turn_persists_a_visible_stop_notice`: history entry, DB row and metadata shape; issuer consumed.
- `test_completed_turn_drops_a_stale_issuer_without_a_notice`.
- `test_system_interrupt_without_a_client_stop_adds_no_notice`.
- `test_turn_stopped_by_redde_ends_with_a_persisted_notice` and `test_turn_that_completes_adds_no_notice`: a real
  `_run_prompt_submit` turn (threads inlined, agent faked) stopped mid-run by a logged-in client ends `interrupted`
  with exactly one persisted notice; a completed turn adds none.

Desktop, `apps/desktop/src/lib/chat-messages.test.ts` "renders a client Stop notice as a system line naming who
stopped the turn" (passes): hydrated `turn_stopped` rows become system messages showing `display_text`, or "turn
stopped" without it.

Untested: the TUI and other non-Desktop clients' rendering of `turn_stopped` rows, a stop through any path other
than `session.interrupt` (e.g. a slash command), and the live Desktop showing the line before a reload (the test
covers history hydration only).

## Applying

Standalone: new code in `methods_session_interrupt.py`, one call in `prompt_turn.py`, and three small Desktop edits.
It relies on `_transport_auth_user_id`, `_submit_row_target_key` and the `display_kind`/`display_metadata` message
columns already present at the fork's merge-base. Verified: applies with `git am -3` on top of 0001–0125 at
merge-base `4957bbd0a0` (Oct 17, 2026 PT).

## Gotchas

- The notice is a `user`-role row (like other notice kinds such as `model_switch`), so it is part of the stored
  history the next turn loads.
- The label is the login, not a device name. Two devices on the same login read the same.
- A local Stop with no login says "Desktop" even if it came from another local client.

## Plugin?

No. The issuer is only known inside the `session.interrupt` handler and the turn's end status inside
`_run_prompt_submit`; there is no plugin hook at either point, and Desktop needs the new display kind in core
hydration.

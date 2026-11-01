# "Stopped by <login>": a client Stop names who stopped the turn

Patches: `patches/0126-feat-tui-name-who-stopped-a-turn-and-leave-a-visible.patch`; 1323 (SSH-attached Desktop reads
"Desktop"; every Stop names its gesture); 1431 (`d9e0c56cb2`, Escape inside the contenteditable composer).

What you see: when a running turn is stopped from a client, the chat ends with a system line such as
"Stopped by alice" (a remote client logged in as `alice`, for example a phone app) or "turn stopped" (the local
Desktop). Stock Hermes ended a stopped turn on a hidden empty assistant row, so a chat stopped from another device
just went quiet and looked stuck. The gateway log also names who sent each Stop.

## Behavior

- `session.interrupt` on a session with a live turn records the issuer: the transport's login with the provider
  prefix dropped (`basic:alice` → `alice`), or `Desktop` for the local credential (no login).
- A Desktop attached over SSH logs in with its per-connection ownership id (provider `desktop-ssh`, a 32-hex id
  matching `~/.hermes/desktop-ssh/<id>`). That is a credential, not a person, so its Stop reads "Stopped by Desktop"
  (before patch 1323 it read "Stopped by 5caca281…"). Real logins still show the login.
- `session.interrupt` takes an optional free-form `origin`: the client gesture that sent the Stop. Desktop sends
  `stop_button` (composer Stop button), `esc` (Esc in the chat), `tile` / `tile:esc` (a split tile's Stop or Esc),
  `slash` (`/stop`), `delete` (deleting a chat mid-turn), `send_now` (send-now on a queued prompt while busy),
  `transcript` (the assistant runtime's own cancel), `rewind` (edit/reload/restore interrupting first) and
  `plugin:hermes-bots` (group-chat stop). An unknown value is accepted and made log-safe (`[^A-Za-z0-9_.:-]` → `_`,
  capped at 64 chars); a missing or non-string one is fine.
- Every `session.interrupt` logs one INFO line: `session.interrupt: ui_session=<sid> session_key=<key> by=<label>
  peer=<address> running=<bool> origin=<origin or ->`.
- When that turn then ends with status `interrupted`, the gateway appends and persists a notice row: role `user`,
  `display_kind` `turn_stopped`, content `[Turn stopped by <label> before it finished.]`, metadata
  `{display_text: "Stopped by <label>", stopped_by: <label>}`, plus `stop_origin: <origin>` when the Stop carried one.
  A later turn's Stop never inherits an earlier gesture; a repeat Stop on an already-stopped turn changes neither
  the label nor the origin (the interrupt cleared `running`).
- Desktop renders `turn_stopped` rows as a system line with `display_text`; a row without it shows "turn stopped".
- No notice when: the session was idle at Stop time (nothing queued for a later turn); the turn completed anyway
  (a Stop that raced the end); or the turn was interrupted by the system (reapers, lease loss, shutdown), since only a
  client Stop records an issuer. The recorded issuer is always consumed when the turn ends.
- No config keys.
- Patch 1431 makes Escape inside the real contenteditable `ChatBar` send `esc` exactly once while busy.
  An idle composer does not cancel. The separate `useComposerEscCancel` hook covers non-editor focus;
  testing that hook alone does not exercise the editor's own key handler.

## How it works

- `tui_gateway/methods_session_interrupt.py`: `_stop_issuer_label(transport)` reads the transport's own
  `auth_identity`: no login → `Desktop`; provider `desktop-ssh` → `Desktop`; else the `user_id`.
  `_stop_origin(params)` sanitizes `origin`. `_note_stop_issuer(sid, session, transport, origin)`, called from the
  `session.interrupt` handler, sets `session["_stop_issuer"]` and `session["_stop_origin"]` (or pops it) under
  `history_lock` only when `session["running"]`, and logs the line.
- `tui_gateway/contracts/sessions.py`: `SessionInterruptParams.origin: str | None` (the contract is
  `extra="forbid"`, so without it an `origin` answers 4000); the generated `apps/shared/src/gateway-contract.*`
  carry it.
- Desktop: `apps/desktop/src/lib/stop-origin.ts` (`StopOrigin`, `interruptParams(sessionId, origin)`, which drops a
  non-string such as a MouseEvent when a cancel handler is wired to `onClick`). Call sites: `cancelRun(origin)` in
  `use-prompt-actions/index.ts` (fed `stop_button` by `use-composer-submit.ts`, `esc` by the contenteditable
  `ChatBar` handler in `composer/index.tsx` via `haltRun('esc')` and separately by `use-composer-esc-cancel.ts`,
  `send_now` by `use-composer-queue.ts`, `transcript` by `app/chat/index.tsx`), `session-tile-actions.ts`
  (`tile`/`tile:esc`), `use-session-tile-delegate.ts` (`interruptSession(id, origin='tile')`), `slash.ts` (`slash`),
  `use-session-actions/index.ts` (`delete`), `rewind.ts` (`rewind`), `plugins/hermes-bots/group-rounds.ts`.
  `api/canonical-protocol.ts` strips `origin` from the canonical `session.interrupt` (that control contract forbids
  unknown keys).
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
- `apps/desktop/src/app/chat/composer/index.tsx`: editor Escape calls `haltRun('esc')` when busy and not awaiting input.
- `apps/desktop/src/app/chat/composer/editor-esc-origin.test.tsx`: mounted real `ChatBar` regression (patch 1431).
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

Added with patch 1323 (SSH Desktop label + Stop gesture), same file, all pass Oct 30, 2026 PT (existing cases
unedited):

- `test_stop_from_an_ssh_attached_desktop_reads_desktop` and `test_ssh_desktop_turn_end_to_end_reads_desktop`: a
  `desktop-ssh` identity yields "Stopped by Desktop", the hex id appears nowhere, the log says `by=Desktop`.
- `test_each_stop_origin_is_logged_and_kept_in_the_notice` (stop_button, esc, tile, slash, delete,
  plugin:hermes-bots): the log line ends `origin=<x>` and `display_metadata.stop_origin` is `<x>`, in memory and in
  the DB row.
- `test_absent_origin_logs_a_dash_and_adds_no_metadata_key`, `test_unknown_or_odd_origin_is_accepted_and_made_log_safe`,
  `test_origin_of_an_earlier_stop_never_labels_a_later_one`,
  `test_a_repeat_stop_on_an_already_stopped_turn_keeps_the_gesture_that_stopped_it`,
  `test_contract_accepts_origin_on_session_interrupt`.

Desktop vitest, one per call site (all pass): `use-composer-submit.test.tsx` (Stop button → `stop_button`),
`use-composer-esc-cancel.test.tsx` (`esc`), `use-composer-queue.test.tsx` (`send_now`), `use-prompt-actions/index.test.tsx`
("cancelRun names the Stop gesture": stop_button/esc forwarded, none or a click event sends none; `/stop` → `slash`;
rewind → `rewind`), `session-tile-actions.test.ts` (`tile`, `tile:esc`), `use-session-tile-delegate.test.ts` (`tile`),
`use-session-actions.test.tsx` (delete → `delete`), `group-rounds.test.ts` (`plugin:hermes-bots`),
`canonical-protocol.test.ts` (stripped on the canonical wire). Untested: the `transcript` origin (assistant runtime
cancel in `app/chat/index.tsx`).

Patch 1431 adds `apps/desktop/src/app/chat/composer/editor-esc-origin.test.tsx`: it mounts the real `ChatBar`
with runtime, router and i18n providers, focuses the rich editor and dispatches Escape. The busy case asserts
`onCancel` is called exactly once with `esc`; the idle case asserts no cancel. This covers the editor entry
path, unlike the separate `use-composer-esc-cancel.test.tsx` hook tests for non-editor focus.

Source verification supplied by the implementation owner: **70 targeted tests passed** across
`editor-esc-origin`, `use-composer-esc-cancel` and `use-composer-submit`; the exact original head
`953727537d` had CI **SUCCESS**, and PR #438 merged as `d9e0c56cb2`. These are attributed source results,
not reruns by this mirror. A mounted jsdom regression is not live Desktop proof; that check belongs to the
implementation owner and is not asserted here.

Desktop, `apps/desktop/src/lib/chat-messages.test.ts` "renders a client Stop notice as a system line naming who
stopped the turn" (passes): hydrated `turn_stopped` rows become system messages showing `display_text`, or "turn
stopped" without it.

Untested: the TUI and other non-Desktop clients' rendering of `turn_stopped` rows, a stop through any path other
than `session.interrupt` (e.g. a slash command), and the live Desktop showing the line before a reload (the test
covers history hydration only).

## Applying

Patch 1431 extends patch 1323's origin plumbing; apply it after those existing composer/stop-origin changes
using the current [series applying recipe](README.md#applying). It is not a standalone implementation of
Stop attribution. This mirror documents the patch and does not claim a new source-test run or app rebuild.

Standalone: new code in `methods_session_interrupt.py`, one call in `prompt_turn.py`, and three small Desktop edits.
It relies on `_transport_auth_user_id`, `_submit_row_target_key` and the `display_kind`/`display_metadata` message
columns already present at the fork's merge-base. Verified: applies with `git am -3 --empty=keep` on top of 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105, 0106, 0107, 0108, 0109, 0110, 0111, 0112, 0113, 0114, 0115, 0116, 0117, 0118, 0119, 0120, 0121, 0122, 0123, 0124, 0125 at
merge-base `4957bbd0a0` (Oct 17, 2026 PT).

## Gotchas

- The notice is a `user`-role row (like other notice kinds such as `model_switch`), so it is part of the stored
  history the next turn loads.
- The label is the login, not a device name. Two devices on the same login read the same. To tell which door
  stopped a turn, read `origin=` on the `session.interrupt:` line in agent.log (or `stop_origin` on the notice row).
- The canonical session protocol drops `origin`; a Stop over it logs nothing gesture-specific.
- Editor Escape and non-editor Escape are distinct entry paths. A hook-only test cannot prove the contenteditable
  path forwards the gesture. Patch 1431 fixes the editor path without changing the key binding or Stop-button origin.
- A local Stop with no login says "Desktop" even if it came from another local client.

## Plugin?

No. The issuer is only known inside the `session.interrupt` handler and the turn's end status inside
`_run_prompt_submit`; there is no plugin hook at either point, and Desktop needs the new display kind in core
hydration.

# A chat held by another backend gets one refusal notice, not a retry loop

Patches:

- `patches/0702-fix-tui-a-chat-held-by-another-backend-gets-one-refu.patch`

On 2026-10-28 a background-process completion was waiting in David-MBP's Desktop backend for chat
`20261023_204423_b85a94`. Meanwhile the mini's Desktop backend (SSH-spawned on the same Mac, so it shares
state.db) held the chat's durable turn lease. The notification poller claimed the idle chat and ran the
completion turn. `_admit_prompt_turn` refused it `SESSION_NOT_OWNED` (`_durable_owner_refusal`), the event went
back on the queue, and the poller ran it again on its next pass. Each refused attempt painted "Hermes hit an
internal error while running this turn ... Your message was not answered". There were 139 of these cards in 110 s,
until hermes-sync's drain closed admission.

## Behavior

- A synthesized turn (process completion, subagent result, /loop tick, kanban notice) refused
  `SESSION_NOT_OWNED` or `SESSION_COORDINATION_UNAVAILABLE` starts an owner-refusal backoff for that chat. The
  backoff starts at 30 s and doubles up to 300 s. While it holds, the poller claims no automatic turn for the
  chat.
- Within one refusal streak, the user sees exactly one notice. A retry first probes ownership without emitting
  anything (`message.start` included) and stays silent while the chat is still held elsewhere.
- A refused completion event is not marked delivered. It is released and held for this chat, and the next
  retry delivers it once the other backend lets go.
- A successful admission ends the streak (`_admit_prompt_turn` clears the backoff keys).
- A user's own prompt is unaffected. It is refused, with a notice, every time.
- Both refusal codes get their own turn-error copy instead of the gateway layer's "internal error":
  "This chat is open in another Hermes window or device. Use it there, or start a new chat with /new." The
  coordination failure has its own line too.

## How it works

`_admit_prompt_turn` stores the refusal code in `session["_turn_owner_refusal"]`. Next, `_notif_run_submit`
pops that key around `_run_prompt_submit`. On a refusal it calls `_notif_note_owner_refusal`, which sets
`_notif_owner_refused_until` (monotonic) and `_notif_owner_refusal_delay`. `_notif_claim_turn` returns False while
`_notif_owner_refused` holds. `_notif_owner_preflight` runs before `_notif_submit` and `_notif_run_submit`, and
before the /loop tick's `message.start`. It probes `_ensure_active_session_slot` only inside a streak (delay
set). A still-refused probe extends the backoff and releases the claim. `_notif_dispatch_event` sees
`accepted is False` under an active backoff, releases the delivery claim and holds the event with
`_notif_hold_for` (shared queue as fallback).

## Code

- `tui_gateway/session_notifications.py`: `_OWNER_REFUSAL_*`, `_notif_owner_refused`,
  `_notif_note_owner_refusal`, `_notif_owner_preflight`, `_notif_claim_turn`, `_notif_run_submit`,
  `_notif_submit`, `_maybe_fire_tui_loop_tick`, `_notif_dispatch_event`.
- `tui_gateway/prompt_turn.py`: `_admit_prompt_turn` (records the refusal, clears the streak on success).
- `tui_gateway/user_messages.py`: `_TURN_ERROR_CODE_COPY` entries for both codes.

## Tests

- `tests/tui_gateway/test_notification_owner_refusal_backoff.py` (real poller thread, dispatch,
  `_run_prompt_submit` and `_admit_prompt_turn`, against a registry lease held by another writer):
  - `test_owner_refusal_is_not_retried_in_a_loop_and_shows_one_notice`: bounded attempts and one notice whose copy
    names where the chat is open.
  - `test_refused_completion_is_delivered_once_the_other_device_lets_go`: the event is held, then delivered after
    the lease is released.
  - `test_a_user_prompt_refusal_still_reports_every_time`: user prompts are not swallowed by the backoff.
- `tests/e2e/core/terminal/test_held_session_notification_refusal.py`: the real `hermes serve` backend with a
  fake LLM. A `terminal(background=true, notify_on_complete=true)` job completes while a second OS process holds
  the chat's durable state.db turn lease. Over 40 s the pre-fix tree gives 111 attempts and 111 refusal cards;
  this patch gives 1 attempt and 1 `SESSION_NOT_OWNED` notice.

Untested: the Desktop renderer's display of the new copy. The e2e asserts the `message.complete` payload text and
`error_surface.code` that the renderer receives.

## Apply

`git am core/patches/0702-*.patch`. Takes effect when the Desktop backend restarts.

## Gotchas

- The registry slot and the durable turn lease are different gates. A warm session with its own registry
  lease is still refused by another process's `session_turn_leases` row. That is the incident's path, and
  the e2e reproduces it that way.
- A test `HERMES_HOME` from `mktemp -d` under `~/.hermes/cache/scratch` (`TMPDIR` on the hosts) trips the
  real-home I/O guard in poller tests. Use `mktemp -d -p /tmp`.

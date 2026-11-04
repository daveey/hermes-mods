# Desktop sends to a busy chat: gateway turns wait for the lease; unqueued sends return to the composer

Patches: **1831** (`79477b7501`, `fix(agent): a gateway-hosted Desktop turn waits for a held turn lease instead of refusing it [daveey]`), **1832** (`5780449cad`, `test: wait on the turn thread, not the clock, in the busy-chat send test [daveey]`), **1839** (`88d92f918c`, `fix(desktop): give a busy-chat send that could not be queued back to the composer (#770)`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- A Desktop turn hosted by the owner gateway (forwarded by the serve backend or a cross-session relay) waits for a held durable turn lease instead of failing at once with `session_busy` and losing the text (1831); the test waits on the turn thread, not the clock (1832).
- A busy-chat send that could not be queued is given back to the composer rather than dropped (1839).

## How it works

`agent/turn_facade_lease.py` no longer applies the Desktop serve rule `wait_seconds=0` to gateway FIFO turns whose platform is the desktop surface; the Desktop composer restores the draft on a busy refusal.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1831: A send to a busy Desktop chat that reached the owner gateway (forwarded by the serve backend, or a cross_session relay) failed at once with session_busy, "Couldn't send your message", and the text was lost. The gateway's agent for a Desktop (gui) local chat carries the session surface as its platform ("desktop"), so admit_durable_turn_lease gave it the Desktop serve rule wait_seconds=0, which exists because a serve client still holds the text and offers Take over. A gateway FIFO turn has already accepted the input, so nobody holds it any more. refuses_held_lease_at_once() keeps the instant...
- 1832: test: wait on the turn thread, not the clock, in the busy-chat send test [daveey]
- 1839: * fix(desktop): give a busy-chat send that could not be queued back to the composer

## Code

`agent/turn_facade_lease.py`, `apps/desktop/src/app/chat/composer/hooks/use-composer-draft.ts`, `apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/message-stream.ts`, `apps/desktop/src/app/session/hooks/use-prompt-actions/index.ts`, `apps/desktop/src/app/session/hooks/use-session-actions/message-equivalence.ts`, `apps/desktop/src/lib/chat-messages/hydration.ts`, `apps/desktop/src/lib/chat-messages/types.ts`, `apps/desktop/src/lib/session-busy-refusal.ts`, `apps/desktop/src/store/refused-sends.ts`, `tui_gateway/prompt_turn.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `apps/desktop/src/app/chat/composer/hooks/use-composer-submit.busy-refusal.test.tsx`: `SESSION_OWNER_BUSY: the refused submit leaves the words in the composer`, `SESSION_OWNER_BUSY answered as a rejected send (accepted === false) keeps the words too`, `a serve turn refused at once by a held lease gives the words back to the composer`, `a gateway turn that waited out the lease gives the words back (older frame: error prefix only)`, `a turn refused because another window took the chat over gives the words back`, `a failed_turn row with failure_reason session_busy brings the text back on a reopened chat`, `a failed_turn row whose error surface names session_busy brings the text back`, `another failed turn (a provider error) leaves the composer alone`, `keeps what the user typed meanwhile and adds the refused words after it`, `offers the words once: a re-read transcript does not refill a composer the user emptied`, `the given-back words are a new message, not a retry of the settled submission id`, `re-running the refused turn from its card takes the untouched offer back` (+4 more)
- `tests/gateway/test_busy_chat_send_queues_behind_turn.py`: `test_desktop_send_while_a_turn_runs_is_processed_after_it`, `test_cross_session_send_while_an_automation_turn_runs_is_processed_after_it`, `test_control_idle_chat_runs_at_once`, `test_control_serve_desktop_turn_still_refuses_a_held_lease_at_once`
- `tests/tui_gateway/test_durable_takeover_recovery.py`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.

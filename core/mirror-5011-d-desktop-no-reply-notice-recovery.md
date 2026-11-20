# Desktop: the "ended without a reply" notice only appears when the store agrees

Patches: **4657, 4658, 4659, 4660**

- 4657 (`e5057461ca`): [daveey] desktop: a disproved no-reply notice leaves with its turn's reply
- 4658 (`b6810bb168`): [daveey] desktop: the retry card needs the store's word that a turn left no reply
- 4659 (`6245b1d3c9`): [daveey] desktop: an ended turn's store re-read never stamps a newer turn
- 4660 (`b3e05209d3`): [daveey] desktop: reconcile late completions and refuse unverified retries

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- A silent-turn check that wrongly stamped "Hermes ended this turn without a reply" is corrected: when the turn's reply is later stored, or the turn emits new output, or a liveness poll lists it as working, the notice (and its Retry) is removed instead of lingering as a second bubble.
- Retry is only offered when the store itself says the turn left no reply. If every bounded store read fails to apply, no retry card is offered and the local transcript does not decide.
- A newer turn that begins during the re-read delay is never stamped with the older turn's notice.
- Every silent-turn check and recovery writes a `[silent-turn]` diagnostic line to the desktop log.

## How it works

- Reconciliation clears a no-reply notice whose turn has a stored reply, matching by descriptor or by text when the descriptor was lost, before local error bubbles are preserved or folded.
- Live-turn verdict helpers fall back to the row whose session key is the chat's stored id when no row carries the runtime id.
- `hydrateStoredSessionTranscript` resolves true only when a stored page was applied; `recoverEndedLiveTurn` re-reads the store on a short backoff schedule and only after exhaustion considers fallback; the recovery remembers its turn's prompt and aborts when the tail prompt changes.
- `disproveNoReplyNotice` fires only on frames carrying new output (not closing frames, which can be late or replayed); a completion carrying reply/error content clears just the synthetic notice and leaves unrelated real failures. Recovery logic is extracted into `session-no-reply-recovery.ts` for size caps.

## Code

- `apps/desktop/src/lib/chat-messages/no-reply.ts`
- `apps/desktop/src/lib/chat-messages/reconciliation.ts`
- `apps/desktop/src/store/session-no-reply.ts`
- `apps/desktop/src/store/session-states-live-turn.ts`
- `apps/desktop/src/store/session-states.ts`
- `apps/desktop/src/app/contrib/hooks/use-background-sync.ts`
- `apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/index.ts`
- `apps/desktop/src/app/session/hooks/use-message-stream/index.ts`
- `apps/desktop/src/store/session-no-reply-recovery.ts`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `apps/desktop/src/app/contrib/hooks/live-status-reap.test.ts`: `takes back a `
- `apps/desktop/src/app/contrib/hooks/use-background-sync.test.ts`: `post-turn hydrate: reports a read that never applied, so no one treats the turn as replyless`
- `apps/desktop/src/app/session/hooks/no-reply-notice-disproved.test.tsx`: `leaves when the turn emits stream output`, `stays for a frame that is not turn output`
- `apps/desktop/src/app/session/hooks/use-message-stream/no-reply-completion.test.tsx`: `keeps the notice on an empty terminal frame`, `does not erase an unrelated failure when completing a bubble`
- `apps/desktop/src/lib/chat-messages/no-reply-notice.test.ts`: `drops the empty notice bubble`, `drops a stamped live bubble that only holds the tool rows of the turn`, `drops the notice when the descriptor was lost and only the text survives`, `keeps the notice`
- `apps/desktop/src/store/session-watchdog.test.ts`: `finds the turn by the stored session id when no row carries the runtime id`, `prefers the row carrying the runtime id over a stored-id match`, `names the row the verdict was read from, or that none matched`, `does not offer a retry while the store has not said the turn had no reply`, `falls back to the local transcript once every store read came back empty-handed`, `takes the retry card back when the turn it was stamped on turns out to be running`, `drops a pending store re-read once the turn is shown running`, `does not stamp a newer turn that began while the store was being re-read`, `uses the stored-key fallback throughout the silence recovery entry path`, `never offers to rerun a prompt whose stored result could not be read`, `does not stamp stale local state when the pane leaves during a failed store read`

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- Closing frames (`message.complete`, `tool.complete`) must not disprove a correct notice; only output-bearing frames do.
- The original incident cause (which snapshot answer produced the wrong "ended" verdict) was not established; the log lines exist to name it.
- Retry safety depends on the store read, so a store outage deliberately removes the retry affordance.

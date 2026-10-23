# Desktop opens Kanban worker sessions read-only and quietly

Patches:

- `patches/0213-fix-desktop-open-Kanban-worker-sessions-read-only-an.patch`: a worker transcript opens read-only, no "Resume failed" toast.
- `patches/0227-refactor-desktop-goneSessionSwitchInFlight-helper-of.patch`: `goneSessionSwitchInFlight` helper offsets the extra branch (code-health ratchet).

## Behavior

Opening a Kanban worker's session from the sidebar or a card used to show a "Resume failed" toast: the gateway refuses to
resume a worker transcript on purpose (JSON-RPC code 4132), since a resumed chat would be a second, unsupervised writer next to the
dispatcher-owned run. Desktop now treats 4132 as expected: the history (already painted from the REST fallback) stays
visible, the transcript is marked read-only, and there is no toast and no retry latch.

## How it works

`owner-follow.ts` exports `KANBAN_WORKER_RESUME_REFUSED_CODE = 4132` and `isKanbanWorkerResumeRefusal(error)`. In
`use-session-actions/index.ts`'s resume path, when the error matches and the REST fallback did not fail, it calls
`markStoredTranscriptReadOnly(id)` and returns. 0227 moves the "known foreign owner or a switch in flight" test into
`gone-session-switch.ts` (`goneSessionSwitchInFlight(owner)`).

## Code

- `apps/desktop/src/app/session/hooks/use-session-actions/index.ts`, `owner-follow.ts`, `gone-session-switch.ts`.

## Tests

- `owner-follow.kanban-refusal.test.ts`: matches the 4132 refusal only (not 4007, plain errors, null).
- `gone-session-switch.test.ts`: false for unknown/blank/matching owner with no switch; true for a foreign owner or a switch in flight.
- Not re-run when this doc was written (untested here).

## Applying

`git am -3` in order 0213, 0227.

## Gotchas

- The read-only mark only applies when the REST fallback loaded the history; if that failed the normal error path still shows.

## Plugin?

No.

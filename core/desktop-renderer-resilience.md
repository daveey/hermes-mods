# Desktop renderer resilience and ticket bootstrap diagnostics

Patches: 1328, 1336, 1338, 1345, 1346, 1361.

## Behavior

- 1336 suppresses expected unhandled `PowerSettlingError` rejections from skipped background reads around sleep/wake; unrelated failures still reach the console.
- 1338 exposes the ticket-bootstrap failure cause in both `error.code` and the message suffix, preserving the `Gateway ticket bootstrap failed` prefix. Refusal and deadline failures get one retry; a missing socket does not.
- 1361 makes queued toast dismissal and session-search focus harmless after the browser environment disappears. Live focus dispatch and toast dismissal callbacks retain their behavior; reset cancels pending notification timers.
- 1345–1346 change only the prepared-submission journal test: storage enumeration is observed on the appropriate real-Storage or fallback-object spy seam, with exact index order asserted.
- 1328 changes the `SESSION_NOT_OWNED` recovery action to use `errorStartNewSession` even when rendered as a link. This label path is **untested by this commit**.

## How it works

The renderer entry installs an `unhandledrejection` listener that prevents the default only for code `POWER_SETTLING` or a message containing `PowerSettlingError:`; Electron may strip the original error code across IPC.

Ticket minting retries `ECONNREFUSED`, `EAGAIN` or `deadline` once after 300 ms, resolving the control socket again before dialing. The per-attempt deadline is 5000 ms. Existing group-writable-home handling is outside this retry branch.

Timer callbacks re-check whether `window` exists. A dismissal after teardown drops its timer bookkeeping without running consumer callbacks. Plugin-tab test cleanup dismisses existing notifications before restoring globals.

The journal test selects `Storage.prototype` when the instance uses its `key` method, otherwise the storage object itself, and restores mocks after each test.

## Code

- `apps/desktop/src/lib/power-settling-rejections.ts`, `src/main.tsx`.
- `apps/desktop/electron/local-gateway.ts`.
- `apps/desktop/src/store/{notifications,layout}.ts`.
- `apps/desktop/src/components/assistant-ui/thread/assistant-message.tsx`.
- `apps/desktop/src/app/session/hooks/use-prompt-actions/prepared-submissions.test.ts`.

## Tests

Tests below are present in the diffs; they were not executed for this documentation task.

- `src/lib/power-settling-rejections.test.ts`: `recognizes the IPC-stripped error by its name in the message, and the main-process code`; `swallows an unhandled PowerSettlingError rejection`; `leaves every other unhandled rejection to the console`; `is installed by the renderer entry`.
- `electron/local-gateway-ticket-cause.test.ts`: `a stalled listener surfaces code "deadline", after one retry`; `a refused connect surfaces code ECONNREFUSED, after one retry`; `one refused connect followed by a live listener returns the ticket`; `one missed deadline followed by an answer returns the ticket`; `a missing socket is not retried`; `a socket unlinked after a refused dial reports missing, with no second dial`. These socket tests skip Windows.
- `src/store/notifications-timers.test.ts`: `auto-dismiss is harmless after the browser environment is torn down`; `auto-dismiss still removes a live toast and calls its callback once`; `reset and explicit dismissal cancel pending auto-dismiss callbacks`.
- `src/store/layout-focus-timer.test.ts`: `queued session-search focus tolerates browser teardown`; `queued session-search focus still dispatches in a live browser`.
- `prepared-submissions.test.ts`: `a browser journal read lists the storage keys once, however many stores it reads` (1345–1346 preserve the count assertion and add exact calls).

## Applying

Apply the numbered patches in the ordered fork series, retaining their earlier dependencies. Main-process and renderer changes require the corresponding Desktop build; no new hotkeys or configuration.

## Gotchas

`EAGAIN`, non-transient ticket errors, group-writable-home behavior and real sleep/wake integration are untested by the new socket/renderer tests. Suppressing an expected rejection does not itself retry a read; the next poll remains responsible. The journal patches repair test observation, not production persistence.

## Plugin side

No plugin implementation is required. Timer teardown is covered in core stores; disk-plugin reload alone cannot replace Electron main-process code.

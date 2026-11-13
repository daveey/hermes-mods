# Desktop: keep a slow-but-alive SSH backend (slow-link mode)

Patches: **3203, 3204**.

## Behavior
- A dispatch health probe that times out while the SSH master process is alive keeps the pooled backend instead of retiring and reconnecting it. A dead transport, a transport-check error, refused/reset errors, or three timeouts with no answer in between still retire it.
- Slow mode is entered only by such a live-transport probe timeout; it is left when the median of the last five answers is fast or after an idle expiry. While slow: backend request and probe timeouts are stretched (x4, capped); timer-polled reads are thinned to one in flight per scope and path with a minimum gap (extra polls fail as a quiet settling error; user-driven reads such as opening a session are never thinned); transcript prefetch skips that connection and resumes when the pause lifts.
- Desktop's own SSH options enable compression.
- Known cost: the master-process check proves the process, not the link, so after a silent network drop requests wait their stretched timeout.

## How it works
- 3203 adds `slow-link.ts` (state, answer ledger, `dispatchSlowLink`, `thinnedPoll`, `forgetScope`), wires it in `main.ts`, `remote-liveness.ts`, `transcript-prefetch.ts` and the transcript cache host, and sets compression in `ssh-connection.ts`.
- 3204 updates the warm-dispatch pin to follow `noteRemoteAnswer` into `slow-link.ts` and tests that the first request of a new descriptor is logged once.

## Code
- `apps/desktop/electron/slow-link.ts`
- `apps/desktop/electron/remote-liveness.ts`
- `apps/desktop/electron/main.ts`
- `apps/desktop/electron/ssh-connection.ts`
- `apps/desktop/electron/transcript-prefetch.ts`
- `apps/desktop/electron/transcript-cache-host.ts`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/electron/slow-link.test.ts`: tests for slow-mode entry on a probe timeout with a live transport, leaving slow mode on a fast median, idle expiry, strike limit, timer-poll thinning, no thinning of non-poll reads, per-profile isolation, slot release when a poll never reached the wire, quiet-skip classification of a held poll, `forgetScope`, and the answer ledger / first-request log (3204).
- `apps/desktop/electron/remote-liveness.test.ts`: keeps the descriptor on a slow-but-alive backend; still retires when the transport is dead, the check throws, or the tracker gives up.
- `apps/desktop/electron/transcript-prefetch.test.ts`: skips a paused connection, fetches others, resumes.
- `apps/desktop/electron/remote-warm-dispatch.test.ts` and `ssh-connection.test.ts` were adjusted.
- Renderer "slow connection" indicator: not part of these patches.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The renderer still reads "Reconnecting" when its WebSocket is down; three background revalidation paths get stretched timeouts but no live-transport tolerance.

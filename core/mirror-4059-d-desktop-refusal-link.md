# Desktop submit refusal guards and slow-link connect wiring

Mirror snapshot 4059; covered ordinals: 3908, 3914.

## Behavior

- An identityless send with an ambiguous error remains unconfirmed and is not automatically resent merely because its text says `session not found`.
- Test coverage traces slow-link chat connect limits through tunnel registration, main IPC, preload, and renderer dial handling. Tunnel registration now requires the master-liveness callback.

## How it works

`submit.ts:isDefiniteSubmitRefusal` calls the self-guarded stale-runtime predicate before its general gateway-error block; the same inputs are accepted, but class/code guards become independently testable. `slow-link.ts:noteRemoteConnect` requires and always registers `masterAlive` with the transport registry. The wiring tests use the real preload bridge with an Electron-free IPC bus, not source-string pins.

## Code

- `apps/desktop/src/app/session/hooks/use-prompt-actions/submit-backend-roll.test.tsx`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts`
- `apps/desktop/electron/delta-reads.test.ts`
- `apps/desktop/electron/gateway-connect-wiring.test.ts`
- `apps/desktop/electron/slow-link.test.ts`
- `apps/desktop/electron/slow-link.ts`
- `apps/desktop/src/api/gateway-link-dial-bridge-faults.test.ts`

### Mirrored commits

- 3908: `afe73775c0f0a6fc837d5c7b0758d3b4d050ba24` — Pins gateway error class/code guards against ambiguous submit resend.
- 3914: `33e109a14fd960d456492f5d81ef64f535add6cc` — Requires tunnel liveness registration and tests main/preload connect wiring.

## Tests

- `apps/desktop/src/app/session/hooks/use-prompt-actions/submit-backend-roll.test.tsx`: a non-4001 `JsonRpcGatewayError`, or plain `Error` carrying 4001, with exact stale-session wording still yields one unconfirmed send and no automatic resend.
- `apps/desktop/electron/gateway-connect-wiring.test.ts`: slow dispatch/chat timeouts stretch the tunnel's chat limit from fifteen to sixty seconds only with live transport evidence; forgetting the scope restores defaults and cannot revive it. Valid opens update shared liveness; invalid elapsed times or failed master checks do not. IPC/preload calls reach both limit and outcome handlers.
- `apps/desktop/src/api/gateway-link-dial-bridge-faults.test.ts`: nonfinite/nonpositive/throwing limit advice falls back to client defaults; throwing reports cannot change a successful dial or replace its original timeout error.
- `apps/desktop/electron/delta-reads.test.ts` and `apps/desktop/electron/slow-link.test.ts`: existing registration fixtures supply the now-required callback; these edits alone add no new assertions.

Tests were not rerun by this documentation pass. These descriptions report assertions inspected in the staged mail diffs, not fresh passing results. Whole-series replay and tree verification belong to the parent mirror verification; no deployment or live behavior is claimed here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including its pinned historical replay bridges, snapshot policy/replay plan, and mandatory post-replay hostname escape repair and repaired-tree verification. These ordinals are not independent cherry-picks: prior schema, helpers, and shared-file context are dependencies. Do not replay only this document's subset or rewrite historical mails to avoid the repair.

## Gotchas

Error wording alone is not proof the backend refused a turn. Slow-link advice is an optimization, not authorization to retry an ambiguous send. IPC tests exercise in-process wiring and simulated transports, not a live remote SSH session or packaged application.

## Plugin?

No. Submit identity and Electron/main/preload transport state are core client responsibilities.

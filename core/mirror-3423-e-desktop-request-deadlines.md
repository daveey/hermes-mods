# Desktop: bounded remote request deadlines

Patches: **3212, 3222, 3236**.

## Behavior
- Each remote JSON request attempt has a total deadline covering Agent queue wait, connect and response; a request queued behind a full Agent or a trickling response now rejects by its deadline with the existing timeout error. A request that answers in time is not destroyed.
- The dispatch health probe uses a dedicated small keep-alive pool, so it answers while the JSON pool is saturated with hung requests.
- The remote down-gate trial has a 20 s total deadline (default); a hung connect or request frees the slot and the next trial runs; a late failure from an abandoned trial cannot escalate backoff again; a late success still reopens the gate; non-trial requests are not cut by the trial deadline.
- Uploads: the total budget grows by body size at 1 Mbit/s, so a large attachment on a slow link completes; the idle timer still cuts a stalled upload at the caller's timeout, including a mid-transfer stall.

## How it works
- 3212 adds `startRequestDeadline` in `api-transport.ts`, the `probe` pool option, and the trial deadline in `remote-down-gate.ts`.
- 3222 adds `deadline.arm(req, reject, bodyBytes)` and `requestDeadlineBudgetMs`, and documents the trial-deadline trade-off (an in-flight trial may still be applied server-side).
- 3236 routes `fetchJson` / `fetchPublicJson` through `sendWithRequestDeadline`, which arms the deadline with the UTF-8 byte size of the body it writes; it replaces a source-regex test of `main.ts` with behavioural tests.

## Code
- `apps/desktop/electron/api-transport.ts`
- `apps/desktop/electron/remote-down-gate.ts`
- `apps/desktop/electron/remote-liveness.ts`
- `apps/desktop/electron/main.ts`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/electron/api-transport.test.ts`: tests for a queued request rejected by its deadline, a trickling response rejected by its deadline, an in-time answer not destroyed, the probe pool answering under saturation, upload budget at 1 Mbit/s, upload outlasting the JSON timeout, same transfer timing out without a body allowance, stalled upload cut by the idle timer, a mid-transfer stall, deadline armed with body byte size via `sendWithRequestDeadline`, end-to-end body allowance.
- `apps/desktop/electron/remote-down-gate.test.ts`: default 20 s trial budget, hung connect/request trials free the slot, late failure cannot re-escalate backoff, non-trial request not cut, late success from an abandoned trial reopens the gate.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The 20 s trial deadline is shorter than a trial request's own budget, so a trial still in flight may be rejected to its caller while the server still applies it (including a non-idempotent mutation).

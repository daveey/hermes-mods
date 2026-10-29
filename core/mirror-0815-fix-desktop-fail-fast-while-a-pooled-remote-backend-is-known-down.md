# fix(desktop): fail fast while a pooled remote backend is known down [daveey]

Patch 0815, source `08c725d741724496579ff212ec70a04634ae61d8`.

## Behavior

fix(desktop): fail fast while a pooled remote backend is known down [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `import { describe, expect, it } from 'vitest'`
- `describe('formatApiRequestFailure', () => {`
- `export function formatApiRequestFailure(`
- `import {`
- `const pooledRemoteLiveness = new RemoteLivenessTracker(undefined, REMOTE_POOLED_`
- `async function ensureRegistryBackend(`
- `async function dispatchRegistryApiRequest(`
- `+import { readFileSync } from 'node:fs'`
- `+// [daveey] Fail fast while a pooled remote backend is known to be down.`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Every `hermes update` on the hub or mini retires its SSH-isolated serve backends, and the
> Desktop kept routing renderer polls (hub-sync/status, kanban/board, runners) at the dead tunnel
> or queued them behind the redial, so each restart surfaced as a run of 4-10 s "Timed out
> connecting to Hermes backend" errors. On 2026-10-28 all 11 mac-mini "dial failed" clusters and
> both hermes-hub ECONNRESET bursts lined up with a core update on that host (hermes-sync.log
> "update exit"), not with sleep, Tailscale (both peers direct) or sshd keepalive.
>
> remote-down-gate.ts tracks each remote pool key (conn:<id>::<profile>): a reset/refused/hung-up
> request, a failed connect or a failed dispatch probe marks it down; while down its requests
> reject at once with RemoteBackendDownError. After a 2 s backoff (doubling to 30 s) one trial
> request redials; its answer (HTTP errors included) reopens the gate. A plain request timeout
> does not take the remote down (slow endpoint) but drops the answer mark so the next dispatch
> re-probes /api/health. Fast-fails log as one line with no stack.

## Code

- `apps/desktop/electron/api-request-failure.ts`
- `apps/desktop/electron/main.ts`
- `apps/desktop/electron/remote-down-gate.ts`

## Tests

Source test files changed by this commit:

- `apps/desktop/electron/api-request-failure.test.ts`
- `apps/desktop/electron/remote-down-gate.test.ts`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

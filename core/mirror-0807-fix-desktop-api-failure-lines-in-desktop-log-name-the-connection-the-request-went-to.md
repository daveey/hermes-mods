# fix(desktop): api failure lines in desktop.log name the connection the request went to [daveey]

Patch 0807, source `02a197fab24c4fdc280fadad43fd670e26c665b2`.

## Behavior

fix(desktop): api failure lines in desktop.log name the connection the request went to [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `+import { describe, expect, it } from 'vitest'`
- `+// Format an api-request failure for desktop.log. The renderer only ever sees`
- `import type { Session } from 'electron'`
- `async function handleHermesApiRequest(request) {`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> '[hermes:api GET /path] Error: Timed out connecting…' didn't say which backend timed out.
> hub-sync asks every registry connection for /status, so mac-mini/hub tunnel timeouts read
> as a stalled local backend (2026-10-28, a day of chasing the wrong process). The line now
> carries conn=<registry id>|primary and profile=<p>; the path still closes the bracket so
> coach backend_timeouts keeps parsing it.

## Code

- `apps/desktop/electron/api-request-failure.ts`
- `apps/desktop/electron/main.ts`

## Tests

Source test files changed by this commit:

- `apps/desktop/electron/api-request-failure.test.ts`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

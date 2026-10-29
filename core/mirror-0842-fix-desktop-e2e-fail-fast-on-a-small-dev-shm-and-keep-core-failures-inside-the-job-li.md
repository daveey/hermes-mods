# fix(desktop-e2e): fail fast on a small /dev/shm and keep core failures inside the job limit [daveey]

Patch 0842, source `5033f308f0aba5cd28fafb04e9cb363466984a41`.

## Behavior

fix(desktop-e2e): fail fast on a small /dev/shm and keep core failures inside the job limit [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `+import { assertShmLargeEnough } from '../shm-preflight'`
- `export function composer(page: Page) {`
- `+import * as fs from 'node:fs'`
- `import { defineConfig } from '@playwright/test'`
- `import * as fs from 'node:fs'`
- `function assertIsolated(): void {`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> CI 40785530488 (a0ebd73b) lost the Desktop core lane with no explanation:
> 9 specs each hit the 180 s test timeout, the 30 min job limit cancelled the
> run before the list reporter printed any failure, and the artifact upload
> hit the account-wide storage quota.
>
> Root cause (host, fixed outside this repo): the rebuilt gen-2 CI box created
> runner containers without --shm-size, so they had Docker's 64 MiB /dev/shm
> (every other box has 2g). Electron's renderer never painted there: the
> sibling update-concurrency job on the same box logged "app never became
> interactive" with an empty body while the backend was ready.
>
> This change makes that class self-diagnosing in the job log:
> - e2e/shm-preflight.ts: globalSetup for the core and update lanes refuses a
>   Linux /dev/shm under 512 MiB in ~2 s, naming --shm-size
>   (HERMES_E2E_ALLOW_SMALL_SHM=1 overrides).
> - core playwright config: maxFailures 3, so a lane-wide failure ends after
>   ~9 min and the end-of-run failure details reach the log.
> - waitForInteractive default 90 s (was 180 s, equal to the test timeout,
>   so its route/DOM/main-process diagnosis could never be printed).
>
> Tests: e2e/shm-preflight.unit.test.ts, e2e/core/lane-budget.unit.test.ts;
> both lanes' globalSetup run for real in a --shm-size 64m container (refused
> in 2 s) and core at 2g (passes).

## Code

- `apps/desktop/e2e/core/global-setup.ts`
- `apps/desktop/e2e/core/harness.ts`
- `apps/desktop/e2e/core/playwright.config.ts`
- `apps/desktop/e2e/shm-preflight.ts`
- `apps/desktop/e2e/update/global-setup.ts`

## Tests

Source test files changed by this commit:

- `apps/desktop/e2e/core/lane-budget.unit.test.ts`
- `apps/desktop/e2e/shm-preflight.unit.test.ts`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

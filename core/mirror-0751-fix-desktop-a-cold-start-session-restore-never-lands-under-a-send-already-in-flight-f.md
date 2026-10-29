# fix(desktop): a cold-start session restore never lands under a send already in flight from the fresh chat [daveey]

Patch 0751, source `a1ea00192f9a6d2a2535bfe5af238611a29de9f9`.

## Behavior

fix(desktop): a cold-start session restore never lands under a send already in flight from the fresh chat [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'`
- `describe('useDesktopIntegrations', () => {`
- `import { pathFromHermesDeepLink, resolveHermesOpenPath } from '@/lib/hermes-open`
- `export function useDesktopIntegrations({`
- `import { $clarifyRequests, clearClarifyRequest } from '@/store/clarify'`
- `export function useSessionActions({`
- `export function migrateSessionDraft(fromKey: string | null | undefined, toKey: s`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Launch 2 of e2e/update/first-run.spec.ts: the user pressed Enter on the fresh
> chat as soon as the composer enabled; the resume-last-session restore then
> navigated to the remembered chat mid session.create, the send aborted as a
> session switch ([submit-drift-abort] mid-create / post-create-null) and the
> message was dropped. A fresh-chat send in flight now spends the restore latch:
> the chat the user sent in is where they stay.

## Code

- `apps/desktop/src/app/contrib/hooks/use-desktop-integrations.ts`
- `apps/desktop/src/app/session/hooks/use-session-actions/index.ts`
- `apps/desktop/src/store/composer.ts`

## Tests

Source test files changed by this commit:

- `apps/desktop/src/app/contrib/hooks/use-desktop-integrations.test.tsx`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

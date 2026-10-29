# fix(desktop): re-list while a failed sidebar list leaves owners stale; log freshness flips [daveey]

Patch 0848, source `dbf62bea792643c94f8a361066633e443266a323`.

## Behavior

fix(desktop): re-list while a failed sidebar list leaves owners stale; log freshness flips [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `describe('actual hub poller + list refresh freshness', () => {`
- `import {`
- `import { refreshCronJobs as refreshCronJobsStore } from '../../cron/cron-actions`
- `export function useSessionListActions({ profileScope }: UseSessionListActionsArg`
- `describe('mergeOfflineFallback', () => {`
- `export const $sessionListFreshness = atom<Record<string, boolean>>({})`
- `export function recordSessionGatewayUnavailable(connection: string): void {`
- `export function recordSessionListResult(`
- `export function hideOfflineSession(row: SessionInfo): void {`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A failed list stamps its owners stale and their rows paint as cached copies (no
> red/grey stripe, 0/0 kanban badge) until a successful list. Nothing re-listed on
> its own: after a Desktop restart whose first list failed, this Mac's rows stayed
> blank 15:30-15:46 PDT until the user switched gateways, and the session status
> auditor filed missing red / badge mismatch / missing waiting for every such row.
>
> - useSessionListActions re-lists with 15/30/60 s backoff while an owner a LIST
>   marked stale is still stale; status-probe outages (hub-sync) are not retried.
> - [session-freshness] desktop.log line on every owner freshness flip with the
>   request that caused it.

## Code

- `apps/desktop/src/app/session/hooks/use-session-list-actions.ts`
- `apps/desktop/src/store/offline-sessions.ts`

## Tests

Source test files changed by this commit:

- `apps/desktop/src/app/session/hooks/use-session-list-actions.test.tsx`
- `apps/desktop/src/store/offline-sessions.test.ts`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

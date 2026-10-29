# fix(desktop): a failed hub list no longer blanks this Mac's sidebar stripes [daveey]

Patch 0843, source `7adaa5306ee98531eafe21d4f15bddbf86969b1a`.

## Behavior

fix(desktop): a failed hub list no longer blanks this Mac's sidebar stripes [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `describe('mergeOfflineFallback', () => {`
- `export function recordSessionListResult(`
- `export function mergeOfflineFallback(`
- `describe('recordStripeTransition', () => {`
- `export type StripeTraceRow = Pick<SessionInfo, 'id'> &`
- `function rowListSource(row: StripeTraceRow): string {`
- `export function formatStripeTransition(`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Rows of this Mac's own chats lost their stripe (periodic, waiting, stuck, done) for
> seconds to 16 minutes whenever the hub backend dropped. The sidebar list request
> follows the window's connection; when it failed on the hub, recordSessionListResult
> marked EVERY owner in $sessions stale, local included. mergeOfflineFallback then
> re-added the cold-start boot-snapshot copies of local chats (frozen at launch, no
> cron_active / kanban_* / goal stamps) and those painted over the live projects-tree
> rows. desktop.log shows each periodic->none flip at the exact second of a hub
> sidebar-list failure (15:46:35, 16:05:28, 16:22:42 PDT 2026-10-28).
>
> - recordSessionListResult: a failed list (or an error naming no connection) on a
>   REMOTE gateway marks only that gateway's owners stale; the primary's list still
>   covers everyone (it can carry other gateways' rows).
> - mergeOfflineFallback: once the own backend has a live row in the list, its boot
>   copies are never re-added (older chats paint from the stamped live tree).
> - stripe-transition trace: logs cron_active and names fallback rows source=fallback
>   (they were mislabelled project-tree, which hid this cause).
>
> Tests: offline-sessions (4 new: hub failure keeps local live, error scoping, no boot
> copies after own backend answered, cold start still paints boot copies; 3 fail on
> the old code), session-dot-state trace source. Store/sidebar/session-hooks suites
> green except voice-prefs (fails on origin/main too).
>
> Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Code

- `apps/desktop/src/store/offline-sessions.ts`
- `apps/desktop/src/store/session-dot-state.ts`

## Tests

Source test files changed by this commit:

- `apps/desktop/src/store/offline-sessions.test.ts`
- `apps/desktop/src/store/session-dot-state.test.ts`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

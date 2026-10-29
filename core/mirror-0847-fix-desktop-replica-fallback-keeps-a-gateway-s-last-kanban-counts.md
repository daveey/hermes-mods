# fix(desktop): replica fallback keeps a gateway's last kanban counts [daveey]

Patch 0847, source `30c707ff2edda7122405d67461f2cf2e8509a80a`.

## Behavior

fix(desktop): replica fallback keeps a gateway's last kanban counts [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `describe('remote project groups from the hub replica', () => {`
- `import {`
- `async function fillFromReplica(connectionId: string, entry: RemoteTreeEntry): Pr`
- `$hiddenGatewayIds.listen(() => {`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> A non-active gateway's project tree falls back to the hub replica on cold
> start (before its first live answer) and after any failed background
> refresh. Replica records carry no kanban_* annotations, so every row of
> that gateway painted an empty task badge and no waiting/stuck stripe
> until a live tree landed. Seen live: after a Desktop restart with the hub
> active, 17 This-device rows showed 0 open / 0 blocked for 16 min (session
> status audit, two runs), recovering only when a local chat was opened.
>
> Remember the task evidence each gateway's rows last carried (including the
> boot snapshot tree replaced by the first apply) and carry it onto replica
> rows of the same connection/profile/session. The original
> kanban_observed_at is kept, so freshness checks still treat it as expired
> until live data arrives; evidence never crosses gateways.

## Code

- `apps/desktop/src/store/projects.ts`

## Tests

Source test files changed by this commit:

- `apps/desktop/src/store/projects-hub-replica.test.ts`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

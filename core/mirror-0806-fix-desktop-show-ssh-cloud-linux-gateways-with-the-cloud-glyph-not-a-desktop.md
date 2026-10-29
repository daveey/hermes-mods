# fix(desktop): show SSH cloud/Linux gateways with the cloud glyph, not a desktop [daveey]

Patch 0806, source `31079f050a46f53f540d158e1af66ab19e8eecb7`.

## Behavior

fix(desktop): show SSH cloud/Linux gateways with the cloud glyph, not a desktop [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `describe('gatewayDevice', () => {`
- `export type GatewayDevice = 'cloud' | 'desktop' | 'laptop' | 'mini'`
- `export function gatewayDevice(`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> hermes-hub is an ssh connection at a bare IP labeled 'hub (EC2)'; the name
> heuristic only knew mini/laptop, so it fell through to Monitor and read as the
> MacBook. Remote connections with a cloud-ish name, a /home or /root hermes
> path, or a stock cloud login (ubuntu/ec2-user/root/admin) now map to cloud.

## Code

- `apps/desktop/src/app/chat/sidebar/gateway-device-icon.tsx`

## Tests

Source test files changed by this commit:

- `apps/desktop/src/app/chat/sidebar/gateway-device-icon.test.ts`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

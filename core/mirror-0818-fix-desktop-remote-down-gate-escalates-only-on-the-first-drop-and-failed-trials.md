# fix(desktop): remote down gate escalates only on the first drop and failed trials [daveey]

Patch 0818, source `7ab44c136e8bec3eafedc99433edaa88ee727722`.

## Behavior

fix(desktop): remote down gate escalates only on the first drop and failed trials [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `describe('remote down gate', () => {`
- `// backend" errors and blocked UI work behind them. This gate records the outage per pool key`
- `export function isRemoteTimeout(error: unknown): boolean {`
- `export class RemoteDownGate {`
- `export async function dispatchThroughRemoteDownGate<TConnection extends { baseUr`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Review of 08c725d741: every in-flight request failing in one outage bumped the
> backoff, so 5 concurrent resets jumped it to the 30 s cap and the hub kept
> fast-failing ~20 s after a ~10 s restart. A stale failure also cleared the
> trial slot, letting a second trial in.
>
> Admissions now carry a per-key epoch and the trial an id. Only the first
> up->down transition and the owning trial's failure escalate; only the trial
> (or a request sent after the down-mark, e.g. probe retire then reconnect)
> reopens; stale in-flight verdicts are ignored. Tests fail on 08c725d741.

## Code

- `apps/desktop/electron/remote-down-gate.ts`

## Tests

Source test files changed by this commit:

- `apps/desktop/electron/remote-down-gate.test.ts`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

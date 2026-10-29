# test(desktop): spy the live localStorage.setItem in voice-prefs failure cases [daveey]

Patch 0919, source `5ca8cccfbdbee6664c4246a6d4d0870d78c54a8f`.

## Behavior

test(desktop): spy the live localStorage.setItem in voice-prefs failure cases [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `import {`
- `it('migrates the legacy preference once, not on every refresh', async () => {`
- `globalThis.ResizeObserver ??= InertResizeObserver as unknown as typeof ResizeObs`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Node 26's vitest.setup.ts stand-in is a plain object, so a Storage.prototype
> spy never runs. The quota and SecurityError cases wrote "false" and JS checks
> failed on main (run 40827146011, voice-prefs.test.ts:45 and :74). jsdom is the
> opposite — an own-property spy is ignored — so spy whichever function the live
> call actually invokes.
>
> The prototype-only spy in 0a461c1d8f never got a completed CI run.

## Code

- `apps/desktop/vitest.setup.ts`

## Tests

Source test files changed by this commit:

- `apps/desktop/src/store/voice-prefs.test.ts`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

# fix(desktop): the cold-start restore stands down once the user typed in the fresh chat; e2e update harness picks the relaunched main, not its crashpad handler [daveey]

Patch 0752, source `cded83cddc8c86978aa9f9bd9e6bec63ba089029`.

## Behavior

fix(desktop): the cold-start restore stands down once the user typed in the fresh chat; e2e update harness picks the relaunched main, not its crashpad handler [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `function exeOf(pid: number): string {`
- `describe('useDesktopIntegrations', () => {`
- `import { pathFromHermesDeepLink, resolveHermesOpenPath } from '@/lib/hermes-open`
- `export function useDesktopIntegrations({`
- `export function beginFreshChatSend(): () => void {`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Second first-run repro: launch 2 typed into the fresh chat before the session
> list arrived; Enter was not yet accepted, and the resume-last-session restore
> then swapped the remembered chat in and the typed text out. The restore now
> also stands down when the fresh draft differs from what this launch found
> (flushed first; a draft left from an earlier launch still restores).
>
> app-update: desktopMainProcesses matched any binary in the packaged dir with no
> --type=, so the pre-update app's chrome_crashpad_handler (re-parented to init)
> was taken for the relaunched app and SIGTERMed; the real app never quit. Match
> the packaged exe's own basename.

## Code

- `apps/desktop/e2e/update/harness.ts`
- `apps/desktop/src/app/contrib/hooks/use-desktop-integrations.ts`
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

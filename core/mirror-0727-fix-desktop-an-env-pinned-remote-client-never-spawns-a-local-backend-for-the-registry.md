# fix(desktop): an env-pinned remote client never spawns a local backend for the registry's 'local' primary [daveey]

Patch 0727, source `b9486154971001d85d0d9a3c18d86112ad8e1089`.

## Behavior

fix(desktop): an env-pinned remote client never spawns a local backend for the registry's 'local' primary [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `test('registry local route: v1 REMOTE global mode forces a genuinely-local backe`
- `function normalizedSshTarget(route: { host?: unknown; port?: unknown; user?: unk`
- `import {`
- `async function ensureRegistryBackend(`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> HERMES_DESKTOP_REMOTE_URL pins the app to a remote but never enters the
> connections registry, so 'local' stays the registry primary and every
> primary-routed renderer call arrives as an explicit 'local'. The hub-sync
> replica read (~0.3 s after boot) was the first: ensureRegistryBackend forced
> a genuinely local child for it (globalRemote branch), the published local
> route then pulled the window's REST traffic onto that child, and e2e
> remote-topology failed 'the client spawned no backend of its own'.
>
> With the env pin and 'local' as primary, the local entry IS the primary and
> delegates to the v1 route (the env remote). Migrated v1-remote users (remote
> is the registry primary) still force-local 'This device'.
>
> Proof: remote-topology.spec.ts in the CI runner image: fails before (local
> '--profile default serve' child, first dial /api/plugins/hub-sync/sessions
> conn=local), passes after with no client backend.

## Code

- `apps/desktop/electron/connection-registry.ts`
- `apps/desktop/electron/main.ts`

## Tests

Source test files changed by this commit:

- `apps/desktop/electron/connection-registry.test.ts`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

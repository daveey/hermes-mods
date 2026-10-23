# Plugin preview door (`ctx.openPreview`)

Patch: 0195

## Behavior

Desktop plugins can open a local file (or http(s) URL) in the app's right-rail preview pane, where Markdown
renders, instead of handing it to the OS. Used by `desktop-plugins/user-steps` and `desktop-plugins/blocked-panel`:
clicking a `.md` / `.markdown` / `.mdx` path in the **For you** strip opens it rendered in the sidebar.

## How it works

`PluginContext.openPreview(target, { cwd? })` runs the target through `normalizeOrLocalPreviewTarget` (the same
main-process normalizer the file tree uses; `~` and relative paths resolve against `cwd`, else the focused
workspace) and calls `openPreview` on the result. It resolves `false` without opening a tab for a missing file,
a directory, an empty target or no Electron shell, and never throws, so a plugin falls back to
`ctx.os.openExternal`.

## Code

- `apps/desktop/src/contrib/plugin.ts`: `openPreview` on `PluginContext`, `openPluginPreview()`.

## Tests

- `apps/desktop/src/contrib/plugin.test.ts` › `createPluginContext.openPreview`: a resolvable file opens one tab
  via the normalizer (with `cwd` passed through); missing file, directory and blank target resolve false and open
  nothing.
- Plugin side: `node --test desktop-plugins/user-steps/test/plugin.test.mjs` (DOM click on a `.md` path in a
  blocked card → `ctx.openPreview`; a `.log` path → `ctx.os.openExternal`) and
  `node --test desktop-plugins/blocked-panel/test/helpers.test.mjs` (`openLink` routing, failed preview and
  older-core fallback).

## Applying

Apply 0195 on top of the series (independent of other patches). Plugins feature-detect it:
`typeof ctx.openPreview === 'function'`.

## Gotchas

- Without the patch (stock core) plugins keep the OS handler for every path.
- Only the renderer changes; it lands after the next Desktop rebuild/restart.

## Plugin?

No: plugins have no access to the preview store; this is the door they use.

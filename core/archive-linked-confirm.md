# Confirm before archiving a chat with linked tasks or machines

## Behavior

Archiving a chat only hides it. Its open Kanban tasks and rented machines keep running (and billing) where you
can no longer see them. With this patch, archiving a chat that still has linked work opens a confirm dialog first:
"Archive “<title>”?", one line per kind of linked work ("Kanban tasks: 2 open · 1 blocked",
"Rented machine: vast-4090 (running, $0.40/h)"), and **Keep chat** / **Archive anyway**. A chat with nothing
linked archives at once, as before. This covers every archive path (row menu, hover button, tile menu, ⌘⇧A
archive-and-advance), because they all go through `archiveSession`.

## How it works

Patch **0110** adds `store/archive-guards.ts`, a registry of async "guards" that report linked work as
`{ label, value }` lines. Before the optimistic hide, `archiveSession` builds the list from two sources and,
when it isn't empty, awaits the shared imperative `confirm()` (`store/confirm.ts`, the dialog mounted at the
shell root) with the lines as `details`:

- **Core Kanban check**: the row's own `kanban_active` + `kanban_blocked` counts (the backend sums them over the
  compression lineage, so they match the sidebar badge; see [kanban-session-links.md](kanban-session-links.md)).
  No extra request.
- **Plugin guards**: new `ctx.registerArchiveGuard(guard)` on the plugin context. The guard receives
  `{ sessionId, session, ids, profile, connectionId }` (the row's owning gateway, so cross-gateway rows are
  checked on their own gateway). Guards run in parallel. Each is capped at 2.5 s, and one that throws or times out
  is skipped, so a broken plugin can delay an archive but never block it. The registration is removed when the
  plugin unloads.

The public [session-tasks](../desktop-plugins/session-tasks/) plugin registers the machines guard: it walks the
chat's `parent_session_id` lineage on the row's gateway, reads `/api/plugins/compute-rentals/inventory`, and lists
machines whose ownership binding matches the gateway, profile and any lineage id. On hosts without this patch,
`ctx.registerArchiveGuard?.()` is a no-op.

`archiveSession(id, { skipLinkedConfirm: true })` archives without asking (for future bulk or scripted callers).

## Code

- `apps/desktop/src/store/archive-guards.ts`: registry, `collectArchiveLinks`, `kanbanArchiveLinks`
- `apps/desktop/src/app/session/hooks/use-session-actions/index.ts`: the check in `archiveSession`
- `apps/desktop/src/contrib/plugin.ts`: `PluginContext.registerArchiveGuard`
- i18n: `desktop.archiveLinked*` in `types.ts`, `en/de/es/fr/ja/ru/zh.ts`, `ar_chat.ts`, `zh-hant_chat.ts`;
  `locales/_keys.desktop.json`
- `website/docs/developer-guide/desktop-plugin-sdk.md`: the new context method

## Tests

From `apps/desktop`:
`npx vitest run src/store/archive-guards.test.ts src/app/session/hooks/use-session-actions/archive-linked-confirm.test.tsx`
(10 passing; with the `archiveSession` hunk reverted, the 3 confirm cases fail). It covers no prompt when nothing
is linked (control), Cancel keeps the row, Confirm archives, plugin-guard lines plus the context the guard gets,
`skipLinkedConfirm`, and guard timeout/throw/dispose/unload. Regression: `use-session-actions.test.tsx`,
`unarchive-session.test.tsx`, the tile-delegate tests, `src/contrib` and `src/i18n` all pass. `tsc --noEmit` is clean.
Plugin: `node --test desktop-plugins/session-tasks/test/projections.test.mjs`.

## Applying

`git am -3` patch 0110 on top of the series (it needs the fork's `kanban_active`/`kanban_blocked` row fields from
the kanban-session-links patches, or the Kanban line never shows). Then rebuild Desktop. The session-tasks plugin
hot-reloads, but its guard only takes effect once the rebuilt Desktop provides `registerArchiveGuard`.

## Gotchas

- The Kanban counts come from the last sidebar refresh, so a task created seconds ago may not be counted yet.
- Machine ownership exists only for rentals bound in the compute-rentals registry (the rent-machine skill does
  this automatically). An unbound box isn't "linked".
- Auto-archive (backend, idle chats) does not ask. It runs server-side with nobody to confirm.

## Why not only a plugin?

`archiveSession` is renderer core with no hook before the RPC. A plugin could only intercept clicks on the DOM,
which misses the hotkey and tile menu and breaks on markup changes. The core part is a small generic guard API,
and the data-specific check (machines) stays in a plugin.

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
  Fresh evidence needs no extra request. Patch **0383** refreshes missing, expired or unknown evidence before warning: it captures the row owner (omitted connection = registry primary), walks ancestry and queries `/api/session-kanban-tasks` with that connection/profile pinned. The whole refresh is bounded to 2.5 seconds. Read failures, malformed ancestry/task responses, stale replicas and unknown obligations remain uncertain; a successful empty answer archives without a spurious task warning.
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

`git am -3 --empty=keep` patch 0110 on top of the series (it needs the fork's `kanban_active`/`kanban_blocked` row fields from
the kanban-session-links patches, or the Kanban line never shows). Then rebuild Desktop. The session-tasks plugin
hot-reloads, but its guard only takes effect once the rebuilt Desktop provides `registerArchiveGuard`.

## Regression diagnosis and verification (0383)

Two independent defects produced similar warnings: the session-tasks plugin rejected an omitted archive connection before issuing REST, despite the guard contract defining it as registry primary; core classified expired sidebar observations as unknown without attempting a new read. Existing tests supplied resolved owners or tested only the conservative failure branch, not successful revalidation. The plugin now resolves primary through the SDK registry and pins the inventory profile; it never substitutes the active gateway. Dialog copy in all existing locale overlays describes a status check, not proof of linked work.

Executed on core `f63cdf362c`:
- `confirm-linked-archive.test.ts`: primary/remote empty revalidation, compressed ancestry, confirmed tasks, offline/malformed/stale/unknown failures with recovery, and bounded timeout. The missing-read cases failed before the change; uncertainty copy also failed before its correction.
- Six Vitest files (the new file, `archive-linked-confirm.test.tsx`, `use-session-actions.test.tsx`, `unarchive-session.test.tsx`, `archive-guards.test.ts`, `overlay-completeness.test.ts`): **189 passed**.
- `tsc --noEmit` and ESLint on changed action modules: clean.
- Plugin projection and strip suites: **42 passed**, including omitted primary and explicit remote machine ownership.
- Full public series **380 patches** applied cleanly with `git am -3 --empty=keep` at merge-base `4957bbd0a0`; resulting archive helper matches the tested fork source. Coverage check passed.

These are automated client tests, not proof that a running Desktop bundle has loaded the revision. The disk plugin can hot-reload after sync; the core change requires the normal Desktop update/build activation. No Desktop restart is performed by the repair.

## Gotchas

- The Kanban counts come from the last sidebar refresh, so a task created seconds ago may not be counted yet.
- Machine ownership exists only for rentals bound in the compute-rentals registry (the rent-machine skill does
  this automatically). An unbound box isn't "linked".
- Auto-archive (backend, idle chats) does not ask. It runs server-side with nobody to confirm.

## Why not only a plugin?

`archiveSession` is renderer core with no hook before the RPC. A plugin could only intercept clicks on the DOM,
which misses the hotkey and tile menu and breaks on markup changes. The core part is a small generic guard API,
and the data-specific check (machines) stays in a plugin.

## Follow-up patch (0560)

**0560** (source `5c653ed500`): the archive hotkey and tile menu archive without a selected row and resolved the owner from Recents only. While a remote gateway's pooled backend reconnects, the unified list has none of its rows, so an open hub chat had no owner and the preflight showed "Task status unavailable" for a chat with nothing linked. The owner is now resolved like resume and branch do (tagged project-tree/cron/messaging row, else the owner hint) in `archive-owner.ts`, and `refresh-archive-tasks.ts` logs why the task preflight returned unknown. Tests: `archive-linked-confirm.test.tsx` (new cases).

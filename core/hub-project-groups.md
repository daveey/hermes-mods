# Project groups of every gateway from the hub; rename another gateway's project while it sleeps

Patches: 0602 (core CLI), 0603 (Desktop). Phase 6b of [kanban-central-service.md](kanban-central-service.md)
(approved by David 2026-10-27, cut straight over with no soak). Needs hermes-hub client 1.9.0 on every gateway and
the hub-sync plugin's `GET /projects` (hermes-mods `plugins/hub-sync`).

## What it does

- **Each gateway publishes its project tree.** `hermes project tree` (0602) prints every profile's own sidebar tree as
  JSON: the same read-only `build_tree` call the all-profiles REST route makes (`profile_project_trees` in
  `hermes_cli/web_routers/profiles.py`, shared by both), lanes without session rows, previews as `previewIds`, every
  claimed `sessionIds`. The hermes-hub collector publishes one `projects` record per node
  (`<profile>/<p_id | auto:<hash> | none>`) and stamps each session record with its node (`project`).
- **Shared name, icon, color.** An explicit project's `name`, `icon` and `color` are hub shared fields (per-field HLC,
  like session titles). Any gateway sets them; the owner applies them with `hermes project update <id> --FIELD=V`
  (0602) when it is next awake. AUTO and Home groups are not settable (`not_settable`).
- **The Desktop draws an unreachable remote from the replica** (0603). `refreshRemoteProjectTree`
  (`src/store/projects.ts`) paints a remote's groups from hub-sync `/projects` before its first live answer this run
  and whenever its live fetch fails, so an asleep gateway's groups stay. An awake gateway's live tree still wins.
- **Rename and Appearance on another gateway's declared project.** Its node carries `remoteProject`
  (`gateway-project-merge.ts`); the menu offers Rename and Appearance (`project-menu.tsx`), and `updateProject` sends
  one shared-field set per declared project the group stands for (`updateRemoteProject`), patches the cached remote
  tree so repaints keep it, and rolls back if the hub refuses.

## Tests

- Core: `tests/hermes_cli/test_profiles_sidebar_scope.py::TestProjectTreeCli` (per-profile and unmerged, read-only,
  an unreadable profile is `ok: false`); `tests/hermes_cli/test_projects_cli.py` (`update` sets and clears).
- Desktop: `src/store/projects-hub-replica.test.ts` (asleep remote from the replica, cold-start paint then live wins,
  rename = one set per declared member and kept across repaints, clear = `""`, refusal rolls back);
  `project-menu.test.tsx` (Rename and Appearance on a remote declared project, neither on a remote AUTO group).
- hermes-hub: `client/tests/test_shared_projects.py`, `worker/test/shared-projects.test.ts`; hub-sync:
  `plugins/hub-sync/tests/test_projects_view.py`.

## Gotchas

- `tui_gateway.server` points `sys.stdout` at stderr on import (its JSON-RPC channel), so `project tree` keeps the real
  stdout before importing the builder.
- The tree costs seconds (CLI start, git probes: about 9 s on a busy MacBook), so the collector caches it in
  `projects-tree.json` and reruns it only on a projects.db change, a new interactive session, an applied project
  field, or every 10 min. Counts and previews in the replica can lag an awake gateway by that much; the Desktop uses
  the live tree for awake gateways.
- Nodes carry no `sessionIds` on the hub (thousands of ids would pass the 64 KiB record cap); the session record's
  `project` stamp carries membership, and the watcher's fast path stamps from the cached tree so it never strips it.
- Groups still merge across gateways by label in the Desktop, so renaming one gateway's project can split it from a
  same-named group on another (as a local rename does).

# Row actions and the right-click menu

David's preference: a row's card, dependency and session opens live only in its right-click menu, in the order
Open session / Open card / Open dependency. Never render them as inline text links. Only http(s) links (CI, PR)
and an ask row's own Open card stay buttons. A line row's own card also opens by double-clicking its state square.

- Producer (`model.py`): `open_card.target={id,gateway,profile,board}`. The waited-on card is an `open_card` with
  `role:'dependency'` and label `Open dependency`. `open_session.target={session,gateway,profile}`.
- Renderer: build the menu from the SDK `ContextMenu`/`ContextMenuTrigger`(asChild)/`ContextMenuContent`/
  `ContextMenuItem` exports through a namespace import. If they are absent (older host, test stub), return the bare
  row. Show items with no target disabled. Treat an older model's unroled second `open_card` as the dependency.
- "Session" means the card's owning chat: `tasks.session_id`, the same chat core's Blocked sidebar groups by.
  Run metadata `worker_session_id` is an opaque `local-<hash>`, so it cannot be opened. Open the chat with
  `host.openSession(id, {route:{connectionId, mode, profile, targetProfile}})` on the card's gateway connection
  (`tasksBridge().connectionForHubGateway`).
- Drop `open_session` when its session is the panel's own chat or a compression ancestor (`bundle.session` +
  `bundle.lineage`). Otherwise the item is a no-op on most rows.
- Before selecting a process-open API, inspect its inventory boundary: core `process.list` filters its backend's registry by exact session key, while canonical sidebar/process evidence can include foreign checkpoints and compression lineage. A matching process ID alone does not establish that the RPC can return it. Preserve gateway/profile/session ownership, fail explicitly on a missing inventory entry, and label any retained panel observation as an at-open snapshot rather than fresh RPC evidence. Do not silently use the active backend or acknowledge logs to populate a read-only viewer.
- Tests: the hub-model jsdom stub must provide ContextMenu* stand-ins (the content renders only when the harness
  enables it). Assert item order, enabled/disabled state, the dispatched action per item, and that no inline
  parent-card button remains.

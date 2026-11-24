# Reconciling lane-strip counts with the session pane and global totals

Use this when the user says lane pills show blocked or running cards that he can't find, or that the pill numbers don't add up to the global totals.

## Probe the live view (on the hub; read-only)

1. The deployed plugins are symlinks: `~/.hermes/plugins/{lanes,session-panel}` -> `~/code/hermes-mods/plugins/...`. Read the code there. `~/.hermes/repos/hermes-mods` has no lanes code.
2. Load `plugins/lanes/dashboard/plugin_api.py` with importlib, run it under `~/.hermes/hermes-agent/.venv/bin/python`, and call `_lane_view(6.0, '6h', True)`. Dump the result to a scratch JSON file. The useful fields are `status` (global totals by reason), `lanes[n].strip` (blocked/running/other plus cap/in_share/over) and `cards[]` (each with `reason` and `lane`).
3. Read card rows from the fleet replica, the same source the view uses, not the local `kanban.db`: `kanban_db_connect.connect_closing()` then `kanban_worker_node.open_replica(conn)`. Pull `status`, `block_kind`, `human_request` and `task_chat_links` for each card in question.
4. To see what the user actually sees, read the Desktop renderer on the Mac. Use the inspecting-hermes-desktop-dom `rend-eval.mjs` over tailnet_exec and pass the expression positionally, as a single argument. It reads sidebar `[data-sidebar-stripe]` values and the pill `aria-label`s. Close the inspector port afterwards.

## Known divergence mechanisms (check these first)

- **Two "needs you" predicates.** The lanes plugin gets its "you" reason from core `kanban_lanes_flow.classify_reason`, which fires on `human_request` OR `block_kind in (needs_input, capability)` regardless of status. The session pane (`session-panel/model.py` ask logic) paints red only when a card is linked to a session, is in blocked/triage, and has a complete human_request. Count each card under the reason that applies to it.
- **Stale `block_kind`.** A card that went blocked -> scheduled/triage can keep `needs_input`. With no human_request it is a false "you" in the lanes count and correctly not red in the pane.
- **Unlinked asks.** Cards from headless producers (watchers, sweeps, spend budget, sync) carry a real human_request but have no `task_chat_links`. The pane is indexed by session, so it shows nothing for them. These are the asks that actually get lost.
- **Board scope.** Lane counts come from the hub's default board (fleet replica) only. Other hub boards (`hermes kanban boards`) and the mini's separate BB Hermes board (`~/.bb-hermes/kanban/boards/bb/kanban.db`, its own `bb_gate`/`bb_governor` with state in `~/.bb-hermes/bb-dev/{governor-state,gate-state}.json` and `holds/*`) are not counted. When the Baby Bear board shows many ready cards and the babybear pill shows few, compare board status counts on each host. Read `holds/*` to find what is stopping BB starts. A hub `dispatch_skipped` count over 48 h shows past triggers, not cards held now.
- **Running split.** `strip.running` always sums to `status.run`. A pill that shows `in_share` and a separate `+over` makes the blue numbers look as if they don't add up.

Report the count for each mechanism with card ids. The structural fix is one shared predicate, enforced by contract and invariant tests, so that lanes `status.you` equals the pane's red count and the rendered blue numbers sum to `status.run`.
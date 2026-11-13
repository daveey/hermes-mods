# Desktop: per-plugin JSON pools and single-flight hub-sync status

Patches: **3697**.

## Behavior
- Every `/api/plugins/<id>/...` JSON request from the Desktop app uses a keep-alive agent dedicated to that plugin, capped at `PLUGIN_POOL_MAX_SOCKETS = 8`. A plugin with slow reads (e.g. 30 s) queues only behind itself and cannot starve a kanban board read.
- Core routes keep the shared 50-socket JSON pool; kanban and hub-sync keep their own sockets; the probe pool is unchanged.
- The hub-sync `/status` read is single-flight per connection: overlapping askers share one request, and a miss (failure or hub-less answer) is not asked again for 60 s.
- Kanban board polling is single-flight: a slow read does not start overlapping polls.

## How it works
3697: `api-transport.ts` / `json-fetch.ts` lazily create a per-plugin agent (the Node per-origin socket cap applies per connection and plugin). `hub-sync.ts` adds in-flight dedupe and a 60 s miss cache for `learnHubGatewayName`. The kanban board poll options are shared as `boardPoll()` / `boardPollMs()` in `api.ts`, used by `board.tsx` and `plugin.tsx`.

## Code
- `apps/desktop/electron/api-transport.ts`
- `apps/desktop/electron/json-fetch.ts`
- `apps/desktop/electron/json-fetch.test.ts`
- `apps/desktop/src/plugins/kanban/api.ts`
- `apps/desktop/src/plugins/kanban/api.board-poll.test.ts`
- `apps/desktop/src/plugins/kanban/board.tsx`
- `apps/desktop/src/plugins/kanban/plugin.tsx`
- `apps/desktop/src/store/hub-sync.ts`
- `apps/desktop/src/store/hub-sync.test.ts`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `apps/desktop/electron/json-fetch.test.ts`: `one plugin with 30 s responses cannot starve a concurrent kanban/board read`, `names the plugin a path is addressed to, and only plugin paths`.
- `apps/desktop/src/plugins/kanban/api.board-poll.test.ts`: `a slow board read does not start overlapping polls`.
- `apps/desktop/src/store/hub-sync.test.ts`: `learns a connection's hub name from its own /status and persists it`, `a slow /status is single-flight: overlapping askers share one read`, `a failed or hub-less /status is not re-asked on every board poll`, `posts to the primary gateway's hub-sync`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A single shared pool for all plugin routes was rejected because the kanban board is itself a plugin route.
- The cap is per (connection, plugin), so total sockets grow with the number of plugins in use.

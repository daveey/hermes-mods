# lanes

Hub-computed view model and push stream for `desktop-plugins/lanes`. Core's
`hermes kanban lanes` remains the authority for classification, shares and policy;
this plugin adds presentation projections, not scheduling behaviour.

| Route | Contract |
|---|---|
| `GET /api/plugins/lanes/v1/lanes?hours=1\|6\|24\|168&window=1h\|6h\|24h\|7d&expanded=false` | Full strip view: per-lane running, not_ready, blocked_on_you, open, ready, held, done, share, target, actual, drift; `totals`, scope and planner notes |
| `GET /api/plugins/lanes/v1/lanes/flow?window=1h\|6h\|24h\|7d` | Same view model, expanded. Also preserves top-level `cards`, `window` and per-lane limits/usage for older Desktop renderers |
| `WS /api/plugins/lanes/v1/lanes/stream?hours=6&window=6h&expanded=false` | Initial `{version, view, params}`, then changed views only. Client may send `{hours, window, expanded}` (any subset) without reconnecting |
| `PUT /api/plugins/lanes/v1/lanes/<lane>` | `{share, paused, admit_per_day, max_workers, cards_per_day, usd_per_day}`, any subset; last three accept null to clear on supporting core |
| `GET /api/plugins/lanes/v1/held?lane=x` | Cards at the lane gate |
| `POST /api/plugins/lanes/v1/held/<card>/admit` | Release one: `{reason: "..."}` |

## Render-only rule

**All counts and grouping live on the hub.** `lane_view(hours, window, expanded)`
returns the strip totals and, when expanded, `floor: {columns, names, cells}` and
`flow.graphs: {project, source}`. Every Floor cell carries `lane`, `step`, `count`,
`piling`, `summary`, and `cards`. Graphs carry `sources`, `lanes`, `sinks`, `edges`
and `total`; sink nodes include dam hints. The server asserts conservation of
open cards through Floor cells and each Flow node/edge layer. Done is a separate
window-throughput count, not an open-card layer.

Desktop renders these numbers even when card lists disagree. It may calculate
pixel geometry, percentages for presentation, and edit PUT payloads; it never
recounts cards, recomputes drift/targets, groups sources, or infers dams. Target
and drift reflect the saved policy until Apply has reached the hub; editing a
slider does not invent a client-side forecast.

## Push and compatibility

The stream uses core's canonical WebSocket authorization (ticket/token/internal
identity), failing closed with 1008. It watches the local and fleet-replica
`task_events` cursor and policy/planner-note mtimes every 0.5 seconds, coalesces
changes for about one second, invalidates legacy read caches once per revision,
and sends only when the view/parameter hash differs. An unchanged board produces
only `{type: "heartbeat", version}` every 30 seconds. Time-window/age values are
snapshots at board/config changes, not a separate clock-driven feed.

Desktop performs one GET on mount and one on reconnect, then uses pushed views.
Expanded/window changes use stream messages. There are no interval GETs on a live
stream. If sockets are unavailable (including OAuth/browser-hosted Desktop), a
60-second GET fallback displays **not live** (it reads nothing while the window is hidden;
test: `push.test.mjs` "fallback poll reads nothing while the window is hidden"). A 45-second heartbeat timeout marks
a stalled connection unavailable. Apply and Reset retain their PUT/draft semantics;
a successful live write is reflected by the next hub push.

The plugin discovers the steering gateway (gated lanes/planner notes), then pins
reads, writes and the stream to that connection, independent of the active chat.
The current SDK `ctx.socket` has no connection/send/status API: unscoped older
hosts use it; native connection-pinned hosts use the same `getPluginWsUrl` ticket
bridge as core, with bounded lookups and reconnect backoff. OAuth stays on REST.
The render-only push interface needs no Desktop restart. Fast expanded projection
requires core's additive `snapshot(..., reader=...)` seam; older cores remain
compatible but use the previous synchronous PR reads until upgraded naturally.

A core exposing `Evidence(conn)` also shares one request-local ordered history
between PR-context invalidation and flow classification. Each payload is decoded
once, and dependency parent reads are reused within that view; no classified card
cache survives into the next request. Older cores retain the existing projection
path until upgraded naturally.

PR network observation runs in one process-shared, bounded background worker,
not the stream or GET request. A report consumes an immutable published reader.
Entries expire 60 seconds after read **start**, refresh becomes due at 30 seconds,
and publication/refresh/expiry wakes the stream's normal hash-deduplicated path.
Each URL binds to its own cards' evidence revisions and contract/status; unrelated
board events and plain heartbeat/watch ticks cannot starve publication. A changed
PR/head event on an owning card invalidates its old observation immediately on
projection. Results completing after invalidation cannot resurrect old approval.
Missing/expired/failed observations stay unknown under the existing classifier.
External GitHub changes not yet reported to the board are bounded by that TTL;
these display observations never authorize shipping. Core's fresh head confirmation
and all ship/review gates remain unchanged. No share/planner policy changes.

Floor/Flow, lane/card navigation, window/tab persistence, per-lane valves, explicit
null clearing, multi-parent dependency hints and the compact collapsed strip are
preserved. Unsupported cores return a reason; failed expanded reads retain strip
counts and an unavailable-flow explanation. Cost remains estimated reported usage.

Enable `lanes` on the gateway holding the board. Tests:

```sh
scripts/test-all -k plugins/lanes
node --test desktop-plugins/lanes/tests/*.test.mjs
```

The Desktop regression fixtures execute the actual Python producer before
rendering; a separate deliberately inconsistent fixture proves numbers are not
recounted on the client.

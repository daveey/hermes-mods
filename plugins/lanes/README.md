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
`task_events` cursor and policy/planner-note mtimes every 0.25 seconds. After quiet, the
first tick that sees a change projects it at once (no trailing debounce). Changes during
or just after a projection coalesce into one follow-up after a minimum 1 second idle
gap from the previous projection's end; new changes do not reset that deadline. Counts
are computed on the hub by core, using request-local evidence shared by the strip and
expanded flow; no cross-request card history memo is retained. Legacy read caches are
invalidated once per revision, and a frame is sent only when the view/parameter hash
differs. `&debug=true` adds a `timing` object to view frames (wall-clock epoch seconds:
`project_start`, `project_end`, `sent`; change-triggered frames additionally carry
`cursor_poll`, `detected`, `released`). Timing is outside the view hash and absent
otherwise. Debug frames also include `load: {pid, stream_viewers}`: accepted
lanes stream connections in that backend process, including non-debug viewers.
This is a point-in-time connection census, not a count of projection builds;
HTTP request traffic is recorded separately by the request tracer.
An unchanged board produces
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

On core with `kanban_lanes_sources.read_view`, strip and expanded panel share one
short, read-only pair of local/fleet snapshots and one whole-card source choice.
Primary-mode local rows (including picked-up copies) win until pending edits are
answered; otherwise strictly newer fleet revisions win, with ties local.
Tombstones on either source suppress deleted cards; missing parents stay missing
rather than trusting an older remote-parent mirror. Each card keeps its chosen
source's complete history and child-owned edges, including empty unlink sets.
PR context revisions include the chosen database identity, never bare cross-file
event IDs. Run/cost/done/admission usage stays on the canonical fleet connection,
so copies cannot double-count it. Held-card provenance is local to its source;
daily-cap usage remains canonical. Shadow/off modes keep the prior behavior.

GET viewers share the existing bounded view cache keyed by invalidation generation
and parent PR-observation cursor. Streams share in-flight projection RPCs by parameters
and requested revision, not retained views. No per-card classifier evidence is retained.
A changed cursor or successful write invalidates the next view; work already started may
finish with its original revision, while the first detected follow-up stays pending.
Tests exercise three real sockets with 40,000 history events and an unchanged stale
replica, plus separate cache-race and write-invalidation controls. Real deployment
latency still needs a live proof.

### Dedicated projection process

After authorization and parameter validation, the first stream viewer starts one
on-demand managed-runtime child using private stdin/stdout pipes. The parent still
owns board/config/planner/PR change detection, leading-edge scheduling, the 1 s
burst gap and socket versions/hashes. It polls the child's lightweight observation
cursor too; the child has no source watcher or autonomous projection loop. GET
continues its existing in-process path.

Both paths call the same borrowed-evidence projection. Each child job freshly opens
tracked `mode=ro` local/fleet handles, registers the existing sync mode without
installing schema, checks it against the parent's mode, and rolls back/closes before
publishing. This preserves primary-board local overlays, including archive before
replica convergence, and reopens rebuilt source files between jobs. Evidence/history
is request-local. SQL has a 250 ms busy timeout and 2 s progress budget.

RPC commands carry a fixed-size cursor digest, never PR URL lists. JSON framing,
size limits (16 KiB commands, 8 MiB replies), session/request/revision/parameter checks,
and captured plugin/core code hashes fence malformed or mixed-deployment replies.
Code identity movement fails unavailable until a matching natural backend rotation;
it never blesses new disk hashes from a stale parent. One projection runs globally,
with at most one pending revision per legal parameter key (32 keys) and 128 viewers.

Startup/job failure bounds are 5/10 s, not latency promises. Child failure uses a fresh,
uncached in-process fallback, one daemon slot held through actual completion with a
3 s delivery deadline and no executor queue. Retry backoff is 1/2/4/8/16/30 s;
backoff does not reuse stale successful frames. Socket receive/heartbeat tasks remain
independent of compute; writes have a 3 s timeout and a latest-only pending frame.
An idle child stops after 30 s without viewers; EOF exits even during hung work.
No service/unit or forced restart is added.

Debug frames add `projector: {mode, projector_pid, source_current}`. Mode is
`projector` or `fallback` (PID null for fallback); `source_current=false` means a
source change was detected during that revision's computation and a follow-up is
pending. `timing.project_start/end` come from the actual compute process. Neither
these diagnostics nor load/timing fields affect the view digest. The real subprocess
fixtures test sync-primary overlay without mocking `board_mode`, byte-identical
projection, source rebuild, identity/session fences, no-spawn auth, EOF during a
hung projection, independent heartbeats and fresh bounded fallback. Private captured
board backups are tested separately and never checked into this repository.

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

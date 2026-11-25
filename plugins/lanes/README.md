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

## Shared human-attention coverage

With the companion core API, `needs_you(card)` classifies both lane cards and
session-panel attention. Historical block kinds alone are not current asks.
The lane snapshot carries `attention.rows` from the same card evidence as
`status.you`, including active chat subscriptions. The Desktop's **Unlinked asks**
section shows red, read-only card links for headless asks and for cards whose
linked chat is not represented by a visible `needs_you` card entry. This last
coverage partition is necessarily client-local (sidebar filters are local); it
does not reclassify or change the server total. It never creates decision
requests, restores retired consent controls, or grants permission to execute.

The sidebar and strip hold one refcounted subscription, so the section works
without a mounted strip and does not add an independent poller. Blue pill
numerals show **total running**; `+N over` is a separate borrowing annotation,
not a subtraction from the blue count. Companion-core or membership failures
leave attention unavailable rather than fabricating card coverage.

## REST stale-while-revalidate

GET keeps the newest completed view for each exact `(hours, window, expanded)`
key (at most 32 legal keys). Revision changes and ten-second expiry start one
background refresh per key, without making readers wait behind it. Responses
include `stale_seconds`, the nonnegative age since that snapshot completed;
refresh failures retain the previous view and permit a later retry. A parameter
combination with no completed view waits for its own initial computation; it
never borrows another window or presentation variant. This is a delivery cache,
not fresh admission evidence, and does not change the Desktop request timeout.

The mount lifecycle refreshes only the default `(6, 6h, false)` REST key every
60 seconds after completion, even when no clients request it. This avoids a cold
first GET after five minutes of inactivity. It shares exact-key single-flight
serialization with request-driven refreshes; other parameter keys remain lazy.
The existing 120-second validity ceiling is unchanged: stalled or failing refreshes
still produce `warming` after expiry, never an arbitrarily old lane payload.
Shutdown stops future refreshes and retains the lifecycle handle until any active
computation actually drains. Tests: `test_rest_idle_warm.py` exercises idle delivery
through the REST router, blocked refresh expiry/close, retries and default-key bounds.

## Reversible load mitigation

`HERMES_LANES_CHANGE_ONLY` defaults on: native file notifications admit source
reads, semantic board revisions (not WAL stamps or heartbeat events) admit
projection, and starts remain at least three seconds apart with a trailing run.
Set it to `0` to restore timer cursor reads and the one-second projection floor.
`HERMES_LANES_IDENTITY_STAT_CACHE` defaults on: unchanged dev/inode/size/mtime/ctime
reuse a content digest, while changed/missing/racing files still fail identity.
Set it to `0` to rehash every checked file on each identity check.

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
identity), failing closed with 1008. Native notifications admit source reads; idle
0.25-second stream wakes do not query boards. Semantic activity revisions exclude
heartbeat and claim clocks, so WAL write stamps alone never request projection.
Older/non-Linux cores use filesystem metadata admission instead. Changes during
or just after a projection keep one trailing run after a minimum 3-second
start-to-start gap; new events do not reset that deadline. Counts
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
with at most one pending revision per hours/window pair (16 pairs) and 128 viewers.
Collapsed and expanded viewers share that source job: the hub runs the existing pure
presentation projection twice on its one snapshot/raw result and fans out the exact
requested variant. Opposite variants no longer each consume a global burst gap.
One completed successful result may satisfy a same-revision late joiner within the
existing gap, after another code-identity check on the supervisor thread. This never
extends the gap deadline; it is discarded on expiry or replacement, and fallback
results are never retained. Generation AND cursor digest must match; no mutable
SQLite evidence/history survives the job.

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

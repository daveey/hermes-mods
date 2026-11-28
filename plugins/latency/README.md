# Latency Ledger backend

Seventeen metric definitions for Using Hermes, Cards & workers and Ship. The
Desktop panel is separate. `span(metric, **identifier_attrs)` uses monotonic
elapsed time and appends a private bounded JSONL journal. No bodies, tool
arguments, queries or credentials are accepted in client spans.

## Setup

Install/link and enable `latency` with the normal Hermes plugin commands. The
long-lived serve/gateway/dashboard process starts a minute sampler; a root-local
flock permits only one collector. The dashboard router has a lifespan that starts
and stops its sampler. API requests never perform source collection. Shutdown
signals the owned thread; an ongoing source operation finishes before releasing
its cross-process lock. No model calls or paid API calls are made.

Repository names and deployment checkout paths are **local configuration**, not
public defaults. Create `$HERMES_HOME/latency/sources.json`, for example:

```json
{
  "gateway": "gateway",
  "profile": "default",
  "boards": [{"path": "/path/to/kanban.db", "gateway": "gateway", "profile": "default", "board": "work"}],
  "github": {
    "core": {"repo": "example/agent", "checkout": "/path/to/agent", "deploy_kind": "core"},
    "mods": {"repo": "example/mods", "checkout": "/path/to/mods", "deploy_kind": "mods"},
    "client": {"repo": "example/client"}
  }
}
```

Absent `boards` means this home's `kanban.db` only (not fixture boards or every
profile). Symlink aliases are deduplicated. GitHub needs the host's normal guarded
`gh api` authentication. API errors retain the cursor; no tokens are persisted.
Sources are read-only Python SQLite URI connections (`mode=ro`, `query_only`).
If the host SQLite build cannot open a WAL source read-only, collection reports
an error rather than silently changing the mandated read mode.

Slow samples in both series and Ledger responses carry a server-built `link`
only when their owner is known. Sessions use `{kind: "session", id, gateway,
profile}`; cards use `{kind: "card", id, gateway, profile, board}`. Desktop must
resolve `gateway` through its connection registry, never the active connection.
Span attributes may include safe `profile` and `board` identifiers (OTLP:
`hermes.profile` and `hermes.board`); paths, URLs and arbitrary text remain rejected.
The state collector preserves the explicitly configured gateway/profile. Board
entries accept legacy path strings or objects as above; only this home's own
`kanban.db` may inherit gateway/profile. External boards require explicit owners,
and board names are never inferred from filenames. Old samples with missing owner
identifiers remain readable but intentionally have no navigation link.
GitHub samples use the metric's `github.core`, `github.mods` or `github.client`
source alias and the corresponding configured repository to construct
`{kind: "pr", url}` or `{kind: "run", url}` links; no repository is hardcoded.

The shipped workflow selectors and initial targets are in `metrics.yaml`. Local
definitions/overrides live in `latency/metrics.local.yaml` under a `metrics:` list.
Local definitions win by ID. Changing a source's definition fingerprint starts a
30-day backfill next cycle without duplicating already-ingested observations.
An added board definition can use `source: kanban`, `start: decision_requested`,
`end: unblocked`, `kind: derived`, `unit: ms` and the normal registry fields.
Request journals support `source: request-stages`, stage names in `start`/`end`
and an optional exact sanitized `path`; spans use `kind: span`.
Only defined request-stage metrics scan the request journals: the 17 shipped rows
are transcript/tool, board and ship proxies, not endpoint request times.

## Add from chat

`latency_watch(describe="decision_requested -> unblocked")` proposes an inert
`::latency-watch` transcript card. A complete definition is also accepted.
The Desktop card lists start, end, p90 target and history; **Change target** edits
the proposed millisecond target. **Add** sends `POST /v1/watch` with
`{definition: {...}}` to the same explicitly routed hub/default profile as Ledger.
It never writes a local chat machine's registry by accident. Standard dashboard
authentication applies; malformed/oversized requests are 400, uncertain Add is
503 with a retry instruction. Rendering/streaming a proposal makes no request.

The endpoint validates a bounded definition, locks the local registry and
atomically adds it to `$HERMES_HOME/latency/metrics.local.yaml`. The file remains
YAML 1.2 (JSON representation); existing values are preserved, comments are not.
Repeated Add is a no-op; a changed definition/target under an existing id and
any attempt to replace a shipped metric are refused. Remove a watch with a
reviewed local registry edit if no longer needed. No historical cache is deleted.

New derived definitions change the existing source fingerprint, so the next
minute cycle backfills 30 days from real sources. The chat parser recognizes
three board pairs; complete definitions also support state turns/tool results.
Unknown projections must use `kind: span, source: spans`; arbitrary shell,
SQL, request bodies and extra definition fields are not accepted.

Span Add files one idempotent instrumentation card using the supported core
creation/publication API and stores its qualified reference on the definition.
The source config can set `instrumentation_board` and `instrumentation_assignee`
(default current board and the existing `default` profile); an unknown profile
or board refuses Add. Creation readback precedes registry publication. A crash
between card creation and file replacement adopts the same card on exact retry,
not a new card. Failed creation leaves the definition unadded. Its body contains
the metric and exact events; normal review/CI/landing remain required.

Until real samples arrive, the panel shows `collecting from today`, then the
Add date in the panel's America/Los_Angeles timezone. It never invents zeroes or
history. Data outside the selected range is not called uninstrumented. The
proposal result and expanded panel row link the instrumentation card. Because
these references are created on Ledger's hub, the button resolves that same
configured hub transport, not the currently focused chat connection.

## Sources and limitations

* Interactive turn/tool history is Desktop-source only. Earliest message UID
  deduplication and any-user-row boundaries match the research method. Final
  output requires nonempty text and `stop`/`end_turn`; interrupted turns are
  censored, not zero. Changed sessions are revisited for late results.
* Board queue = first created event to first claimed event, including ineligible
  time. Completion = first created event to last completed event, in event-ID
  order. Task-row timestamps are not substitutes; missing event pairs stay absent.
  Neither is useful-work CPU.
  Decision pairs match the latest request preceding an unblock, once per episode.
* Actions runs are sliced daily and saturated slices subdivided until below the
  1000-result cap. Only completed success/failure runs count. Duration = updated
  minus run-start (falling back to creation); chart day = that start. Reruns revise
  one logical run ID, not another observation. Incomplete runs are retried by ID.
  One older creation-day is rescanned each cycle to discover reruns of completed
  runs (a complete 30-day sweep takes at most 30 successful remote cycles).
  GitHub runs/PRs refresh every
  ten minutes; transient 5xx failures retry with bounded exponential backoff.
  Failed scans also wait ten minutes; deleted pending runs (HTTP 404) are retired,
  while other failures retain the cursor. Lock contenders retry every minute so
  a slow prior reload generation cannot leave collection permanently stopped.
* PRs are paginated in merged/updated order, including all merged PRs in the
  backfill window. PR history day = merge day. Core activation requires an exact,
  unambiguous first-parent commit prefix from successful activation log markers;
  mods deployment uses checkout HEAD reflog ancestry. A disk checkout receipt
  is not proof every running process reloaded. Missing joins stay absent.
  Legacy core markers have no UTC offset: local parsing is ambiguous during the
  DST fall-back hour (up to one hour); source logs cannot recover that missing bit.
* Default ranges contain whole hourly buckets intersecting the interval. Daily
  bins use America/Los_Angeles, including DST, to match the research. Boundary
  hours/days are partial-window comparisons, not exact arbitrary-second queries.
* Log histogram upper-edge quantiles have at most 0.5% bucket approximation error
  before reference rounding. Histograms and dedup IDs retain 90 days; only the
  20 slowest raw samples per metric/UTC day are exposed. Mutable board/CI
  observations also retain minimal contribution records for 90 days so revisions
  can retract old buckets and recover the correct slowest twenty. Slowest results
  carry `attrs.session/card/pr/run` for native navigation; PR/run repository is
  identified by the metric's configured source alias.
* Source cursors and histograms commit transactionally. No full message text,
  PR title/body or raw tool payload is stored. Journals rotate globally across
  three 4 MiB files and expire after seven days. Best-effort local telemetry
  failures cannot mask the timed operation's exception.
* The event-semantics-v2 migration uses fresh source cursors for a real 30-day
  backfill. On the first successful projection, a single transaction clears only
  legacy board/CI metric aggregates, writes corrected contributions and its
  marker/cursor. Existing spans, state/tool, PR/deployment history and their
  identities remain untouched. No research/probe data is imported. Migration
  discards older affected aggregates whose raw contributions were not retained;
  corrected observations then accumulate up to the normal 90-day retention.
  All collection, including manual cycles, must hold `collector.lock`.
  Reconciliation verifies the complete per-metric histogram against retained
  contributions and repairs mismatches transactionally, including old-only
  hours and unchanged replay. This is linear in retained contributions and only
  rewrites histogram rows on a mismatch. Pruning keeps a partial boundary
  histogram hour while contributions expire at exact timestamps, so a boundary
  mismatch can legitimately trigger a full repair each cycle (linear scan and
  rewrite, not a constant-time incremental path). A pre-migration process can temporarily
  append stale values after winning the lock; the next corrected source cycle
  removes them, even when the legacy attempt has no corrected counterpart.
  Reload all collector processes normally before reporting rollout complete.
  Recent events on cards created 30–90 days ago can revise their retained
  last-completed duration; a mere archive/comment does not remove the sample.
* Optional request-stage joins keep at most 10,000 recent request IDs for one
  hour; excess in-flight requests can be lost. Retired definition cursors remain
  in this small local metadata table. Neither limit changes the shipped 17 rows,
  which do not use request-stage joins.

## Accepted decision → committed unblock spans

Local `kind: span, source: spans` watches with `start: decision.submit` and
`end: unblocked` use the durable canonical decision operation's `recordedAt`
(accepted submission, not the earlier UI click). The minute collector joins its
operation/request/session identity through the server-owned `kanban_projections`
binding to an **unblocked** event's `user_steps_projection.operationId`, on the
same gateway and exact board/card. Partial answers, archive/release receipts,
unrelated unblocks and remote operations without local canonical evidence do
not produce samples. Missing joins are retried on later cycles for 30 days.

The first collector activation establishes a durable per-watch/per-board floor;
no decisions predating it are imported as instrumentation history. Replay is
idempotent across restarts. Board event timestamps have whole-second precision:
both endpoints are quantized to seconds and spans carry
`phase: seconds_resolution` (duration uncertainty less than one second;
same-second operations are zero). This is not subsecond UI latency. The existing
identifier-only span validator creates stable OTLP context; only safe card,
session, board, profile and phase identifiers enter the aggregate/slow samples.
Source JSON is projected to identifier/timestamp leaves in read-only SQLite;
prompts, answers, frozen requests and credentials are not copied. HTTP admission
limits are unchanged; internal durable-source replay may be older than one hour.
External board entries require an explicit board name, and only this home's
canonical action store is read. Definitions remain local, not shipped defaults.

`tests/test_decisions.py` exercises the real sampler, SQLite sources, series and
Ledger HTTP view, OTLP replay, restart deduplication, privacy and negative joins.
Live deployment is verified separately; fixture observations are never imported
into a production Ledger.

## API v1

The standard dashboard authentication applies to both routes.

`GET /api/plugins/latency/v1/series?range=24h|7d|30d|90d[&metric=<id>]`
returns `{api:1,range,at,timezone,bucket_seconds,quantile_relative_error,metrics,
collection}`. Each metric carries its registry fields plus `measured_since`,
`n,p50,p90,p99`, `series:[{day,n,p50,p90,p99}]`, `hours:[{hour,...}]`, and
`slowest:[{id,metric,start,dur_ms,host,gateway,attrs}]`. All numeric durations and
targets are **milliseconds**. Empty percentiles are **null**, never zero.
The service caches reads for 15 seconds, single-flight; POST invalidates the cache.
`collection` reports the last collection time/counts/error classes, not raw errors.

`POST /api/plugins/latency/v1/spans` accepts `{spans:[...]}` (1–200 events,
256 KiB max). Event shape:

```json
{"id":"optional-stable-event-id","metric":"ui.session_switch","start":1795300000.12,"dur_ms":842,"host":"desktop","gateway":"gateway","attrs":{"session":"session-id","phase":"transcript_painted"}}
```

Only registered span metrics and safe identifier attrs (`session/card/pr/run/phase`)
are allowed. Unknown fields, malformed batches and events older than one hour
return 400 before journal writes. Missing IDs use a canonical-content digest;
retries are idempotent. The journal is durable before the response; if aggregation
fails, the minute collector can replay it. The Desktop sender owns offline
buffering/drop after one hour. This backend does not manufacture historical UI
samples: session-switch and new-session-ready stay null until instrumented.

## Grafana-ready transport

All existing legacy span clients remain supported. `POST /v1/spans` also accepts
OpenTelemetry spans under the same `{spans:[...]}` envelope:

```json
{"trace_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","span_id":"bbbbbbbbbbbbbbbb","name":"ui.session_switch","start_time_unix_nano":"1795300000120000000","end_time_unix_nano":"1795300000962000000","attributes":{"host.name":"desktop","service.name":"hermes-desktop","hermes.session_id":"session-id"}}
```

Timestamps are decimal strings (uint64 nanoseconds, no floating-point ID/time
loss). Trace/span IDs are nonzero lowercase 32/16 hex; `parent_span_id` is
optional. Permitted attributes: `host.name`, `service.name`, `hermes.gateway`,
`hermes.session_id`, `hermes.card_id`, `hermes.pr`, `hermes.run_id`, `hermes.phase`,
`hermes.profile`, `hermes.board` and `hermes.event_id`.
Only short safe identifier strings are accepted; unknown fields are rejected.

`POST /api/plugins/latency/v1/otlp/traces` accepts OTLP/HTTP JSON
`resourceSpans[].scopeSpans[].spans[]`, including resource-level string attrs.
It shares the same limits, validation, durable journal and aggregation path.
Sample IDs are `trace_id:span_id` for OTel clients, preserved across both doors.
Legacy IDs stay stable; missing trace IDs derive deterministically from event ID.
Export carries the admitted ID as `hermes.event_id` so legacy → OTLP → ingestion
preserves that same deduplication key, including subsequent legacy retries.
This field passes the ordinary safe event-identifier validation, not a free-text
attribute; native OTel clients without it continue using `trace_id:span_id`.
`latency.span(..., trace_id=..., parent_span_id=...)` yields the joinable context
and preserves it in the journal and slow samples.

`GET /api/plugins/latency/v1/metrics` returns Prometheus text exposition:
`hermes_latency_seconds_bucket{metric,host,le}`, `_sum`, `_count`, and
`hermes_latency_p90_seconds{metric,range="24h"}`. These reconstruct retained
rolling **90-day** histograms, not process-lifetime counters; retention and source
reconciliation can decrease them. `_sum` reconstructs bucket upper edges (at
most 0.5% error), because old histograms do not retain exact sums. Use the p90
gauge for dashboards, not `rate()` on this rolling projection. Milliseconds are
converted to seconds. Only metrics with samples are exposed.

### Optional exporter — off by default

The only exporter setting is `latency.otlp_export_url`, default empty. Configure
it using `hermes config set latency.otlp_export_url <collector-url>` only when
you intend to send identifiers off-host. Empty means no outbound request.
The configured HTTP(S) endpoint receives OTLP JSON via a background queue of at
most 32 batches (256 KiB each), a 2-second timeout and **no retries**. Overflow
and transport, serialization or thread-start failures drop export, never ingestion;
cache invalidation precedes export and redirects are not followed.
No exporter credentials, URL query, prompts or response bodies are logged.
This deliberately adds no OTel SDK dependency.

`GET /v1/ledger?range=24h|7d|30d|90d` is the Desktop view model: server-owned
ordered groups/statuses plus `chip.over_target_count`, eight calendar days of
first-reply history and five worst-by-target metrics. Authenticated
`/v1/events?range=...` pushes changed views and reuses canonical dashboard WS
authentication, failing closed when it is unavailable.

## Tests

Run on a pool worker: `scripts/test-all -k latency` (includes repo-guards), then
`--no-core --exact -k plugins/latency` for public CI parity. Tests exercise real
fixture SQLite collectors, late completions, definition-triggered backfill,
replay, runtime boundaries, Actions saturation splitting/5xx retry, ancestry,
HTTP malformed admission, empty metrics, cache, histogram bounds, zero, DST,
retention, concurrent spans/SQLite, rotation and exception preservation.
Live deployment, full 30-day GitHub backfill and research comparison are separate
acceptance gates; passing unit tests alone does not establish them.

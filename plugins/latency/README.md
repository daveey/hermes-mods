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
  "boards": ["/path/to/kanban.db"],
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
  rewrites histogram rows on a mismatch. A pre-migration process can temporarily
  append stale values after winning the lock; the next corrected source cycle
  removes them, even when the legacy attempt has no corrected counterpart.
  Reload all collector processes normally before reporting rollout complete.
  Recent events on cards created 30–90 days ago can revise their retained
  last-completed duration; a mere archive/comment does not remove the sample.
* Optional request-stage joins keep at most 10,000 recent request IDs for one
  hour; excess in-flight requests can be lost. Retired definition cursors remain
  in this small local metadata table. Neither limit changes the shipped 17 rows,
  which do not use request-stage joins.

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

## Tests

Run on a pool worker: `scripts/test-all -k latency` (includes repo-guards), then
`--no-core --exact -k plugins/latency` for public CI parity. Tests exercise real
fixture SQLite collectors, late completions, definition-triggered backfill,
replay, runtime boundaries, Actions saturation splitting/5xx retry, ancestry,
HTTP malformed admission, empty metrics, cache, histogram bounds, zero, DST,
retention, concurrent spans/SQLite, rotation and exception preservation.
Live deployment, full 30-day GitHub backfill and research comparison are separate
acceptance gates; passing unit tests alone does not establish them.

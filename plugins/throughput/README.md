# throughput

Per-minute tokens, sessions and cards for each gateway, shown by the Desktop throughput chip and panel
(`desktop-plugins/throughput`).

## Data

| Series | Source | Attributed to |
|---|---|---|
| `tokens_io` (input + output) / `tokens_cache` (cache read + write) | this gateway: diff of every session's totals in `state.db` + `profiles/*/state.db`, once a minute. Other gateways: deltas of the Board machine registry counters `tokens_io_total` / `tokens_cache_total` | the gateway |
| `sess_in` / `sess_out` | this gateway: `sessions.started_at` / `ended_at` (backfilled over the retention window). Others: `sessions_started_total` / `sessions_ended_total` | the gateway |
| `cards_in` / `cards_out` | Board replicas `kanban/sync-shadow/*.db`: `tasks.created_at`, and `completed_at` with status done/archived | created: `tasks.created_on`. Done: the holder of the card's last `claimed` event (`holder`, or `lock` `hermes-worker-4:<pid>` → `worker-4`); `hwp-*`/`rec-*` → `ephemeral`; `boot-<uuid>` (macOS claims) → the machine whose cards that boot id claimed (≥ 80 % agreement), else `unattributed` |

Rules: a negative counter delta (reset, deleted rows) counts 0. A minute without a sample is stored as missing
(NULL), never 0; a gap of up to 5 minutes is spread evenly over the minutes it covers. A gateway that never
reported a metric has no rows, and the API returns `null` for it. Retention: 8 days, in
`<hermes root>/cache/throughput/series.sqlite`.

### Model series (`by=model`)

History starts at deployment: model rows are never backfilled, and the API never re-attributes earlier scalar
history to `unreported`. Stored per gateway as `tokens_io:<model>` / `tokens_cache:<model>` plus
`tokens_io:unreported` / `tokens_cache:unreported`, which is written for every sampled minute (0 included) and marks
it as sampled. Decisions:

- **Scalar is the authority.** Every sampled minute: sum of model rows + `unreported` == scalar delta. Models short
  of the scalar leave the rest as `unreported`; models above it (cumulative per-model churn: deleting a session
  lowers a model counter by more than the scalar, an aux-call ledger row `sessions` never counted) are scaled down
  proportionally to the scalar, `unreported` 0 (`store.apportion`).
- **Registry**: `metrics.tokens_by_model = {model: [io_cumulative, cache_cumulative]}`. A missing/non-map value
  leaves the scalar delta in `unreported`; malformed entries or invalid numeric components are skipped independently.
  At most the first 64 valid names per token kind are retained; omitted deltas become `unreported`. A model baseline
  is used only if taken at the scalar's previous baseline minute, so a new model, one that came back after being
  absent, and the first reading after a map was missing are baselines only (their tokens stay `unreported`, no spike).
  On first deployment an existing persisted scalar baseline still supplies scalar history, but no model rows are
  written until after the first model-aware reading. A negative delta (reset/deleted rows) is 0; gap spreading and
  stale-report rules are the scalar's.
- **Local**: `session_model_usage` (all rows of a session/model summed over provider/mode/task) snapshot per
  `(db, session, model)`, diffed like the scalar (new session in full, vanished 0, lower 0). A db without the table,
  with an unreadable table, a new profile, or one that just became model-capable is a baseline only. After an
  unreadable model sample, the first successful read also establishes a fresh model baseline: both intervals stay
  `unreported`, rather than assigning an outage's model growth to a later sampled interval. Scalar snapshots retain
  unreadable db totals. If no scalar db is readable, no interval is sampled; normal short-gap spreading applies.
  Scalar and model reads use separate connections, so concurrent writes can skew the model mix; proportional
  normalization enforces total parity, not exact per-model attribution under such races or auxiliary ledger skew.
- **Names**: names are trimmed/truncated to 128 characters (blank becomes `unknown`); colliding normalized registry
  names are summed before differencing. Reserved wire names `other`/`unreported` remain reserved buckets, never
  renamed or ranked as real models. A literal model with one of these reserved names is indistinguishable here.
- **API** `?by=model` (default `gateway`, anything else 400): same payload plus `by: "model"` and `models`: the top 8
  real models by combined io+cache window total (ties by name; reserved names never compete), then `other` (the rest,
  only if any) and `unreported`; `[]` when no model rows exist in the window. `series`/`sampled`/`recent` of
  `tokens_io`/`tokens_cache` are keyed by model, aggregated across gateways; sessions, cards and `gateways` are
  unchanged. `sampled` is the union of minutes any gateway sampled (wall time, not machine-minutes); inside one, a
  model without a row is 0, outside None. The 15 s cache is keyed by (range, by).

Until every gateway's registry metrics carry the token/session counters, tokens and sessions cover the local
gateway only; cards cover every gateway.

## Throttle and runway

`GET /v1/throttle` returns `{percent, reference_per_s, cap_per_s, rate_per_s, holding, scope, pools, now}`;
`PUT /v1/throttle` with `{"percent": 0..100}` sets the level; any other `percent` is a 400 and a body that is not
a JSON object a 422, and neither changes anything.

- **Level**: 0 = no cap, 100 = a cap of zero, between = `(1 - percent/100) x reference_per_s`, where the reference is
  the peak fleet in+out token rate over the last 7 days in 15-minute buckets. Without any sampled minute there is no
  reference and a level between 1 and 99 caps nothing.
- **Scope**: the level is a file in this Hermes root (`cache/throughput/throttle.json`). It holds model calls made by
  Hermes processes on that root only (`scope` names the gateway). It is not sent to other machines: the Board's
  machine registry rejects fields it does not know, so a fleet-wide level needs a Board + core change.
- **Enforcement** (`throttle.py`): an `llm_execution` middleware. While the fleet rate (mean of the sampled minutes
  among the last three) is above the cap, a main-loop model call waits and re-checks every 5 s; at 100 % every such call waits until
  the level is lowered. Auxiliary calls that bypass core's execution middleware (titles, compression ...) are not
  held. The 7-day reference is cached for 10 minutes per process. Rates are sampled once a minute, so the control lags by about a minute and duty-cycles around
  the cap. A wait is not interruptible and records no agent activity: a Kanban worker held for long can be reclaimed as
  stalled, and a gateway turn held past `agent.gateway_timeout` is abandoned by the gateway's idle watchdog. Fail open:
  an unreadable level, a missing series db or a failed read never holds a call. A core without `register_middleware`
  shows the slider but enforces nothing. Untested: the middleware inside a live core agent loop (the tests call the
  registered callback directly).
- **Runway** (`runway.py`): per subscription pool, from the usage samples core's Kanban pacing governor keeps in
  `llm_pacing.json` (no usage request is made): `remaining_pct` (sum of `100 - used` over the accounts' weekly
  windows), `burn_pct_per_h` (each account's slope over the last 10-60 minutes), `runway_s = remaining / burn`
  (`null` when nothing burns) and `next_reset_at` (the earliest weekly reset). Resets are not folded into the
  runway. Account names are not returned. A host without the governor's file reports no pools, and a provider
  whose newest sample is over 30 minutes old is left out.

The Desktop panel shows the slider under Tokens, the cap as a red line on the Tokens chart (hidden with `+cache` or
a legend filter, which the in+out fleet cap cannot be compared with) and one runway line per pool, rescaled by
`cap / rate` when the cap is under the measured rate.

## Sampler

`register()` starts a sampler thread in long-lived server processes only (`hermes gateway`, `hermes serve`,
`hermes dashboard`; never a CLI chat or a Kanban worker). A non-blocking `flock` on
`cache/throughput/sampler.lock` keeps one sampler per Hermes root; the others wait and take over if it exits.
The REST API also starts one if the process has none.

Token sampling uses message/session watermarks and refreshes active sessions; deletions,
rewinds and periodic full reads retain the full-snapshot fallback. Snapshot SELECTs transfer
numeric/text rows through SQLite JSON aggregation and one Python decode, avoiding per-row
GIL handoffs when other threads read SQLite. SQLite builds without JSON functions and
legacy BLOB values use the original row reader. This does not change read-only connections,
watermarks, token normalization or fallback rules. Full reads still allocate O(state) memory,
including a temporary JSON string; incremental query batches remain bounded by the existing
ID chunk size.

`test_sample_local_tokens_replay_budget` measures sampler **thread CPU** with a 0.3%-of-one-core
budget per 60-second tick. Its busy stress replay includes 23k sessions, about 1.28M messages,
31.7k model rows, six sampler initializations over 46 ticks, and four concurrent SQLite readers.
It models observed busy-reader concurrency and reload frequency, not an exact production trace
or all profile files. Relative speedup compares with the legacy full-row reader; the absolute
budget is unchanged. Live process accounting remains the deployment acceptance check.

## API

`GET /api/plugins/throughput/v1/series?range=1h|6h|24h|7d[&by=gateway|model]` (buckets 1/5/15/60 min):

```json
{"now": 1800000000, "since": 1799996400, "bucket_s": 60, "range": "1h", "local": "hub",
 "gateways": ["hub", "worker-1", "ephemeral", "unattributed"],
 "series": {"tokens_io": {"hub": [600, 580, null], "worker-1": null}, "cards_out": {"hub": [1, 0, 2]}},
 "recent": {"tokens_io": {"hub": [600, 610]}}, "recent_minutes": 15}
```

`series` holds counts per bucket; `sampled` (tokens and sessions only) holds the sampled minutes per bucket, so
the Desktop divides a partly sampled bucket's count by the minutes actually sampled (cards are complete, so by
the whole bucket) and converts to /s, /m, /h; `recent` holds the last 15 per-minute
counts for the "rate now" numbers. Built at most once per range per 15 s (single flight), in a worker thread.

## Tests

```bash
scripts/test-all --no-core --exact -k plugins/throughput
HERMES_DESKTOP_PACKAGE=<dir with react, react-dom, jsdom, @tanstack/react-query, radix-ui>/package.json \
  node --test desktop-plugins/throughput/tests/*.test.mjs
```

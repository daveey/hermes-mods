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

`GET /v1/throttle` returns `{percent, reference_per_s, cap_per_s, rate_per_s, holding, set_at, set_by, scope, fleet,
gateways, pools, now}`; `PUT /v1/throttle` with `{"percent": 0..100}` sets the level; any other `percent` is a 400
and a body that is not a JSON object a 422, and neither changes anything.

- **Level**: 0 = no cap, 100 = a cap of zero, between = `(1 - percent/100) x reference_per_s`, where the reference is
  the peak fleet in+out token rate over the last 24 hours in 15-minute buckets, measured on the gateway that takes
  the setting. The cap is stored in tokens/s with the level, so it stays where it was set while the peak moves, and
  every gateway enforces the same number. Without any sampled minute there is no reference and a level between 1
  and 99 caps nothing.
- **Scope: the fleet.** The level is written to `cache/throughput/throttle.json` on the gateway whose panel set it.
  The hermes-hub link (client 1.9.51 or newer) publishes that file in the gateway's `gateway/info` record and every
  gateway keeps a replica of the others' records (`~/.local/state/hermes-hub/hub.db`), so each gateway reads every
  published level and obeys the newest `set_at` (`set_by` names the gateway that set it; `null` = this one). Stamps are stored and compared at millisecond precision, the precision the link publishes. A level
  stamped more than 5 minutes ahead of this gateway's clock is ignored until that time comes (gateways are
  expected to keep NTP time; a level set under a badly wrong clock can outrank later ones). It arrives within the
  link's sync periods: the setter's next full collect (up to 3 minutes) plus the receiver's next pull (about a
  minute). Each gateway acknowledges the level it obeys in `throttle-seen.json` (once a minute, from
  the sampler) and the link publishes that too: `gateways` lists this gateway and every gateway whose link record
  is under 30 minutes old, with `seen` true once it acknowledged the current `set_at`. `fleet` is false on a host
  without the link's replica: the level then applies to `scope`, this gateway, alone. A gateway that does not run
  this plugin is not throttled and shows `seen: false`. A gateway with the plugin but an older link client obeys
  published levels, but cannot publish a level set on it or its acknowledgement (`seen: false`). Only a gateway
  whose server process runs the sampler acknowledges. A gateway that cannot read its replica obeys its own
  stored level until it can; the fleet's level is the setter's `cache/throughput/throttle.json`, so clearing that
  file drops the setting.
- **Enforcement** (`throttle.py`): an `llm_execution` middleware that paces model calls. Each gateway takes a share
  of the cap: a quarter split evenly between the gateways that sampled tokens in the last 15 minutes, the rest in
  proportion to what each used (every gateway computes the same shares from the same series, so they sum to 1). It then spaces
  its calls `tokens per call / (share x cap)` seconds apart, where tokens per call is its own measured in+out
  tokens per model call over those 15 minutes (20k until it has measured any; every call is counted, throttled or
  not). Calls on one gateway queue for slots in `cache/throughput/throttle-pace.json` under a file lock, across
  all its Hermes processes: an idle gateway's first call starts at once, the rest follow one interval apart, and
  unused time is not saved up. The spacing is capped at 10 minutes, so a gateway with a sliver of the cap still
  makes progress. At 100 % (or a cap of zero) every call waits until the level is lowered; a waiting call re-reads
  the level every 5 s. Shares and tokens per call are re-read every 30 s per process. This replaced a rule that
  held every call while the fleet rate was over the cap and released them all when it dipped under, which
  overshot in bursts (2026-11-17: cap 76k tokens/min, fleet average 116k, peaks of 794k). The fleet's rate now
  follows the cap as closely as the tokens-per-call estimate holds; a gateway whose calls suddenly grow overshoots
  until its 15-minute average catches up. `holding` in the payload still means "the measured fleet rate is above
  the cap right now".
- **Card starts** follow the same cap. On a core with the `kanban_start_gate` hook the dispatcher asks the plugin
  before each claim, and a card stays ready (guard reason `start_gate:token_throttle`) while the fleet's measured
  rate is over the cap, or the level is 100 %. The answer is cached for 15 s and fails open. Pacing alone only
  slows the calls of running cards; without the gate the dispatcher keeps starting cards whose calls then queue,
  filling worker slots with runs that wait. A core without the hook starts cards as before.
- **A held turn stays alive.** Core gives middleware no activity API, so the gate finds the agent behind the call it
  wraps (the closure of core's `_perform_api_call`) and stamps its activity clock every poll, as core's own offline
  wait does: the gateway's idle watchdog and the turn liveness watchdog see a waiting turn, and a Kanban card's
  claim is renewed. A stop request ends the wait at the next poll and hands the call to core's own interrupt
  handling. On a core where that closure looks different the wait runs without those reports (a card still renews
  its claim through `tools.kanban_tools`), and a long hold can then be reaped as stalled.
- **Auxiliary calls** (titles, compression, vision ...) do not pass the execution middleware. In card runs and
  other short-lived agent processes they wait in the `pre_auxiliary_call` hook. Core bounds a hook callback at
  600 s, so a held auxiliary call goes ahead after 540 s at the latest, and core runs one bounded callback per turn
  at a time, so parallel auxiliary calls of one turn pass while one waits: at 100 % auxiliary traffic there is
  slowed, not stopped. In long-lived server processes (gateway, Desktop serve, dashboard) auxiliary calls are not
  held at all: core fires the hook synchronously for async callers, and a hold would freeze the server's event
  loop, including the route that lowers the level. The dispatcher's admission judge (`hub_admission`) is never
  held, and neither is the max-iterations summary, which calls the provider directly.
- **Fail open**: an unreadable level, a missing series db or replica, or a failed read never holds a call. A core
  without `register_middleware` shows the slider but enforces nothing.
- **Not covered by tests**: the middleware and the hook inside a live core agent loop (the tests call the registered
  callbacks with a stand-in for core's call closure), and the hub link round trip between two real hosts.
- **Slots** (`slots.py`): `slots.gateways[name]` in the same payload, for the Runners panel: `share` of the cap,
  `cap_per_s` (share x cap), `rate_per_s` and `avg_busy` over the 15-minute share window,
  `tokens_per_slot_per_s` (tokens / busy slot-seconds from the Board replicas' card runs; `cost_source`
  `measured`, or `fleet_median` under five busy slot-minutes), `by_throttle` = floor(cap share / cost per slot)
  and `allowed` (the same; null = no limit). `slots.pacing.held_starts` counts the cards whose start the pacing
  governor held in the last 5 minutes (`llm_paced` events in the replicas); pacing is not converted to slots,
  because its per-gateway limits are not published. Each pool carries its `pacing_factor`. `slots` is null if it cannot be computed; that never fails
  the route. Fresh snapshots add `observed_at`, `window_since`, `window_until` (Unix seconds), `valid: true`
  and `stale: false`; `pacing` adds its own `window_since`/`window_until`. The observation is taken at refresh
  start, not publication. At 30 seconds of wall or monotonic age, at a minute-window change, on cold start,
  or after incomplete/failed reads, the response is `slots: null` (never stale limits or fabricated zero holds).
  The current request's cap always recomputes the plan from the measurement.
  All optional replica and usage reads run in one owned subprocess per root, not on GET/PUT's awaited path.
  A two-second deadline cancels/kills and reaps that child; teardown joins its coordinator before reloading.
  Failures back off one second. Plugin unload, server lifespan shutdown and process exit own cleanup.
  Boot attribution uses only a nonblocking fresh Cards snapshot: a missing boot mapping leaves slots unavailable
  until the regular accurate series path supplies one. No background Cards rebuild or empty series substitution.
  Tests: `tests/test_slots.py`, `tests/test_advisory.py`, `tests/test_api.py` (real blocked Cards rebuild,
  SQLite-lock GET/PUT bound, cancellation and lifecycle). **Not tested**: live macOS activation/soak.
- **Runway** (`runway.py`): per subscription pool, from the usage samples core's Kanban pacing governor keeps in
  `llm_pacing.json` (no usage request is made): `remaining_pct` (sum of `100 - used` over the accounts' weekly
  windows), `burn_pct_per_h` (each account's slope over the last 10-60 minutes), `runway_s = remaining / burn`
  (`null` when nothing burns) and `next_reset_at` (the earliest weekly reset). Resets are not folded into the
  runway. Account names are not returned. A host without the governor's file reports no pools, and a provider
  whose newest sample is over 30 minutes old is left out. `pacing` is the governor's own verdict for the pool
  (`open`, `paced`, `held`; `pacing_until` for a hold's known end): with `paced` or `held`, Kanban starts fewer or
  no cards on that pool whatever the throttle level is, and the panel says so on the pool's line.

The Desktop panel shows the slider under Tokens ("applies to all gateways · 3 of 4 have this level"), the cap as a red line on the Tokens chart (hidden with `+cache` or
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

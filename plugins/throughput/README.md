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

`GET /v1/throttle` returns `{percent, reference_per_s, cap_per_s, rate_per_s, holding, set_at, set_by, mode, budget,
user_cap_per_s, pacing, scope, fleet, gateways, pools, slots, slot_gate, now}`; `PUT /v1/throttle` with `{"cap_per_min": N | null}`
sets a person's cap (below), with `{"percent": 0..100}` the slider level; any other `percent` is a 400
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
  not). Calls on one gateway share `cache/throughput/throttle-pace.json` under a file lock, across all its Hermes
  processes: an idle gateway's first call starts at once, the rest follow one interval apart, and unused time is
  not saved up. The spacing is capped at 10 minutes, so a gateway with a sliver of the cap still
  makes progress. At 100 % (or a cap of zero) every call waits until the level is lowered; a waiting call re-reads
  the level every 5 s. Shares and tokens per call are re-read every 30 s per process. This replaced a rule that
  held every call while the fleet rate was over the cap and released them all when it dipped under, which
  overshot in bursts (2026-11-17: cap 76k tokens/min, fleet average 116k, peaks of 794k). The fleet's rate now
  follows the cap as closely as the tokens-per-call estimate holds; a gateway whose calls suddenly grow overshoots
  until its 15-minute average catches up. `holding` in the payload still means "the measured fleet rate is above
  the cap right now".
- **Chats are never throttled; slots are granted at start.** Only Kanban card runs (`HERMES_KANBAN_TASK` or
  `HERMES_KANBAN_WORKER_TASK` set) and cron calls (`platform` `cron`) wait; any other call, an interactive chat,
  is counted for tokens per call and goes ahead at once, at any level, without touching the pace. Auxiliary calls
  follow the same rule, with the parent turn's platform (empty outside a turn, so there only the card variables
  decide). A paced call polls the pace file and is stamped (`last_start`) only when it starts, once one interval
  has passed since the last start. A wait holds no slot: the earlier version booked a slot before waiting, so
  waits that ended early (a stop, the auxiliary 540 s bound, a crash) and every level change left booked slots
  behind, and the queue ratcheted past the 10-minute cap (waits of 763-966 s were logged). Calls count in the
  minute they start. A stored stamp more than one interval ahead is treated as now. During rollout an older
  process sharing the file books its slot after the `next_at` a new process writes on each start, and a new
  process honours an older process's `next_at` only while it is at most one interval ahead.
- **A person's cap** (`write_cap`; `PUT /v1/throttle {"cap_per_min": N | null}`, `hermes throttle set 150|1.5m|0|off` (a plain number is thousands, as in the panel),
  the Runners panel's "Token cap" field): an absolute fleet cap in tokens, stored as a `mode: "manual"` level
  (`cap_per_s`; a cap of 0 is stored at `percent: 100` and any other cap at a percent strictly between 0 and 100,
  so code from before this mode enforces the stored cap too; null = no cap of one's own). Set while a live budget
  level is in force, it carries that budget's `pool_caps` scaled down to the cap (reserve holds stay) and stores
  the percent of the budget it cuts, which an older publisher keeps. It reaches the fleet like any level. Without a budget host every gateway paces at it. The budget-publish host
  records the newest person-set level in `cache/throughput/user-cap.json` (a manual level as is; a slider level,
  or an older panel's slider on the budget level, as its percent of the budget) and writes its budget level with
  `percent` such that the cap is the lower of the person's cap and the budget (`percent_for`), so pool caps scale
  with it and older gateways enforce it unchanged. Its own writes are recognised by `published_set_at`. A budget
  level written elsewhere with the percent it last published (a burst set or ended) is not a person's setting
  (`published_percent`). A burst multiplies the cap in force, the person's cap included: it is a deliberate,
  time-boxed raise; the recorded cap is unchanged when it ends.
  `user_cap_per_s` in the payload is that cap as far as this gateway can tell. Tests: `tests/test_slotgate.py`
  (manual pacing, fold, lower-of, removal, older slider), `tests/test_api.py::test_put_cap_per_min_*` (REST),
  `tests/test_cli.py` (CLI and its registration), Runners `tests/throttle.test.mjs` (panel field).
- **Card starts: the slot gate** (`slotgate.py`, `Gate.start_gate`). Each gateway opens only as many card slots as
  its share of the cap pays for: `allowed = floor(share x cap / tokens per slot)`, at least 1 while the cap is
  above 0, 0 at a cap of 0, no limit without a cap, 2 before the gateway has measured itself. `share` is the same
  share its calls are paced at. Tokens per slot is a running estimate (1-hour half-life, dropped after 6 hours
  without a measurement, `cache/throughput/slot-estimate.json`) of this gateway's tokens over the last 15 minutes
  divided by its busy slot-seconds **minus the seconds its cards' calls were held by the throttle**: the sampler
  stores core's running card count (`slot:busy`) and the held seconds (`slot:wait`, per card and minute at most
  60, counted by `Gate.wait` for card runs only) each minute. Held time is left out because a throttled slot burns
  few tokens; counting it would make slots look cheap and keep open as many as already run. On a core with the
  `kanban_start_gate` hook a card stays ready (guard reason `start_gate:token_slots`) while running cards plus the
  starts let through since the last read (every 15 s, counted per card: core asks about a card in pickup and
  again at the claim) reach `allowed`. Running cards are never stopped. Without a
  slot limit or a card count (no cap, or outside a Hermes runtime) the older rule holds starts
  (`start_gate:token_throttle`) while the fleet's measured rate is over the cap. Any failed read starts the card.
  The payload's `slot_gate` is `{allowed, share, per_slot_per_s, source, estimate_at, cap_per_s, running}`, and
  this gateway's row in `slots.gateways` carries the enforced numbers (`enforced: true`); other gateways' rows
  stay advisory estimates (their held time is not published). Held seconds are written to the pace file once a
  minute per waiting call. **Known gaps**: the token count is the gateway's whole in+out use, so chats and cron on
  a gateway (the hub) make its slots look dearer; a gateway whose slots are held over two thirds of the time
  stops refreshing its estimate (it expires after 6 h). Tests: `tests/test_slotgate.py`. **Not tested**: a live
  dispatcher tick and core's `active_card_runs` on a worker.
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
- **Budget mode** (`mode: "auto"`). On the host whose core config sets `kanban.llm_pacing.budget_publish: true` (the
  hub; read like core's dispatcher reads `kanban.llm_pacing`), the sampler reads core's capacity model (`v2` in
  `<kanban home>/llm_pacing.json`) once a minute and writes the level as `{"mode": "auto", "percent", "cap_per_s":
  v2.global_rate_tokens_per_s, "pool_caps": {provider: rate_tokens_per_s, 0 when at_reserve, null when the rate is
  unknown}, "budget_at": v2.computed_at, "set_at"}`. It keeps the fleet's `percent` (the newest level any gateway
  set, so a slider moved anywhere is folded in on the next pass) and rewrites only when that level is not its own
  budget, when any cap moves by more than 5 %, or 15 minutes after its last write; a missing or malformed `v2` writes
  nothing, and any other host never writes. Its `set_at` is the obeyed level's plus 1 ms, not the clock, so a newer
  slider setting elsewhere still outranks it until it is folded in. The level travels like any other (hub client
  1.9.55 or newer carries `mode`, `budget_at` and `pool_caps`).
  - **Caps**: every budget cap is scaled by the slider, `budget x (1 - percent/100)`: 0 % is the full budget, 100 %
    holds every throttled call. `cap_per_s` in the payload is the scaled global cap; `budget` is `{state, budget_at,
    age_s, budget_per_s, pool_budgets, cap_per_s, pool_caps, governed_rate_per_s, governed_cap_per_s}` (`null` for a
    slider level; `pool_budgets` unscaled, `pool_caps` scaled), `mode` is `auto` or `manual`.
  - **The slider on a budget level** (`PUT /v1/throttle` while the level in force is `auto`) keeps `mode`,
    `cap_per_s`, `pool_caps` and `budget_at` and changes only `percent` and `set_at`, so reserve holds stay in force
    on every gateway; the panel previews the same. On a slider level it writes a slider level, as before.
  - **Calls**: a card or cron call is paced at its own pool's cap (core's middleware context `provider`), with the
    same share and tokens-per-call as above and its own slot in the pace file (`pools.<provider>`). A pool cap of 0
    holds the call; `null` (rate unknown) passes; a provider without a pool cap (or a call naming none) passes,
    ungoverned. Chats pass as always.
  - **Card starts** hold at 100 %, or while the governed pools' fleet rate is over the sum of their scaled caps.
    Governed pools are those whose scaled cap is a positive number; their rate is their providers' in+out tokens/s
    over the last 3 sampled minutes, from the series' per-model rows (`tokens_io:<model>`, every gateway, the
    `by=model` data) attributed to a provider with core's `provider_for_model` (`claude*` anthropic, `gpt-`/`codex`/
    `o1`/`o3`/`o4` openai-codex, `grok` xai-oauth; the same table when core is absent). Tokens on other providers,
    `unreported` and `other` tokens, and pools at reserve or of unknown rate do not count, so an interactive chat
    on an ungoverned provider never holds a start. The series does not tell chats from card runs, so chat tokens on
    a governed provider do count (they spend that pool's quota too). No governed pool, or a sum of 0 (every pool at
    reserve or unknown), never holds a start: reserve holds stay per pool, in the call pacing above and in core's
    own held pools. The global `cap_per_s` (pools of known rate only) is shown, not used to hold starts.
  - **Stale budget**: `budget.state` is `fresh` up to 20 minutes after `budget_at`, `stale` (caps kept) up to 60
    minutes, then `expired`: no global or pool cap from it, every call and start passes (fail open), except at
    100 %. The panel shows `Budget −p% · cap …`, with "budget stale … keeping last caps" or "budget expired … no
    cap".
  - A level without `mode` is the slider level above, unchanged, whatever other fields it has. A `mode: "manual"`
    level is a person's cap (above) or, without `pool_caps`, budget pacing switched off (below).
  - **Exactly one host** may set `budget_publish`. Two publishers each see the other's newer level as "not ours"
    and rewrite on every pass, alternating forever. The pin only says **which host publishes**: it must be true on
    that host for "On" to have any effect, and the hub's config sync re-pins it every pass, so it is not the on/off
    control. The panel's switch (below) is.
  - **Budget pacing switch** (`set_pacing`; `PUT /v1/throttle/pacing {"on": true|false}`; the panel's "Budget
    pacing" On/Off). The hub link carries only `percent, cap_per_s, set_at, mode, budget_at, pool_caps, burst`, so
    the switch is a level like any other and wins fleet-wide by newest `set_at` (stamped like the slider, `max(now,
    set_at in force + 2 ms)`).
    - **Off** writes `{"mode": "manual", "percent", "cap_per_s", "set_at"}`: an explicit manual level **without
      `pool_caps`** (`pacing_off`). While it is the level in force the publisher writes nothing, however long it
      stays (no budget level, no fold into `user-cap.json`). Otherwise it is an ordinary manual level: its cap, no
      pool caps (reserve holds end), the start gate's rate rule, 100 % holds everything. **The cap**: from a live
      budget level, the absolute cap in force is kept (a budget level's percent is a person's cap the publisher folded
      in, relative to the budget; re-deriving it against this gateway's 24-hour reference would loosen or tighten
      it): on the publish host the cap it recorded in `user-cap.json` (including a cap above the budget), elsewhere
      `budget x (1 - percent/100)`, never the burst; stored like a person's cap (percent strictly between 0 and 100,
      so older code enforces it; none at 0 %, 0 at 100 %). From any other level, `cap(percent, reference)` with the
      current percent. A manual level stored without `cap_per_s` (an Off set where no reference was measured yet)
      caps at this gateway's own `(1 - percent/100) x reference`, as a level without `mode` does. The slider on an
      Off level then sets `(1 - percent/100) x reference`, so its first move re-derives the cap from the reference. A burst is dropped when it is written and ignored on it (the burst route answers
      409). The slider on it keeps `mode: "manual"` (no pool caps), so moving the slider never turns pacing back on,
      and a person's cap set while it is off carries no `pool_caps` either and keeps it off.
    - **On** writes `{"percent", "cap_per_s", "set_at"}` without `mode` (the current percent): the publisher folds it
      into a budget level on its next pass, holding back that percent of the budget. It writes only when pacing is
      off; on any other level (already on, or on its way) it writes nothing, so a live budget level never loses its
      pool caps to a stray click. Off when already off writes nothing either.
    - **Why `pool_caps` is the marker**: a person's cap (`write_cap`) is also `mode: "manual"` and must still be
      folded in when pacing is on. It now always carries `pool_caps` (the live budget's scaled pools, or `{}`) unless
      the level in force has pacing off. A legacy level without `mode` keeps today's behaviour (folded).
    - **`pacing`** in the payload is `{state, publisher}` (`pacing_status`, from the levels already read):
      `on` = the level in force is a budget level whose budget has not expired (fresh or stale); `off` = it is an
      explicit manual level without `pool_caps`; `pending` = it is a level the publisher folds in (no `mode`, or a
      person's cap with `pool_caps`) set under 3 minutes ago, or under 10 minutes ago while `budget_seen` (the link
      takes up to about 4 minutes each way, so a fold can take 8-9 minutes to come back); `unavailable` = anything
      else: an expired budget, or a level nothing folded in, i.e. no publisher is running or reaching this gateway.
      `budget_seen` is true when any level read here (its own or a published one) is a budget level with `budget_at`
      in the last 24 hours, expired or not (after a long Off the publisher's last budget level has expired but is
      still there), or an Off set in the last 24 hours (an Off set on the publish host replaces its budget level),
      or this gateway switched pacing On in the last 10 minutes (local `cache/throughput/pacing-on-request.json`,
      never published), so an On after more than a day Off keeps its switch visible while the fold travels.
      The panel hides the switch on `unavailable` with `budget_seen` false: that fleet has no budget publisher. `publisher` is the gateway
      whose unexpired budget level is newest by `budget_at` (then `set_at`) among this gateway's own and every
      published one, or null; a slider or burst set on another gateway copies the budget level there, so that
      gateway can be named until the publisher's next write.
    - **Rollout and mixed versions**:
      - The publisher host must run this version first; an older publisher folds an Off level back in.
      - An older plugin's `write_cap` (a person's cap from an older panel or CLI) set while no live budget level is in
        force writes `mode: "manual"` without `pool_caps`. This version reads that as Off, and it sticks: the
        publisher stops until someone turns pacing On. Update every host that sets caps before relying on the switch.
      - An Off set on a gateway whose hub client is older than 1.9.55 is published without `mode`, so every other
        gateway (and the publisher) reads it as a plain level and folds it back to On. Set Off only on gateways with
        hub client 1.9.55 or newer.
      - Newest `set_at` wins: a slider move, cap or burst set on a gateway that has not yet received an Off (link lag,
        up to a few minutes) is stamped after the level that gateway sees, a budget level, and writes a level the
        publisher folds in again, so it turns pacing back On fleet-wide. Wait until the panel shows Off on the gateway
        you change things from.
      - Other gateways obey an Off level as a manual cap on any version.
    - Tests: `tests/test_throttle_budget.py` (publisher skip and legacy fold, off set on another gateway, round trip,
      slider and person's cap while off, `pacing` states, On after more than an hour Off, Off without a reference,
      Off keeping a person's absolute cap), `tests/test_throttle_burst.py` (burst dropped and ignored),
      `tests/test_api.py::test_pacing_route_switches_budget_pacing_off_and_on` (REST, validation), panel
      `desktop-plugins/throughput/tests/throughput.test.mjs`. **Not tested**: an off level travelling through two
      real hub links.
  - **Without a publisher** (the pin false, or the hub down): the last auto level stays in force (stale after 20
    minutes, expired after 60: then no budget cap, `pacing` `unavailable`) until it expires or the slider moves; 100 %
    still holds every throttled call and card start, expired or not. A slider move keeps a live budget level a budget
    level (above); once the budget has expired, a slider move writes a level without `mode`.
  - **Rollout order**: deploy this plugin fleet-wide and restart the workers (and every long-lived Hermes process)
    before enabling `budget_publish`. An older plugin reads an auto record's `cap_per_s` as an unscaled manual cap
    (and ignores `pool_caps`), and a gateway on a hub client older than 1.9.55 receives the record without `mode`,
    `budget_at` and `pool_caps`.
  - Tests: `tests/test_throttle_budget.py`; panel text `desktop-plugins/throughput/tests/throughput.test.mjs`.
    **Not tested**: a real publish through two hub links, and a live core passing `provider` to the middleware.
- **Burst** (budget mode only): a control that multiplies throughput for a set time, e.g. 2x for an hour, and
  expires on its own. The budget level carries `burst: {"factor", "until", "set_at"}` (`1 < factor <= 4`, `until` at
  most 24 h after `set_at`; hub client 1.9.56 or newer carries it, an older one drops it in transit, and an older
  plugin ignores it: those gateways keep the unboosted caps).
  - **Caps**: while `now < until`, every budget cap (`cap_per_s` and each positive pool cap) is `budget x (1 -
    percent/100) x factor`; the start gate's governed cap uses the same scaled caps. A pool cap of 0 (reserve) stays 0,
    a null cap stays null, and 100 % still holds everything: a burst never lifts a reserve hold or the slider's stop.
    `budget.burst_factor` in the payload is the factor in force (1 without one).
  - **Rides on the level**: starting or ending a burst writes a new level stamped like the slider (after the level
    in force, `max(now, set_at + 2 ms)`), so the newest setting wins fleet-wide. The slider (`set_level`) keeps the
    burst; the budget publisher keeps a running burst when it rewrites the level (and folds in one set on another
    gateway, like a slider move) and drops an expired one. A manual level (`write_level`) never carries one.
  - **Expiry** needs no action: once `until` passes the burst is ignored everywhere. An expired or malformed burst,
    a burst on a manual level, or one whose `until` is more than 24 h from this gateway's clock, is ignored.
  - **Mixed versions: update before using it.** The budget-publish host and every host where a burst is set must run
    this plugin and hub client 1.9.56 or newer before anyone starts a burst: an older publisher rewrites the level
    without `burst` (ending it fleet-wide on its next write), and an older client drops it in transit. A gateway on an
    older plugin ignores the burst and keeps the unboosted caps; its start gate then compares the fleet's burst-level
    spend against those caps, so it holds card starts for the length of the burst until it updates.
  - **API**: `PUT /v1/throttle/burst` with `{"factor": 1 < f <= 4, "hours": 0 < h <= 24}` starts one on the budget
    level in force; any other value is a 400, and a manual level (or an expired budget) a 409 that says why.
    `DELETE /v1/throttle/burst` ends it now (a no-op without one). Both answer with the `GET /v1/throttle` payload,
    which carries `burst: {factor, until, set_at, remaining_s, active}` (`null` without one).
  - **What it costs** (`budget_view.project`, `GET /v1/budget?factor=&hours=`): per pool in core's `v2`, `rate_now`
    (the planned `rate_tokens_per_s`), `extra_tokens = min(remaining, factor x rate_now x duration) - min(remaining,
    rate_now x duration)` (the burst's spend over the plan's in the same time), `exhausts_during_burst_at` (when the
    remaining tokens run out at the burst rate, if before the burst ends: the pool would hit its reserve),
    `earlier_s` (how much earlier the pool runs out if spend goes back to the old plan without re-planning:
    `extra_tokens / rate_now`, at most the time to `exhaust_plan_at`; when it runs out during the burst, exactly
    `exhaust_plan_at - exhausts_during_burst_at`), `rate_after = (remaining - burst spend) / (exhaust_plan_at -
    burst end)`, at least 0 (the slower rate the planner re-plans to), and `exceeds_5h_cap` with `rate_cap_5h`:
    whether the burst rate is above core's 5-hour cap for the pool (the summed rate at which each account under
    reserve would spend its 5-hour window down to its reserve by that window's reset; null when core has none), so a
    sustained burst would park accounts at their 5-hour reserve until the window resets. A pool at reserve gives
    zeros (`exceeds_5h_cap` false), a pool of unknown rate nulls, a missing or malformed `v2` `{}`.
  - Tests: `tests/test_throttle_burst.py`, `tests/test_budget_view.py`, `tests/test_api.py`
    (`test_burst_routes_set_end_and_refuse`). **Not tested**: a burst travelling through two real hub links.
- **Budget view** (`GET /v1/budget[?factor=&hours=]`, `budget_view.py`): `{now, computed_at, margin_s,
  global_rate_tokens_per_s, pools, events, curve, history, burst, projection}`. `pools`, `events` and `curve` are this
  host's `v2` (`llm_pacing.json`, so only a host whose dispatcher runs core's pacing governor, the hub, has them;
  others answer empty): pools without their account readings (`accounts` is a count), events of the types
  `reset`, `reset_early`, `account_added`, `account_removed` as `{ts, provider, type, kind}`, curves as
  `[[t, capacity, planned_spend]]`. `history` is the measured spend per provider, hourly over the last 7 days, from
  the series' `tokens_io:<model>` rows of every gateway attributed like the start gate (`provider_of_model`):
  `{bucket_s: 3600, since, until, t: [bucket starts], providers: {p: {rate: [tokens/s or null when no gateway
  sampled the hour], cumulative: [tokens from since through each bucket's end]}}}`. `projection` is
  `{factor, hours, pools: project(...)}` when both are given (each validated like the burst route; one without the
  other is a 400), else null. One read of the pacing file and the 7-day slice of the series (the query walks the
  primary key per gateway: about 0.2 s on a 60 MB series), cached for 60 s per process. `&projection_only=1` (with
  factor and hours) returns just `{now, computed_at, burst, projection}` and reads no series: the panel fetches the
  charts once per poll and asks only this when the burst pick changes. Test: `tests/test_api.py`
  (`test_budget_view_endpoint_shape`).
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
  start, not publication. A snapshot is served for the current minute's window, or the previous minute's until the
  next tick's flight replaces it, and never past 90 seconds of wall or monotonic age; on cold start or after
  incomplete/failed reads the response is `slots: null` (never stale limits or fabricated zero holds).
  The current request's cap always recomputes the plan from the measurement.
  All optional replica and usage reads run in one owned subprocess per root, not on GET/PUT's awaited path.
  A 15-second deadline cancels/kills and reaps that child; teardown joins its coordinator before reloading.
  **Cadence (advisory.py)**: the share window only moves when a minute ends, so the advisory is computed **at most
  once per minute**: 3 s after each minute boundary (just after the sampler's write), and once for that minute
  however many reads arrive and whether it succeeded (a failure invalidates the prior snapshot and waits for the
  next minute; no in-minute retry). Reads do not drive refreshes: `GET /v1/throttle` (and `POST /v1/watch` with
  `advisory: true`) records interest; a read that finds no usable snapshot and no flight yet for its minute starts
  that minute's flight (cold start). The scheduler runs only while interest was recorded in the last 5 minutes; with
  none it exits and nothing is computed. The dashboard's own pushes read with `interest=False`, so they never keep
  it alive. Before 2026-11-20 every read kept a 10 s refresh loop warm for 45 s and failures retried every second:
  the Desktop's 15 s polls kept `slots_worker.py` running permanently on the hub (measured: 46 workers a minute).
  Plugin unload and process exit own cleanup. Router lifespan shutdown also cleans up when mounted before
  startup; hot-mounted routers do not run that lifespan and rely on unload/process-exit cleanup.
  Boot attribution uses only a nonblocking fresh Cards snapshot, rechecked at each refresh. Missing mappings
  (including UUIDs Cards cannot learn), an empty sampled usage window, or any busy gateway without sampled
  usage leave the entire slots advisory unavailable. This is intentionally fail-closed, not a claim that the
  live fleet has complete attribution or usage. An unlearnable UUID need not recover on the next series refresh.
  No background Cards rebuild or empty series substitution.
  Tests: `tests/test_slots.py`, `tests/test_advisory.py` (one flight per minute tick at 5/15/30 s read rates, no
  compute without interest, interest lapse, failed minute not retried, listener), `tests/test_api.py` (real blocked
  Cards rebuild, SQLite-lock GET/PUT bound, cancellation and lifecycle). **Not tested**: live macOS activation/soak.
- **Runway** (`runway.py`): per subscription pool, from the usage samples core's Kanban pacing governor keeps in
  `llm_pacing.json` (no usage request is made): `remaining_pct` (sum of `100 - used` over the accounts' weekly
  windows), `burn_pct_per_h` (each account's slope over the last 10-60 minutes), `runway_s = remaining / burn`
  (`null` when nothing burns) and `next_reset_at` (the earliest weekly reset). Resets are not folded into the
  runway. Account names are not returned. A host without the governor's file reports no pools, and a provider
  whose newest sample is over 30 minutes old is left out. `pacing` is the governor's own verdict for the pool
  (`open`, `paced`, `held`; `pacing_until` for a hold's known end): with `paced` or `held`, Kanban starts fewer or
  no cards on that pool whatever the throttle level is, and the panel says so on the pool's line.

The Desktop panel opens on three **tiles**: spending now, the limit (whose: your limit, the subscription budget or
no limit; orange at the limit) and this gateway's card slots (running / open, from `slot_gate`). Under them,
**Budget / Limit / No limit**: Budget clears an own cap and switches budget pacing on (`PUT /v1/throttle
{"cap_per_min": null}`, then `PUT /v1/throttle/pacing {"on": true}` when it was off); Limit sends a number in
thousands of tokens per minute on Enter or Set (`{"cap_per_min": N}`; with pacing on the lower of it and the budget
applies); No limit clears the cap and switches pacing off where a budget exists (never on a fleet without a publisher, where an
Off would fake one). Budget is offered only where a budget publisher exists; while it starts the limit tile says
so. One change at a time: the controls are disabled while it saves, and each change reads the limit afresh first.
Beside it **Raise 2× · 1 h** (the burst, only on a live budget level; "…" picks factor and duration; the cost per
pool is the hover text, a reserve or 5-hour warning one line). Then one row per subscription: a block per account
(filled = still usable), "3 of 7 · 9.4 d" (at the limit or this pace) and the state colour (tight, paced, held, used
up). The limit is a red line on the Tokens chart (hidden with `+cache` or a legend filter). Right-clicking the
status-bar chip opens the same choices and a raise (2× or 3× for an hour, or End). Tests:
`desktop-plugins/throughput/tests/throughput.test.mjs`.

Below the charts, the folded **Forecast · 7 days** (when this host has core's `v2`): one chart per pool plus a total ("All subscriptions"),
from 7 days ago to the furthest reset in the curve. Left of the "now" line, the measured cumulative spend; right of
it, core's capacity steps (green) and planned cumulative spend (dashed), both lifted by the spend so far so they
continue the measured line. Ticks along the bottom mark resets (and the curve's planned weekly resets), early
resets, accounts added or removed; a red shaded span marks the burst (dashed edges while it is only being
considered). The burst control (budget mode) picks 1.5x, 2x or 3x for 30 min, 1, 2 or 4 h and, before it can be
started, lists each pool's cost ("runs out ~X earlier at today's rate · after the burst the planner slows to Y
tok/s") with a warning when a pool would hit its reserve during the burst or run over its 5-hour cap; a running burst shows its time left and
an End button. In budget mode the slider stays, labelled "Hold back N%" (the share of the budget held back), and the
cap text shows the burst ("Budget −20% · burst 2x · cap ..."). Lines and text use the theme's variables
(`--ui-blue`, `--ui-green`, `--ui-orange`, `--ui-purple`, `--ui-red`, `--ui-text-*`), with fallbacks of at least 3:1
contrast on white and on near-black, so the charts read in light and dark themes. Tests: `desktop-plugins/throughput/tests/throughput.test.mjs`.

Above the slider, a **Budget pacing** On/Off switch (`role="switch"`, shown when the backend sends `pacing`) puts
`/v1/throttle/pacing`. Off: the burst control is hidden, the slider is labelled "Manual cap N%", and one line says
budget pacing is off. Pending: "Budget pacing is turning on…". Unavailable: "No hub is publishing a budget". On: which
gateway publishes it. The note sits on its own line and wraps; the switch's label id is unique per panel. The switch
is not shown on a fleet that has no budget publisher (`unavailable` with `budget_seen` false). The switch uses `--ui-stroke-secondary`, `--ui-green`, `--ui-bg` and `--ui-text-tertiary` with
fallbacks, so it reads in light and dark themes.

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

### Push

`POST /api/plugins/throughput/v1/watch` with `{"series": [{"range": "6h", "by": "gateway"}], "advisory": bool}`
registers interest for 5 minutes and answers the current snapshot (the catch-up). `GET /v1/series` and
`GET /v1/throttle` count as interest too; only `GET /v1/throttle` and a watch with `advisory: true` keep the slots
advisory computing. While anyone is interested, the process serving the Desktop (`hermes serve`; no thread starts
where the gateway transport is not loaded) pushes **`plugin.throughput.snapshot`** once a minute, right after that
minute's advisory flight (at the latest 20 s into the minute), plus once when interest starts and after every other
finished advisory flight (a cold start), changed or not:

```json
{"feed": "hub:4242", "epoch": "…", "seq": 7, "at": 1800000003.1, "minute": 1800000000, "interval_s": 60,
 "throttle": {"…": "the GET /v1/throttle payload"}, "series": {"6h|gateway": {"…": "GET /v1/series"}},
 "series_keys": ["6h|gateway"]}
```

`series` holds the views read or watched in the last 5 minutes (at most 4, the most recent). The body is JSON-safe
(NaN/inf → null); over 256 KB the series go by name only (`series_keys`) and clients read them. `feed` (host:pid) is
also on every `GET /v1/throttle` payload, so a client knows which connection a push belongs to. With no interest for
5 minutes the push thread exits. Tests: `tests/test_push.py` (one push per minute, deadline push, no push without
interest, JSON safety and size bound, the advisory kick, the thread, the watch route). **Not tested**: delivery over
a live gateway stream (core `broadcast_plugin_event`).

Desktop (`desktop-plugins/throughput`): while the window is shown and the host has an event stream (`ctx.onEvent`),
it watches its current view (`advisory` while the panel or chip menu is open) on mount, on a view change and every
4 min, applies every push whose `feed` matches the watch answer's to the series and throttle queries, and, while
pushes arrive (the last within 2 min), re-reads only every 4 min instead of every 30 s. Pushes stopped for 2 min (or
a gateway without `/v1/watch`): the 30 s reads resume. Nothing runs while the window is hidden. The budget charts
are not pushed; they still read every 30 s while the panel is open. Tests: `throughput.test.mjs` "push: …",
"applySnapshot: …".

## Tests

```bash
scripts/test-all --no-core --exact -k plugins/throughput
HERMES_DESKTOP_PACKAGE=<dir with react, react-dom, jsdom, @tanstack/react-query, radix-ui>/package.json \
  node --test desktop-plugins/throughput/tests/*.test.mjs
```

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

Until every gateway's registry metrics carry the token/session counters, tokens and sessions cover the local
gateway only; cards cover every gateway.

## Sampler

`register()` starts a sampler thread in long-lived server processes only (`hermes gateway`, `hermes serve`,
`hermes dashboard`; never a CLI chat or a Kanban worker). A non-blocking `flock` on
`cache/throughput/sampler.lock` keeps one sampler per Hermes root; the others wait and take over if it exits.
The REST API also starts one if the process has none.

## API

`GET /api/plugins/throughput/v1/series?range=1h|6h|24h|7d` (buckets 1/5/15/60 min):

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

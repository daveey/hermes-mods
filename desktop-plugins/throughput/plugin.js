// Throughput: tokens, sessions and cards per gateway over time (direction B "Flow").
// Chip (titlebar, left of the llm-split chip; status-bar fallback): 34 px token sparkline + "56k tok/m · 1.5 cards/m". Popover: three mirrored stacked charts
// (inflow above the line, outflow below), stacked by gateway. Data: GET /v1/series?range=… on the
// local gateway (plugins/throughput/series.py): per-bucket counts, null = gateway doesn't report.
// Throttle: GET/PUT /v1/throttle (plugins/throughput/throttle.py). A slider under Tokens sets a 0-100 % level;
// its cap is the red line on the Tokens chart, and each subscription pool's runway is listed beside it.
// Budget: GET /v1/budget (plugins/throughput/budget_view.py). Under Tokens, one chart per budget pool plus a total:
// the spend measured over 7 days left of "now", core's capacity steps and planned spend right of it, with resets,
// account changes and the burst marked. PUT/DELETE /v1/throttle/burst starts or ends a burst (budget mode only).
// Budget pacing switch: PUT /v1/throttle/pacing {on}. Off = an explicit manual level the hub's publisher leaves alone
// (the slider is then a plain manual cap and there is no burst); `pacing.state` is on, off, pending or unavailable.
import { useQuery, useQueryClient, STATUSBAR_AREAS, PALETTE_AREA, Popover, PopoverTrigger, PopoverContent, ContextMenu, ContextMenuTrigger, ContextMenuContent, ContextMenuItem, ContextMenuCheckboxItem, ContextMenuSeparator } from '@hermes/plugin-sdk'
import { createElement as h, useState, useEffect, useMemo, useRef } from 'react'

export const POLL_MS = 30000
// Push (plugins/throughput dashboard): POST /v1/watch registers the views this window shows (5 min of interest on the
// gateway) and answers the current snapshot with the gateway's `feed` id; the gateway then pushes
// `plugin.throughput.snapshot` once a minute (after its sampler minute) with the throttle and those series. While its
// pushes arrive (the last within PUSH_TRUST_MS) nothing polls; the watch is renewed and the views re-read every
// SAFETY_MS. When pushes stop (or the gateway has no /v1/watch) the POLL_MS reads resume. Nothing runs while hidden.
export const SNAPSHOT_EVENT = 'plugin.throughput.snapshot'
export const PUSH_TRUST_MS = 120000
export const SAFETY_MS = 240000
export const REWATCH_MS = 30000
export const REQUEST_TIMEOUT_MS = 20000 // the hub answers in 0.3-10 s under load
export const STORE_KEY = 'hermes-throughput-view'
export const RANGES = ['1h', '6h', '24h', '7d']
export const UNITS = ['s', 'm', 'h']
const UNIT_S = { s: 1, m: 60, h: 3600 }
export const GROUPS = ['gateway', 'model']
export const DEFAULT_VIEW = { unit: 'm', range: '6h', cache: false, by: 'gateway' }
const PALETTE = ['#7aa2ff', '#3ddc97', '#f0a43a', '#d982f0', '#5fd4e6', '#ef7b7b', '#c9d36a', '#e8a0c8', '#a0e8c8', '#8fb0ff']
const FIXED = { ephemeral: '#9aa0aa', unattributed: '#6b7280' }
const NOREP = 'These gateways don\u2019t report this metric yet (their Board counters arrive with the core token/session counters), so they show \u201c\u2013\u201d, never 0'
const BAR = 'inline-flex h-full items-center gap-1.5 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
const TB_CHIP = 'hermes-tp-tbchip'
export const TITLEBAR_SPAN = 8 // titlebar cells (8 x 24 px; the core's span cap), left of llm-split (order 50)
export const SHOW_EVENT = 'hermes-show-throughput'

// ── pure helpers (exported for tests) ─────────────────────────────────────

export const validSeries = (body, by = 'gateway') =>
  !!body && Array.isArray(body.gateways) && (by !== 'model' || Array.isArray(body.models)) && !!body.series && !!body.bucket_s
export const seriesQueryKey = (range, by = 'gateway') => by === 'model' ? ['throughput', 'series', range, 'model'] : ['throughput', 'series', range]

// A push counts toward liveness only when it carries every view this window shows (by name: a view too big to push is
// still named in series_keys and re-read).
export const pushCovers = (p, keys) => Array.isArray(p?.series_keys) && keys.every((k) => p.series_keys.includes(k))

// One snapshot (a push, or the watch answer) into the query cache: every series view it carries, and the throttle. A
// view sent by name only (too big to push) is re-read. True when it carried anything usable.
export function applySnapshot(client, p) {
  if (!p || typeof p !== 'object') return false
  let used = false
  for (const key of Array.isArray(p.series_keys) ? p.series_keys : []) {
    const [range, by = 'gateway'] = String(key).split('|')
    if (!RANGES.includes(range) || !GROUPS.includes(by)) continue
    const body = p.series?.[key]
    if (validSeries(body, by)) { client.setQueryData(seriesQueryKey(range, by), body); used = true }
    else client.invalidateQueries({ queryKey: seriesQueryKey(range, by), exact: true })
  }
  if (p.throttle && typeof p.throttle.percent === 'number') { client.setQueryData(['throughput', 'throttle'], p.throttle); used = true }
  return used
}

export function loadView(storage = globalThis.localStorage) {
  try {
    const v = JSON.parse(storage?.getItem(STORE_KEY) || '{}') || {}
    return {
      unit: UNITS.includes(v.unit) ? v.unit : DEFAULT_VIEW.unit,
      range: RANGES.includes(v.range) ? v.range : DEFAULT_VIEW.range,
      cache: v.cache === true,
      by: GROUPS.includes(v.by) ? v.by : DEFAULT_VIEW.by
    }
  } catch { return { ...DEFAULT_VIEW } }
}

export function saveView(view, storage = globalThis.localStorage) {
  try { storage?.setItem(STORE_KEY, JSON.stringify({ unit: view.unit, range: view.range, cache: !!view.cache, by: GROUPS.includes(view.by) ? view.by : DEFAULT_VIEW.by })) } catch {}
}

export function fmt(v) {
  if (v == null || !Number.isFinite(v)) return '\u2013'
  const a = Math.abs(v)
  if (a >= 1e9) return (v / 1e9).toFixed(a >= 1e10 ? 0 : 1) + 'B'
  if (a >= 1e6) return (v / 1e6).toFixed(a >= 1e7 ? 0 : 1) + 'M'
  if (a >= 1e3) return (v / 1e3).toFixed(a >= 1e4 ? 0 : 1) + 'k'
  if (a >= 10) return v.toFixed(0)
  if (a >= 1) return v.toFixed(1)
  if (a === 0) return '0'
  return v.toFixed(2)
}

export function colorFor(gateways) {
  const out = {}
  let i = 0
  for (const g of gateways) out[g] = FIXED[g] || PALETTE[i++ % PALETTE.length]
  return out
}

// A per-bucket (or per-minute) count array as a rate in the chosen unit; missing stays null. `seconds` is one
// number for every bucket, or an array (the sampled seconds of each bucket; 0 sampled = missing).
export const toRate = (counts, seconds, unit) => counts.map((c, i) => {
  const s = Array.isArray(seconds) ? seconds[i] : seconds
  return c == null || !s ? null : (c * UNIT_S[unit]) / s
})

// Seconds actually sampled per bucket for a sampled metric (tokens, sessions); null = whole buckets (cards, or an
// older backend without `sampled`).
export function sampledSeconds(payload, metric, gw) {
  const s = payload?.sampled || {}
  const a = metric === 'tokens' ? (s.tokens_io?.[gw] ?? s.tokens_cache?.[gw]) : s[metric]?.[gw]
  return Array.isArray(a) ? a.map((n) => n * 60) : null
}

// Counts per bucket for one metric, or null when the gateway doesn't report it. `tokens` combines
// tokens_io with tokens_cache when cache is on (missing only if both are).
export function counts(payload, metric, gw, cache) {
  const s = payload?.series || {}
  if (metric !== 'tokens') return s[metric]?.[gw] ?? null
  return combine(s.tokens_io?.[gw], cache ? s.tokens_cache?.[gw] : undefined)
}
export function recent(payload, metric, gw, cache) {
  const s = payload?.recent || {}
  if (metric !== 'tokens') return s[metric]?.[gw] ?? null
  return combine(s.tokens_io?.[gw], cache ? s.tokens_cache?.[gw] : undefined)
}
function combine(a, b) {
  if (!a) return b || null
  if (!b) return a
  return a.map((v, i) => (v == null && b[i] == null ? null : (v || 0) + (b[i] || 0)))
}

// Rate now = average over the last 5 min (range 1h) or 15 min (other ranges) of the sampled minutes.
export const nowWindow = (range) => (range === '1h' ? 5 : 15)
export function rateNow(payload, metric, gws, view) {
  const w = nowWindow(view.range)
  const per = new Array(w).fill(null) // summed per minute over the gateways; null = nobody sampled it
  for (const g of gws) {
    const r = recent(payload, metric, g, view.cache)
    if (!r) continue
    const s = r.slice(-w), off = w - s.length
    s.forEach((v, i) => { if (v != null) per[off + i] = (per[off + i] || 0) + v })
  }
  const sampled = per.filter((v) => v != null)
  if (!sampled.length) return null
  return (sampled.reduce((a, b) => a + b, 0) / (sampled.length * 60)) * UNIT_S[view.unit]
}

export function visibleGateways(gateways, filter) {
  return gateways.filter((g) => !filter.hidden.includes(g) && (!filter.solo || filter.solo === g))
}

// Legend click: plain = only this one (again = all); shift = toggle hidden; 'all' = reset.
export function legendClick(filter, g, shift) {
  if (g === 'all') return { solo: null, hidden: [] }
  if (shift) {
    const hidden = filter.hidden.includes(g) ? filter.hidden.filter((x) => x !== g) : [...filter.hidden, g]
    return { solo: null, hidden }
  }
  return { solo: filter.solo === g ? null : g, hidden: filter.hidden }
}

// Stacked layers (rates) for the visible gateways that report `metric`, and the per-bucket totals.
export function layers(payload, metric, gws, view) {
  const L = []
  for (const g of gws) {
    const c = counts(payload, metric, g, view.cache)
    if (c) L.push({ g, v: toRate(c, sampledSeconds(payload, metric, g) ?? payload.bucket_s, view.unit) })
  }
  const len = L[0]?.v.length ?? Math.round(((payload?.now ?? 0) - (payload?.since ?? 0)) / (payload?.bucket_s || 60))
  const total = Array.from({ length: len }, (_, i) => {
    let t = null
    for (const l of L) if (l.v[i] != null) t = (t || 0) + l.v[i]
    return t
  })
  return { layers: L, total, len }
}

export function notReporting(payload, metric, gws) {
  const key = metric === 'tokens' ? 'tokens_io' : metric
  return gws.filter((g) => (payload?.series?.[key] || {})[g] == null)
}

export function timeLabel(t, range) {
  const d = new Date(t * 1000)
  const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
  return range === '7d' ? d.toLocaleDateString([], { weekday: 'short' }) + ' ' + hm : hm
}

export function chipNumbers(payload, view) {
  if (!payload?.gateways) return null
  const gws = payload.gateways
  const tok = rateNow(payload, 'tokens', gws, view)
  const cards = rateNow(payload, 'cards_out', gws, view)
  const { total } = layers(payload, 'tokens', gws, view)
  return { tok, cards, spark: total.slice(-24) }
}

// ── throttle + runway (pure) ──────────────────────────────────────────────

export const THROTTLE_RED = '#ef4444'
const POOL_NAMES = { anthropic: 'Claude', 'openai-codex': 'GPT', 'xai-oauth': 'Grok' }

// The cap as a rate in the chosen unit; null = no cap (0 %, or no 24-hour peak to scale yet).
export function capRate(throttle, unit) {
  const c = throttle?.cap_per_s
  return c == null || !Number.isFinite(c) ? null : c * UNIT_S[unit]
}

export function fmtDuration(s) {
  if (s == null || !Number.isFinite(s) || s < 0) return '\u2013'
  if (s >= 2 * 86400) return (s / 86400).toFixed(1) + ' d'
  if (s >= 2 * 3600) return (s / 3600).toFixed(1) + ' h'
  return Math.max(1, Math.round(s / 60)) + ' min'
}

// The fleet's burn slows by cap/rate when the cap is under the measured rate; 0 = paused, 1 = unchanged.
export function burnScale(throttle) {
  const cap = throttle?.cap_per_s, rate = throttle?.rate_per_s
  if (cap == null) return 1
  if (cap <= 0) return 0
  return rate != null && rate > cap ? cap / rate : 1
}

// Where the level applies: the whole fleet through the hub link (with how many running gateways have acknowledged
// the current setting), or this gateway alone on a host without the link.
export function scopeText(throttle) {
  if (!throttle) return ''
  if (!throttle.fleet) return throttle.scope ? `applies to ${throttle.scope} only` : ''
  const all = throttle.gateways || []
  if (throttle.set_at == null || !all.length) return 'applies to all gateways'
  return `applies to all gateways \u00b7 ${all.filter((g) => g.seen).length} of ${all.length} have this level`
}

export function scopeTitle(throttle) {
  if (!throttle?.fleet) return 'This host has no hub link, so the level stays on this gateway.'
  const waiting = (throttle.gateways || []).filter((g) => !g.seen).map((g) => g.name)
  const by = throttle.set_by ? `Set on ${throttle.set_by}. ` : ''
  const lag = 'A new level reaches each gateway through the hub link within a few minutes.'
  return `${by}${lag}${waiting.length && throttle.set_at != null ? ` Not acknowledged yet: ${waiting.join(', ')}.` : ''}`
}

// ── budget pacing switch (pure) ───────────────────────────────────────────

// The switch's state from the payload's `pacing` (null on an older backend without the switch).
export const pacingState = (throttle) => {
  const s = throttle?.pacing?.state
  return ['on', 'off', 'pending', 'unavailable'].includes(s) ? s : null
}
export const pacingIsOff = (throttle) => pacingState(throttle) === 'off'

// ── token tiles (pure) ────────────────────────────────────────────────────

// What a person chose: `budget` (the subscription budget alone), `limit` (a number of their own, 0 included) or
// `no_limit` (nothing: budget pacing off and no cap). From the payload's level, budget pacing switch and own cap.
export const budgetOffered = (throttle) => showPacing(throttle)
export function limitChoice(throttle) {
  if (!throttle) return null
  if ((throttle.percent ?? 0) >= 100) return 'limit'
  const own = typeof throttle.user_cap_per_s === 'number' ? throttle.user_cap_per_s : null
  if (pacingIsOff(throttle)) return own != null || typeof throttle.cap_per_s === 'number' ? 'limit' : 'no_limit'
  if (own != null) return 'limit'
  if (budgetOffered(throttle)) return 'budget'
  return typeof throttle.cap_per_s === 'number' ? 'limit' : 'no_limit'
}

export const CHOICE_LABEL = { budget: 'subscription budget', limit: 'your limit', no_limit: 'no limit' }

// The three tiles' numbers. `state`: unlimited | under | at | holding.
export function tiles(throttle, unit) {
  const choice = limitChoice(throttle)
  const rate = throttle?.rate_per_s == null ? null : throttle.rate_per_s * UNIT_S[unit]
  const cap = capRate(throttle, unit)
  const holding = (throttle?.percent ?? 0) >= 100 || cap === 0
  const state = holding ? 'holding' : cap == null ? 'unlimited' : rate != null && rate > cap ? 'at' : 'under'
  const f = burstFactor(throttle)
  const ps = pacingState(throttle)
  const limitNote = state === 'holding' ? 'every call held' : state === 'at' ? 'at the limit, calls queue'
    : choice === 'budget' && ps === 'pending' ? 'budget starting (a few minutes)'
      : choice === 'budget' && ps === 'unavailable' ? 'no hub is publishing a budget'
        : state === 'unlimited' ? 'nothing limits spend' : 'under the limit'
  const g = throttle?.slot_gate
  const slots = !g ? null : g.allowed == null
    ? { big: typeof g.running === 'number' ? String(g.running) : '–', of: null, note: 'running here · no slot limit' }
    : { big: typeof g.running === 'number' ? String(g.running) : '–', of: String(g.allowed), note: 'running · open on this gateway' }
  return {
    choice, state, rate, cap, slots,
    limitLabel: `Limit · ${CHOICE_LABEL[choice] || 'none'}${f > 1 ? ` · raised ${fmtFactor(f)}` : ''}`,
    limitBig: holding ? '0' : cap == null ? (choice === 'budget' && ps === 'pending' ? '\u2026' : 'None') : fmt(cap), limitNote
  }
}

// One subscription row: one block per account (filled = still usable), how long what is left lasts, a state.
// `state`: ok | tight (runs out before the next refill) | paced (quota pacing starts fewer cards) | held (quota pacing
// holds card starts; chats still run) | used (every account has used its week). The runway is scaled by the
// fleet-wide limit/rate ratio: an estimate, the limit is not split per pool.
export function accountRow(pool, throttle, now = Date.now() / 1000) {
  const name = POOL_NAMES[pool.provider] || pool.provider
  const total = typeof pool.accounts === 'number' ? pool.accounts : null
  const usable = total == null ? null : Math.max(0, total - (pool.exhausted ?? 0))
  const scale = burnScale(throttle)
  const runway = pool.runway_s == null || scale === 0 ? null : pool.runway_s / scale
  const left = pool.runway_s == null ? 'idle' : scale === 0 ? 'paused' : fmtDuration(runway)
  let state = 'ok'
  if (usable === 0) state = 'used'
  else if (pool.pacing === 'held') state = 'held'
  else if (runway != null && pool.next_reset_at && runway < pool.next_reset_at - now) state = 'tight'
  else if (pool.pacing === 'paced') state = 'paced'
  const why = { used: 'every account has used its week', held: 'quota pacing holds card starts; chats still run',
    tight: 'runs out before the next refill', paced: 'quota pacing starts fewer cards', ok: null }[state]
  const refill = pool.next_reset_at ? `next refill ${timeLabel(pool.next_reset_at, '7d')}` : null
  const text = total == null ? left : `${usable} of ${total} · ${left}`
  const title = [`${name}: ${state === 'ok' ? 'ok' : why}`, total == null ? null : `${usable} of ${total} accounts usable`,
    pool.runway_s == null ? 'not in use' : `lasts ${left} at ${scale < 1 ? 'the limit' : 'this pace'}`, refill].filter(Boolean).join('\n')
  return { provider: pool.provider, name, total, usable, text, state, title }
}

// Thousands of tokens per minute typed in the limit field: '150' or '150k' = 150 000, '1.5m' = 1 500 000; undefined
// for anything else.
export function parseLimitK(raw) {
  const m = String(raw ?? '').trim().toLowerCase().match(/^([0-9]+(?:\.[0-9]+)?)\s*([km]?)$/)
  return m ? Number(m[1]) * (m[2] === 'm' ? 1e6 : 1e3) : undefined
}

// ── budget chart + burst (pure) ───────────────────────────────────────────

export const BURST_FACTORS = [1.5, 2, 3]
export const BURST_HOURS = [0.5, 1, 2, 4]
export const DEFAULT_BURST = { factor: 2, hours: 1 }
// The Desktop theme's own colours (light and dark each define them), with fallbacks that keep at least 3:1 against
// a white or a near-black background for a host without them. Capacity is a darker green than the chip's, which is
// too faint on white.
export const BUDGET_COLORS = {
  spent: 'var(--ui-blue, #3b82f6)', capacity: 'var(--ui-green, #1f8a65)', reset: 'var(--ui-green, #1f8a65)',
  reset_early: 'var(--ui-orange, #d9822b)', account_added: 'var(--ui-purple, #8b6fd6)',
  account_removed: 'var(--ui-text-tertiary, #8a909c)', burst_start: 'var(--ui-red, #dc3545)', burst_end: 'var(--ui-red, #dc3545)'
}
const MARK_LABEL = {
  reset: 'refill', reset_early: 'early refill', account_added: 'account added', account_removed: 'account removed',
  burst_start: 'raise starts', burst_end: 'raise ends'
}
export const poolName = (p) => (p == null ? 'All pools' : POOL_NAMES[p] || p)
export const fmtFactor = (f) => `${f}\u00d7`
export const fmtHours = (h) => (h < 1 ? `${Math.round(h * 60)} min` : `${h} h`)

// The factor a burst multiplies the budget caps by right now (1 = none). The server's budget says which burst is in
// force; a payload without it falls back to the burst's own `active`.
export function burstFactor(throttle) {
  const f = throttle?.budget?.burst_factor
  if (typeof f === 'number' && Number.isFinite(f)) return f
  const b = throttle?.burst
  return b?.active && typeof b.factor === 'number' ? b.factor : 1
}

// "Burst 2× · 42 min left"; null without a running burst.
export function burstText(burst, now) {
  if (!burst || !(burst.until > now)) return null
  return `Raised ${fmtFactor(burst.factor)} \u00b7 ${fmtDuration(burst.until - now)} left`
}

// One pool's cost of a `factor` x burst (budget_view.project): `{text, warn, warn5h}`. `warn`: the pool would hit
// its reserve before the burst ends; `warn5h`: the burst rate is above the pool's 5-hour cap (core's rate_cap_5h).
export function projectionText(provider, pr, factor = null) {
  const name = poolName(provider)
  if (!pr || pr.rate_now == null) return { text: `${name}: rate unknown, the raise cannot be costed`, warn: null, warn5h: null }
  if (pr.rate_now <= 0) return { text: `${name}: planned at 0 tok/s (at reserve or idle), a raise adds nothing`, warn: null, warn5h: null }
  const text = `${name}: runs out ~${fmtDuration(pr.earlier_s)} earlier at today\u2019s rate \u00b7 after the raise the planner slows to ${fmt(pr.rate_after)} tok/s (now ${fmt(pr.rate_now)})`
  const warn = pr.exhausts_during_burst_at == null ? null
    : `${name} would hit its reserve during the raise, at ${timeLabel(pr.exhausts_during_burst_at, '7d')}`
  const warn5h = !pr.exceeds_5h_cap ? null
    : `${name} would run over its 5-hour cap (${fmt(pr.rate_cap_5h)} tok/s${factor ? ` against ${fmt(factor * pr.rate_now)} tok/s` : ''}): accounts reach their 5-hour reserve and wait for the window to reset`
  return { text, warn, warn5h }
}

// A step curve's value at t: the last point at or before t (the first point before the curve starts).
function stepAt(points, t) {
  let v = points.length ? points[0][1] : 0
  for (const p of points) { if (p[0] <= t) v = p[1]; else break }
  return v
}
// A piecewise-linear curve's value at t, held flat beyond its ends.
function lineAt(points, t) {
  if (!points.length) return 0
  if (t <= points[0][0]) return points[0][1]
  for (let i = 1; i < points.length; i++) {
    const [t1, v1] = points[i], [t0, v0] = points[i - 1]
    if (t <= t1) return t1 === t0 ? v1 : v0 + ((v1 - v0) * (t - t0)) / (t1 - t0)
  }
  return points[points.length - 1][1]
}

// What one budget chart draws, in epoch seconds and tokens. `pool` null = the total of every budget pool.
// History: the measured cumulative spend from 7 days ago (0) to now. Forward: core's curve (capacity steps and the
// planned cumulative spend from its computed_at), lifted by the spend so far so both continue the history line.
// Marks: v2 events, the curve's future resets, and the burst (running, or `preview` = {factor, hours} from now).
export function chartSeries(budget, pool, { burst = null, preview = null, now = budget?.now } = {}) {
  const pools = pool == null ? Object.keys(budget?.pools || {}) : [pool]
  const h = budget?.history || {}
  const hp = h.providers || {}
  const hist = []
  if (Array.isArray(h.t) && h.t.length) {
    hist.push([h.since ?? h.t[0], 0])
    h.t.forEach((t0, i) => {
      let v = 0
      for (const p of pools) v += hp[p]?.cumulative?.[i] || 0
      hist.push([Math.min(t0 + (h.bucket_s || 3600), h.until ?? Infinity), v])
    })
  }
  const base = hist.length ? hist[hist.length - 1][1] : 0
  const curves = pools.map((p) => (budget?.curve?.[p] || []).filter((pt) => Array.isArray(pt) && pt.length === 3))
  const times = [...new Set(curves.flatMap((c) => c.map((pt) => pt[0])))].sort((a, b) => a - b)
  const capOf = curves.map((c) => c.map((pt) => [pt[0], pt[1]]))
  const planOf = curves.map((c) => c.map((pt) => [pt[0], pt[2]]))
  const cap = times.map((t) => [t, base + capOf.reduce((s, c) => s + (c.length ? stepAt(c, t) : 0), 0)])
  const plan = times.map((t) => [t, base + planOf.reduce((s, c) => s + lineAt(c, t), 0)])
  const x0 = h.since ?? now - 7 * 86400
  const x1 = Math.max(times.length ? times[times.length - 1] : now + 86400, now + 3600)
  const marks = []
  for (const e of budget?.events || []) {
    if (pools.includes(e.provider) && e.ts >= x0 && e.ts <= x1 && MARK_LABEL[e.type]) {
      marks.push({ t: e.ts, type: e.type, label: `${poolName(e.provider)} ${MARK_LABEL[e.type]}${e.kind === 'five_hour' ? ' (5 h window)' : ''}` })
    }
  }
  for (let i = 1; i < cap.length; i++) {
    if (cap[i][1] > cap[i - 1][1] + 1e-6 && cap[i][0] > now) marks.push({ t: cap[i][0], type: 'reset', label: 'weekly refill (planned)' })
  }
  const running = burst && burst.until > now
  const span = running ? [burst.set_at ?? now, burst.until] : preview ? [now, now + preview.hours * 3600] : null
  if (span) {
    marks.push({ t: span[0], type: 'burst_start', label: MARK_LABEL.burst_start, preview: !running })
    marks.push({ t: span[1], type: 'burst_end', label: MARK_LABEL.burst_end, preview: !running })
  }
  const ymax = Math.max(1, ...hist.map((p) => p[1]), ...cap.map((p) => p[1]), ...plan.map((p) => p[1]))
  return { x0, x1, now, hist, cap, plan, marks, burst: span, base, ymax }
}

// ── components ────────────────────────────────────────────────────────────

function Spark({ values, w = 34, h: H = 14 }) {
  const v = values.map((x) => x || 0)
  const mx = Math.max(...v, 1e-9)
  const pts = v.map((x, i) => `${((i / Math.max(v.length - 1, 1)) * w).toFixed(1)},${(H - 1 - (x / mx) * (H - 2)).toFixed(1)}`).join(' ')
  return h('svg', { width: w, height: H, viewBox: `0 0 ${w} ${H}`, 'aria-hidden': true, className: 'hermes-tp-spark' },
    h('polyline', { points: pts, fill: 'none', stroke: 'currentColor', strokeWidth: 1.4 }))
}

function Seg({ value, options, onPick, onHover, label, render = (o) => o }) {
  return h('div', { className: 'hermes-tp-seg', role: 'group', 'aria-label': label },
    options.map((o) => h('button', {
      key: String(o), type: 'button', className: o === value ? 'is-on' : '', 'aria-pressed': o === value,
      onClick: () => onPick(o), onPointerEnter: onHover ? () => onHover(o) : undefined
    }, render(o))))
}

const W = 620
function Chart({ id, title, up, down, upLabel, downLabel, payload, view, gws, colors, cap = null, children = null }) {
  const [hover, setHover] = useState(null)
  const U = layers(payload, up, gws, view)
  const D = down ? layers(payload, down, gws, view) : null
  const H = down ? 120 : 84
  const mid = down ? H * 0.55 : H - 14
  const len = Math.max(U.len, D?.len ?? 0, 2)
  const mx = Math.max(...U.total.map((x) => x || 0), ...(D ? D.total.map((x) => x || 0) : [0]), 1e-9)
  // The throttle cap: a red line at its rate, pinned to the top edge (with an arrow) when it is above the chart.
  const capY = cap == null ? null : mid - (Math.min(cap, mx) / mx) * (mid - 4)
  const x = (i) => (i / (len - 1)) * W
  const yu = (v) => mid - (v / mx) * (mid - 4)
  const yd = (v) => mid + (v / mx) * (H - mid - 14)
  const paths = []
  const stack = (L, y, opacity, side) => {
    let base = new Array(len).fill(0)
    for (const l of L) {
      const top = base.map((b, i) => b + (l.v[i] || 0))
      const d = 'M' + top.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('L') + 'L' +
        base.map((_, j) => `${x(len - 1 - j).toFixed(1)},${y(base[len - 1 - j]).toFixed(1)}`).join('L') + 'Z'
      paths.push(h('path', { key: side + l.g, d, fill: colors[l.g], fillOpacity: opacity, 'data-gw': l.g, 'data-side': side }))
      base = top
    }
  }
  stack(U.layers, yu, 0.85, 'up')
  if (D) stack(D.layers, yd, 0.45, 'down')
  const peak = (t) => fmt(Math.max(...t.map((v) => v || 0), 0))
  const t0 = payload.since, bs = payload.bucket_s
  const ticks = [0, Math.floor(len / 2), len - 1]
  const nowUp = rateNow(payload, up, gws, view)
  const nowDown = down ? rateNow(payload, down, gws, view) : null
  const missing = notReporting(payload, up, gws)
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect()
    if (!r.width) return
    const i = Math.round(((e.clientX - r.left) / r.width) * (len - 1))
    // Open upward when the plot sits in the lower half of the window, so a long list stays on screen.
    const vh = typeof window === 'undefined' ? 0 : window.innerHeight || 0
    const up = vh > 0 && r.top + r.height / 2 > vh / 2
    setHover(i >= 0 && i < len ? { i, px: e.clientX - r.left, w: r.width, up } : null)
  }
  const tipRows = (L, i) => {
    const rows = L.layers.filter((l) => l.v[i] > 0).sort((a, b) => b.v[i] - a.v[i])
      .map((l) => h('div', { key: l.g, className: 'hermes-tp-tiprow' },
        h('span', { className: 'hermes-tp-sw', style: { background: colors[l.g] } }), h('span', null, l.g),
        h('span', { className: 'hermes-tp-num' }, fmt(l.v[i]))))
    const gaps = L.layers.filter((l) => l.v[i] == null).map((l) => l.g)
    const silent = missing.filter((g) => gws.includes(g))
    if (gaps.length || silent.length) {
      rows.push(h('div', { key: '_missing', className: 'hermes-tp-tiprow is-missing' },
        h('span', null, [...gaps, ...silent].join(', ')), h('span', { className: 'hermes-tp-num' }, '\u2013')))
    }
    return rows
  }
  return h('section', { className: 'hermes-tp-chart', 'data-chart': id },
    h('div', { className: 'hermes-tp-chead' },
      h('h3', null, title),
      h('span', { className: 'hermes-tp-now' },
        h('span', { className: 'hermes-tp-big', 'data-now': up }, fmt(nowUp)),
        h('span', { className: 'hermes-tp-lbl' }, `${upLabel}/${view.unit}`)),
      down ? h('span', { className: 'hermes-tp-now' },
        h('span', { className: 'hermes-tp-big is-down', 'data-now': down }, fmt(nowDown)),
        h('span', { className: 'hermes-tp-lbl' }, `${downLabel}/${view.unit}`)) : null,
      missing.length ? h('span', {
        className: 'hermes-tp-info', tabIndex: 0, role: 'note', 'data-tip': `Not reported by ${missing.join(', ')}. ${NOREP}.`,
        'aria-label': `Not reported by ${missing.join(', ')}`
      }, 'i') : null),
    h('div', { className: 'hermes-tp-plot', onMouseMove: onMove, onMouseLeave: () => setHover(null) },
      h('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', height: H, width: '100%', role: 'img', 'aria-label': title },
        h('line', { x1: 0, x2: W, y1: mid, y2: mid, stroke: 'var(--ui-stroke-secondary, #2a2f39)' }),
        paths,
        capY == null ? null : h('line', { 'data-cap': String(cap), x1: 0, x2: W, y1: capY, y2: capY, stroke: THROTTLE_RED, strokeWidth: 1.2, vectorEffect: 'non-scaling-stroke' }),
        capY == null ? null : h('text', { x: W - 2, y: Math.max(capY - 3, 9), textAnchor: 'end', className: 'hermes-tp-axis hermes-tp-capl' }, `limit ${fmt(cap)}/${view.unit}${cap > mx ? ' \u2191' : ''}`),
        h('text', { x: 2, y: 11, className: 'hermes-tp-axis' }, `${upLabel} \u2191 peak ${peak(U.total)}`),
        D ? h('text', { x: 2, y: H - 16, className: 'hermes-tp-axis' }, `${downLabel} \u2193 peak ${peak(D.total)}`) : null,
        hover ? h('line', { x1: x(hover.i), x2: x(hover.i), y1: 0, y2: H - 12, stroke: 'var(--ui-text-secondary, #8a909c)', strokeWidth: 0.7 }) : null),
      h('div', { className: 'hermes-tp-ticks' }, ticks.map((i) => h('span', { key: i }, timeLabel(t0 + i * bs, view.range)))),
      hover ? h('div', {
        className: 'hermes-tp-tip', role: 'tooltip',
        style: {
          ...(hover.px > hover.w / 2 ? { right: `${hover.w - hover.px + 10}px` } : { left: `${hover.px + 10}px` }),
          ...(hover.up ? { bottom: '16px' } : { top: '4px' })
        }
      },
        h('div', { className: 'hermes-tp-tiptime' }, `${timeLabel(t0 + hover.i * bs, view.range)} \u00b7 per ${view.unit}`),
        h('div', { className: 'hermes-tp-tiphead' }, `${upLabel} ${fmt(U.total[hover.i])}`), tipRows(U, hover.i),
        D ? h('div', { className: 'hermes-tp-tiphead is-down' }, `${downLabel} ${fmt(D.total[hover.i])}`) : null,
        D ? tipRows(D, hover.i) : null) : null),
    children)
}

// One budget pool's (or the total's) capacity and spend: measured spend left of "now", the capacity steps and the
// planned spend right of it, event ticks along the bottom, the burst as a shaded span.
const BH = 96
export function BudgetChart({ id, title, series: s }) {
  const top = 12, bottom = BH - 16
  const x = (t) => ((Math.min(Math.max(t, s.x0), s.x1) - s.x0) / Math.max(s.x1 - s.x0, 1)) * W
  const y = (v) => bottom - (v / s.ymax) * (bottom - top)
  const line = (pts) => pts.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join('')
  const step = (pts) => pts.map((p, i) => (i ? `H${x(p[0]).toFixed(1)}V${y(p[1]).toFixed(1)}` : `M${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`)).join('')
  const capEnd = s.cap.length ? s.cap[s.cap.length - 1][1] : null
  return h('section', { className: 'hermes-tp-bchart', 'data-budget-chart': id },
    h('div', { className: 'hermes-tp-chead' },
      h('h3', null, title),
      h('span', { className: 'hermes-tp-lbl' }, `used in 7 d: ${fmt(s.base)} \u00b7 still available until ${timeLabel(s.x1, '7d')}: ${fmt(capEnd == null ? null : Math.max(0, capEnd - s.base))}`)),
    h('div', { className: 'hermes-tp-plot' },
      h('svg', { viewBox: `0 0 ${W} ${BH}`, preserveAspectRatio: 'none', height: BH, width: '100%', role: 'img', 'aria-label': `${title}: capacity and spend` },
        h('line', { x1: 0, x2: W, y1: bottom, y2: bottom, stroke: 'var(--ui-stroke-secondary, #2a2f39)' }),
        s.burst ? h('rect', { 'data-mark': 'burst', x: x(s.burst[0]), width: Math.max(1, x(s.burst[1]) - x(s.burst[0])), y: top, height: bottom - top, fill: BUDGET_COLORS.burst_start, fillOpacity: 0.12 }) : null,
        s.hist.length ? h('path', { 'data-line': 'spent', d: line(s.hist), fill: 'none', stroke: BUDGET_COLORS.spent, strokeWidth: 1.5, vectorEffect: 'non-scaling-stroke' }) : null,
        s.plan.length ? h('path', { 'data-line': 'plan', d: line(s.plan), fill: 'none', stroke: BUDGET_COLORS.spent, strokeWidth: 1.3, strokeDasharray: '4 3', vectorEffect: 'non-scaling-stroke' }) : null,
        s.cap.length ? h('path', { 'data-line': 'capacity', d: step(s.cap), fill: 'none', stroke: BUDGET_COLORS.capacity, strokeWidth: 1.3, vectorEffect: 'non-scaling-stroke' }) : null,
        s.marks.map((m, i) => h('line', {
          key: `${m.type}-${i}`, 'data-mark': m.type, x1: x(m.t), x2: x(m.t), y1: m.type.startsWith('burst') ? top : bottom - 7, y2: bottom,
          stroke: BUDGET_COLORS[m.type], strokeWidth: 1.4, strokeDasharray: m.preview ? '2 2' : undefined, vectorEffect: 'non-scaling-stroke'
        }, h('title', null, `${m.label} \u00b7 ${timeLabel(m.t, '7d')}`))),
        h('line', { 'data-now-line': '', x1: x(s.now), x2: x(s.now), y1: 2, y2: bottom, stroke: 'var(--ui-text-secondary, #8a909c)', strokeWidth: 1, vectorEffect: 'non-scaling-stroke' }),
        h('text', { x: Math.min(x(s.now) + 3, W - 24), y: 10, className: 'hermes-tp-axis' }, 'now')),
      h('div', { className: 'hermes-tp-ticks' },
        h('span', null, timeLabel(s.x0, '7d')), h('span', null, timeLabel(s.x1, '7d')))))
}

// Raise (a burst on the budget): one button with the picked factor and duration; "…" shows the pickers. The cost per
// pool is the button's hover text; a reserve or 5-hour warning shows as one line. Running: time left and End.
export function BurstControl({ throttle, budget, pick, setPick, onStart, onEnd, error, now }) {
  const [choosing, setChoosing] = useState(false)
  if (pacingIsOff(throttle) || throttle?.mode !== 'auto') return null // no budget to raise
  const burst = throttle?.burst?.active ? throttle.burst : null
  const running = burstText(burst, now)
  if (running) {
    return h('div', { className: 'hermes-tp-burst', 'data-slot': 'burst' },
      h('span', { className: 'hermes-tp-ttext', 'data-burst': String(burst.factor) }, running),
      h('button', { type: 'button', className: 'hermes-tp-btn', onClick: onEnd }, 'End raise'),
      error ? h('div', { className: 'hermes-tp-note', role: 'alert' }, `Raise not changed: ${error}`) : null)
  }
  const proj = budget?.projection
  const fresh = proj && proj.factor === pick.factor && proj.hours === pick.hours
  const rows = fresh ? Object.entries(proj.pools || {}).map(([p, pr]) => projectionText(p, pr, proj.factor)) : []
  const warns = rows.flatMap((r) => [r.warn, r.warn5h].filter(Boolean))
  const tip = fresh ? rows.map((r) => r.text).join('\n') : 'Costing this raise…'
  return h('div', { className: 'hermes-tp-burst', 'data-slot': 'burst' },
    h('div', { className: 'hermes-tp-brow' },
    choosing ? [
      h(Seg, { key: 'f', label: 'Raise factor', value: pick.factor, options: BURST_FACTORS, onPick: (f) => setPick({ ...pick, factor: f }), render: fmtFactor }),
      h(Seg, { key: 'h', label: 'Raise duration', value: pick.hours, options: BURST_HOURS, onPick: (hrs) => setPick({ ...pick, hours: hrs }), render: fmtHours })
    ] : null,
    h('button', { type: 'button', className: 'hermes-tp-btn', disabled: !fresh, title: tip, 'data-raise-tip': tip, onClick: () => onStart(pick) },
      `Raise ${fmtFactor(pick.factor)} · ${fmtHours(pick.hours)}`),
    h('button', { type: 'button', className: 'hermes-tp-btn is-quiet', 'aria-expanded': choosing, 'aria-label': 'Choose how much and how long', onClick: () => setChoosing(!choosing) }, '…')),
    warns.length ? h('div', { className: 'hermes-tp-warn hermes-tp-wline', role: 'alert', 'data-raise-warn': '' }, `⚠ ${warns[0]}${warns.length > 1 ? ` (+${warns.length - 1} more on hover)` : ''}`) : null,
    error ? h('div', { className: 'hermes-tp-note', role: 'alert' }, `Raise not changed: ${error}`) : null)
}

// The 7-day forecast, folded away: per pool and in total, what was used, what the planner means to use and what is
// available, with refills and raises marked.
export function ForecastSection({ budget, throttle, pick, now = Date.now() / 1000 }) {
  const pools = Object.keys(budget?.pools || {})
  if (!pools.length) return null
  const burst = throttle?.burst?.active ? throttle.burst : budget?.burst?.active ? budget.burst : null
  const preview = !burst && throttle?.mode === 'auto' ? pick : null
  const opts = { burst, preview, now }
  return h('details', { className: 'hermes-tp-budget', 'data-slot': 'forecast' },
    h('summary', null, h('h3', null, 'Forecast · 7 days')),
    h('div', { className: 'hermes-tp-chead' },
      h('span', { className: 'hermes-tp-blegend' },
        [['spent', 'used', BUDGET_COLORS.spent, false], ['plan', 'planned', BUDGET_COLORS.spent, true], ['capacity', 'available', BUDGET_COLORS.capacity, false],
          ['reset', 'refill', BUDGET_COLORS.reset], ['reset_early', 'early refill', BUDGET_COLORS.reset_early], ['account_added', 'account added', BUDGET_COLORS.account_added], ['burst', 'raise', BUDGET_COLORS.burst_start]]
          .map(([k, label, color, dashed]) => h('span', { key: k, className: 'hermes-tp-bkey' },
            h('span', { className: dashed == null ? 'hermes-tp-tick' : dashed ? 'hermes-tp-dash' : 'hermes-tp-solid', style: { borderColor: color } }), label)))),
    h(BudgetChart, { key: 'total', id: 'total', title: 'All subscriptions', series: chartSeries(budget, null, opts) }),
    pools.map((p) => h(BudgetChart, { key: p, id: p, title: poolName(p), series: chartSeries(budget, p, opts) })))
}

// Shown when the panel offers Budget (a budget publisher exists or existed lately; hidden on an older backend).
export const showPacing = (throttle) => {
  const state = pacingState(throttle)
  return !!state && !(state === 'unavailable' && throttle?.pacing?.budget_seen === false)
}

const CHOICES = [['budget', 'Budget'], ['limit', 'Limit'], ['no_limit', 'No limit']]
const CHOICE_TIP = { budget: 'The subscription budget decides', limit: 'Your number in thousands of tokens per minute; with the budget on, the lower of the two applies', no_limit: 'Nothing limits spend, the budget included' }

// Budget / Limit / No limit and the limit field. A number is sent with Enter or Set only; leaving the field or picking
// another choice drops it. `onLimit(choice, capPerMin)`.
export function LimitControl({ throttle, onLimit, busy = false }) {
  const current = limitChoice(throttle)
  const [pick, setPick] = useState(null)
  const [draft, setDraft] = useState(null)
  const [bad, setBad] = useState(false)
  const shown = pick ?? current
  const own = typeof throttle?.user_cap_per_s === 'number' ? throttle.user_cap_per_s : (throttle?.percent ?? 0) >= 100 ? 0 : null
  const k = own == null ? null : (own * 60) / 1000
  const value = draft ?? (current === 'limit' && k != null ? String(k >= 10 ? Math.round(k) : Math.round(k * 10) / 10) : '')
  const choices = CHOICES.filter(([c]) => c !== 'budget' || budgetOffered(throttle))
  const choose = (c) => {
    setBad(false); setDraft(null)
    if (c === 'limit') { setPick(current === 'limit' ? null : 'limit'); return }
    setPick(null)
    if (c !== current) onLimit?.(c, null)
  }
  const commit = () => {
    if (draft == null) return
    const v = parseLimitK(draft)
    if (v === undefined) { setBad(true); return }
    setBad(false); setDraft(null); setPick(null)
    if (!(current === 'limit' && own != null && Math.abs(own * 60 - v) < 1)) onLimit?.('limit', v)
  }
  return h('div', { className: 'hermes-tp-limitctl', 'data-slot': 'limit-control' },
    h('div', { className: 'hermes-tp-seg', role: 'group', 'aria-label': 'What limits token spend' },
      choices.map(([c, label]) => h('button', {
        key: c, type: 'button', 'data-choice': c, className: c === shown ? 'is-on' : '', 'aria-pressed': c === shown,
        title: CHOICE_TIP[c], disabled: !onLimit || busy, onClick: () => choose(c)
      }, label))),
    shown === 'limit' ? h('label', { className: 'hermes-tp-limit' },
      h('input', {
        type: 'text', inputMode: 'decimal', size: 6, value, placeholder: current === 'limit' && own == null ? 'above budget' : 'e.g. 150',
        disabled: !onLimit || busy, 'aria-label': 'Limit, thousands of tokens per minute', 'aria-invalid': bad ? 'true' : undefined,
        onChange: (e) => setDraft(e.target.value),
        onKeyDown: (e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { setDraft(null); setPick(null); setBad(false) } }
      }), h('span', { className: 'hermes-tp-lbl' }, 'k/min'),
      draft != null ? h('button', { type: 'button', className: 'hermes-tp-btn', onClick: commit }, 'Set') : null) : null,
    bad ? h('span', { className: 'hermes-tp-warn', role: 'alert' }, 'e.g. 150 or 1.5m') : null)
}

// Design C: three tiles (spending now, the limit, card slots), the controls (choice, field, raise) and one row per
// subscription with a block per account.
export function TokenTiles({ throttle, unit, onLimit, busy = false, error, budget, pick, setPick, onBurst, onEndBurst, burstError, now = Date.now() / 1000 }) {
  if (!throttle) return null
  const t = tiles(throttle, unit)
  const tile = (key, label, big, of, note, extra = {}) => h('div', { key, className: 'hermes-tp-tile', 'data-tile': key, ...extra },
    h('div', { className: 'hermes-tp-tlabel' }, label),
    h('div', { className: 'hermes-tp-tbig' }, big, of ? h('span', { className: 'hermes-tp-tof' }, ` / ${of}`) : null),
    h('div', { className: 'hermes-tp-tnote' }, note))
  const scope = scopeText(throttle)
  return h('section', { className: 'hermes-tp-tiles-wrap', 'data-slot': 'spend', 'data-state': t.state },
    h('div', { className: 'hermes-tp-tiles' },
      tile('rate', 'Spending now', fmt(t.rate), null, `tokens / ${unit === 'm' ? 'min' : unit === 's' ? 'sec' : 'hour'}`),
      tile('limit', t.limitLabel, t.limitBig, null, t.limitNote, { title: scope ? `${scope}\n${scopeTitle(throttle)}` : undefined }),
      t.slots ? tile('slots', 'Card slots', t.slots.big, t.slots.of, t.slots.note) : null),
    h('div', { className: 'hermes-tp-ctlrow' },
      h(LimitControl, { key: t.choice, throttle, onLimit, busy }),
      onBurst ? h(BurstControl, { throttle, budget, pick, setPick, onStart: onBurst, onEnd: onEndBurst, error: burstError, now }) : null),
    error ? h('div', { className: 'hermes-tp-note', role: 'alert' }, error.startsWith('partly saved') ? `Limit ${error}` : `Limit not saved: ${error}`) : null,
    (throttle.pools || []).length ? h('div', { className: 'hermes-tp-accts', role: 'list', 'aria-label': 'Subscriptions' },
      throttle.pools.map((pool) => {
        const r = accountRow(pool, throttle, now)
        const blocks = r.total == null ? [] : Array.from({ length: Math.min(r.total, 24) }, (_, k) => k < r.usable)
        return h('div', { key: r.provider, role: 'listitem', className: `hermes-tp-acct is-${r.state}`, 'data-pool': r.provider, 'data-pool-state': r.state, title: r.title },
          h('span', { className: 'hermes-tp-aname' }, r.name),
          h('span', { className: 'hermes-tp-blocks', 'aria-hidden': true },
            blocks.map((on, k) => h('span', { key: k, className: on ? 'is-on' : '' }))),
          h('span', { className: 'hermes-tp-aleft' }, r.text))
      })) : null)
}

// `payload` is the selected range's data, or null while it loads / after it failed (`status` says which); the
// previous range's data is never drawn under the new range's label. `frame` (the last good payload, any range)
// only supplies the gateway list for the legend, so the controls stay usable meanwhile.
export function Panel({ payload, frame = payload, status = null, modelPayload = null, modelFrame = null, modelStatus = null, modelFilter = { solo: null, hidden: [] }, setModelFilter, view, setView, filter, setFilter, onHoverRange, throttle = null, onLimit = null, limitBusy = false, throttleError = null, budget = null, burstPick = DEFAULT_BURST, setBurstPick = () => {}, onBurst = null, onEndBurst = null, burstError = null }) {
  const colors = useMemo(() => colorFor(frame.gateways), [frame.gateways])
  const gws = visibleGateways(frame.gateways, filter)
  const models = modelFrame?.models || []
  const modelColors = useMemo(() => colorFor(models), [modelFrame?.models])
  const modelGws = visibleGateways(models, modelFilter)
  const set = (k) => (v) => setView({ ...view, [k]: v })
  // The limit counts in+out tokens over every gateway: it is only comparable with the unfiltered in+out chart.
  const filtered = view.by === 'model' ? !!modelFilter.solo || modelFilter.hidden.length > 0 : !!filter.solo || filter.hidden.length > 0
  const cap = view.cache || filtered ? null : capRate(throttle, view.unit)
  return h('div', { className: 'hermes-tp' },
    h('div', { className: 'hermes-tp-top' },
      h('h2', null, 'Throughput'),
      h('div', { className: 'hermes-tp-ctl' },
        h(Seg, { label: 'Token grouping', value: view.by || 'gateway', options: GROUPS, onPick: set('by') }),
        h(Seg, { label: 'Unit', value: view.unit, options: UNITS, onPick: set('unit'), render: (u) => '/' + u }),
        h(Seg, { label: 'Range', value: view.range, options: RANGES, onPick: set('range'), onHover: onHoverRange }),
        h(Seg, { label: 'Tokens', value: view.cache, options: [false, true], onPick: set('cache'), render: (c) => (c ? '+cache' : 'in+out') }))),
    throttle ? h(TokenTiles, { throttle, unit: view.unit, onLimit, busy: limitBusy, error: throttleError, budget, pick: burstPick, setPick: setBurstPick, onBurst, onEndBurst, burstError }) : null,
    h('div', { className: 'hermes-tp-legend' },
      h('button', { type: 'button', 'data-gw': 'all', className: !filter.solo && !filter.hidden.length ? 'is-solo' : '', onClick: () => setFilter(legendClick(filter, 'all')) }, 'All gateways'),
      frame.gateways.map((g) => h('button', {
        key: g, type: 'button', 'data-gw': g,
        className: [filter.hidden.includes(g) ? 'is-off' : '', filter.solo === g ? 'is-solo' : ''].join(' ').trim(),
        onClick: (e) => setFilter(legendClick(filter, g, e.shiftKey))
      }, h('span', { className: 'hermes-tp-sw', style: { background: colors[g] } }), g)),
      h('span', { className: 'hermes-tp-hint' }, 'click = only \u00b7 \u21e7click = hide')),
    ...(payload ? [
    ...(view.by === 'model' ? [
      h('div', { className: 'hermes-tp-legend', key: 'models', 'aria-label': 'Models' },
        h('button', { type: 'button', onClick: () => setModelFilter(legendClick(modelFilter, 'all')) }, 'All models'),
        models.map((m) => h('button', { key: m, type: 'button', 'data-model': m,
          className: [modelFilter.hidden.includes(m) ? 'is-off' : '', modelFilter.solo === m ? 'is-solo' : ''].join(' ').trim(),
          onClick: (e) => setModelFilter(legendClick(modelFilter, m, e.shiftKey))
        }, h('span', { className: 'hermes-tp-sw', style: { background: modelColors[m] } }), m))),
      modelPayload ? h(Chart, { key: 'tokens-model', id: 'tokens', title: 'Tokens by model', up: 'tokens', upLabel: view.cache ? 'in+out+cache' : 'in+out', payload: modelPayload, view, gws: modelGws, colors: modelColors, cap })
        : h('div', { key: 'model-pending', className: 'hermes-tp-note hermes-tp-pending', role: 'status' }, modelStatus || `Loading models ${view.range}…`)
    ] : [h(Chart, { key: 'tokens', id: 'tokens', title: 'Tokens', up: 'tokens', upLabel: view.cache ? 'in+out+cache' : 'in+out', payload, view, gws, colors, cap })]),
    budget ? h(ForecastSection, { key: 'forecast', budget, throttle, pick: burstPick }) : null,
    h(Chart, { key: 'sessions', id: 'sessions', title: 'Sessions', up: 'sess_in', down: 'sess_out', upLabel: 'new', downLabel: 'ended', payload, view, gws, colors }),
    h(Chart, { key: 'cards', id: 'cards', title: 'Cards', up: 'cards_in', down: 'cards_out', upLabel: 'created', downLabel: 'done', payload, view, gws, colors }),
    h('div', { className: 'hermes-tp-foot', key: 'foot' }, `Big numbers = average over the last ${nowWindow(view.range)} min; buckets of ${payload.bucket_s / 60} min.`)
    ] : [h('div', { className: 'hermes-tp-note hermes-tp-pending', key: 'pending', role: 'status' }, status || `Loading ${view.range}\u2026`)]))
}

function useVisible() {
  const [visible, setVisible] = useState(() => globalThis.document?.visibilityState !== 'hidden')
  useEffect(() => {
    const f = () => setVisible(document.visibilityState !== 'hidden')
    document.addEventListener('visibilitychange', f)
    return () => document.removeEventListener('visibilitychange', f)
  }, [])
  return visible
}

// The chip's right-click menu: what limits spend (Budget, Limit… opens the panel, No limit) and a raise of the budget
// (2× or 3× for an hour, or End while one runs). The same actions as the panel; nothing to show until the limit loads.
export function ChipMenu({ throttle, failed = false, busy = false, error = null, onLimit, onBurst, onEndBurst, openPanel }) {
  if (!throttle) return h(ContextMenuItem, { disabled: true, 'data-menu': 'loading' }, failed ? 'Limit unavailable' : 'Loading the limit\u2026')
  const choice = limitChoice(throttle)
  const now = Date.now() / 1000
  const burst = throttle.burst?.active ? throttle.burst : null
  const canRaise = throttle.mode === 'auto' && !pacingIsOff(throttle) && !!onBurst
  const own = typeof throttle.user_cap_per_s === 'number' ? ` (${fmt(throttle.user_cap_per_s * 60)}/min)` : ''
  return [
    error ? h(ContextMenuItem, { key: 'err', disabled: true, 'data-menu': 'error', className: 'hermes-tp-warn' }, `\u26a0 ${error}`) : null,
    budgetOffered(throttle) ? h(ContextMenuCheckboxItem, { key: 'budget', disabled: busy, checked: choice === 'budget', 'data-menu': 'budget', onSelect: () => choice !== 'budget' && onLimit?.('budget', null) }, 'Budget') : null,
    h(ContextMenuCheckboxItem, { key: 'limit', checked: choice === 'limit', 'data-menu': 'limit', onSelect: openPanel }, `Limit${choice === 'limit' ? own : ''}…`),
    h(ContextMenuCheckboxItem, { key: 'no_limit', disabled: busy, checked: choice === 'no_limit', 'data-menu': 'no_limit', onSelect: () => choice !== 'no_limit' && onLimit?.('no_limit', null) }, 'No limit'),
    burst || canRaise ? h(ContextMenuSeparator, { key: 'sep' }) : null,
    burst ? h(ContextMenuItem, { key: 'end', 'data-menu': 'end-raise', onSelect: () => onEndBurst?.() }, `End raise (${burstText(burst, now)})`) : null,
    !burst && canRaise ? h(ContextMenuItem, { key: 'r2', 'data-menu': 'raise-2', onSelect: () => onBurst({ factor: 2, hours: 1 }) }, 'Raise 2× for 1 h') : null,
    !burst && canRaise ? h(ContextMenuItem, { key: 'r3', 'data-menu': 'raise-3', onSelect: () => onBurst({ factor: 3, hours: 1 }) }, 'Raise 3× for 1 h') : null
  ]
}

export function Throughput({ ctx, placement = 'bar' }) {
  const [open, setOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [view, setViewState] = useState(loadView)
  const [filter, setFilter] = useState({ solo: null, hidden: [] })
  const [modelFilter, setModelFilter] = useState({ solo: null, hidden: [] })
  const setView = (v) => { setViewState(v); saveView(v) }
  const visible = useVisible()
  const fetchRange = (range, by = 'gateway') => async () => {
    const body = await ctx.rest(`/v1/series?range=${encodeURIComponent(range)}${by === 'model' ? '&by=model' : ''}`, { timeoutMs: REQUEST_TIMEOUT_MS })
    if (!validSeries(body, by)) throw Error('Invalid throughput response')
    return body
  }
  const client = useQueryClient()
  // Push: the gateway's feed id (from the watch answer) and when its last push arrived.
  const feed = useRef(null)
  // This window's views as the gateway names them; a push that leaves one out (the gateway keeps the 4 most recently
  // watched views, and another window or a hover prefetch may have pushed ours out) is not trusted, and we re-watch.
  const want = useRef([])
  want.current = [`${view.range}|gateway`, ...(view.by === 'model' ? [`${view.range}|model`] : [])]
  const rewatch = useRef(() => {})
  const [liveAt, setLiveAt] = useState(-Infinity)
  const [clock, setClock] = useState(() => Date.now())
  const live = clock - liveAt < PUSH_TRUST_MS
  const poll = (on) => (visible && on ? (live ? SAFETY_MS : POLL_MS) : false)
  useEffect(() => {
    const subscribe = typeof ctx?.onEvent === 'function' ? ctx.onEvent : null
    const off = subscribe ? subscribe(SNAPSHOT_EVENT, (ev) => {
      const p = ev?.payload
      if (!p?.feed || p.feed !== feed.current) return // another gateway's, or before our watch answered
      applySnapshot(client, p)
      if (!pushCovers(p, want.current)) { rewatch.current(); return }
      const t = Date.now()
      setLiveAt(t); setClock(t)
    }) : null
    return () => { if (typeof off === 'function') off() }
  }, [ctx, client])
  useEffect(() => { // re-evaluate liveness while shown (so polling resumes when pushes stop)
    if (!visible) return undefined
    setClock(Date.now()) // shown again: judge liveness now, not at the next tick
    const t = setInterval(() => setClock(Date.now()), 15000)
    return () => clearInterval(t)
  }, [visible])
  const advisory = open || menuOpen
  useEffect(() => { // register this window's views; renewed every SAFETY_MS while shown
    if (!visible || typeof ctx?.onEvent !== 'function') return undefined // no event stream: pushes cannot reach us
    let gone = false
    const watch = async () => {
      const series = [{ range: view.range, by: 'gateway' }, ...(view.by === 'model' ? [{ range: view.range, by: 'model' }] : [])]
      try {
        const body = await ctx.rest('/v1/watch', { method: 'POST', body: { series, advisory }, timeoutMs: REQUEST_TIMEOUT_MS })
        if (gone || typeof body?.feed !== 'string') return
        feed.current = body.feed
        applySnapshot(client, body)
      } catch { /* an older gateway without /v1/watch: the polls below carry on */ }
    }
    let lastAt = -Infinity
    rewatch.current = () => { // at most every REWATCH_MS: two windows with different views must not ping-pong
      if (Date.now() - lastAt < REWATCH_MS) return
      lastAt = Date.now(); void watch()
    }
    rewatch.current()
    const t = setInterval(() => { lastAt = Date.now(); void watch() }, SAFETY_MS)
    return () => { gone = true; clearInterval(t); rewatch.current = () => {} }
  }, [ctx, client, visible, view.range, view.by, advisory])
  // Hovering a range button fetches it ahead of the click, so switching is usually instant.
  const prefetch = (range) => {
    if (range !== view.range) {
      client.prefetchQuery({ queryKey: ['throughput', 'series', range], queryFn: fetchRange(range), staleTime: 10000 }).catch(() => {})
      if (view.by === 'model') client.prefetchQuery({ queryKey: ['throughput', 'series', range, 'model'], queryFn: fetchRange(range, 'model'), staleTime: 10000 }).catch(() => {})
    }
  }
  const query = useQuery({
    queryKey: ['throughput', 'series', view.range],
    queryFn: fetchRange(view.range),
    refetchInterval: poll(true),
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    retry: false,
    staleTime: 10000,
    placeholderData: (prev) => prev
  })
  const modelQuery = useQuery({
    queryKey: ['throughput', 'series', view.range, 'model'],
    queryFn: fetchRange(view.range, 'model'),
    enabled: view.by === 'model',
    refetchInterval: poll(view.by === 'model'),
    refetchIntervalInBackground: false, refetchOnWindowFocus: false, retry: false, staleTime: 10000,
    placeholderData: (prev) => prev
  })
  const throttleQuery = useQuery({
    queryKey: ['throughput', 'throttle'],
    queryFn: async () => {
      const body = await ctx.rest('/v1/throttle', { timeoutMs: REQUEST_TIMEOUT_MS })
      if (!body || typeof body.percent !== 'number') throw Error('Invalid throttle response')
      return body
    },
    enabled: open || menuOpen, refetchInterval: poll(open || menuOpen),
    refetchIntervalInBackground: false, refetchOnWindowFocus: false, retry: false, staleTime: 5000
  })
  const [throttleError, setThrottleError] = useState(null)
  // Budget / Limit / No limit onto the backend: a limit is PUT /v1/throttle {cap_per_min}; Budget clears an own cap
  // and switches budget pacing on; No limit clears it and switches pacing off (PUT /v1/throttle/pacing).
  const putThrottle = async (path, body) => {
    const b = await ctx.rest(path, { method: 'PUT', body, timeoutMs: REQUEST_TIMEOUT_MS })
    if (!b || typeof b.percent !== 'number') throw Error('Invalid throttle response')
    client.setQueryData(['throughput', 'throttle'], b)
    return b
  }
  const [limitBusy, setLimitBusy] = useState(false)
  const busyRef = useRef(false)
  const onLimit = async (choice, capPerMin) => {
    if (busyRef.current) return // one change at a time: a second click waits for the first to land
    busyRef.current = true
    setLimitBusy(true)
    let done = []
    try {
      // Decide from the limit as it is now, not from a cached copy (the menu may open on an old one).
      let t = (await throttleQuery.refetch()).data || throttleQuery.data
      if (!t) throw Error('limit unavailable')
      const own = typeof t.user_cap_per_s === 'number' || (t.mode === 'manual' && typeof t.cap_per_s === 'number') || (t.percent ?? 0) >= 100
      const state = pacingState(t)
      const budgetHere = budgetOffered(t) || state === 'on' || state === 'pending'
      if (choice === 'limit') { t = await putThrottle('/v1/throttle', { cap_per_min: capPerMin }); done.push('limit set') }
      else {
        if (own) { t = await putThrottle('/v1/throttle', { cap_per_min: null }); done.push('your cap cleared') }
        const off = pacingIsOff(t)
        // Pacing is only switched where a budget exists: on a fleet without a publisher an Off would fake one.
        if (choice === 'budget' && off) t = await putThrottle('/v1/throttle/pacing', { on: true })
        if (choice === 'no_limit' && !off && budgetHere) t = await putThrottle('/v1/throttle/pacing', { on: false })
      }
      client.invalidateQueries({ queryKey: ['throughput', 'budget'] })
      setThrottleError(null)
    } catch (e) {
      const msg = String(e?.message || e).slice(0, 200)
      setThrottleError(done.length ? `partly saved (${done.join(', ')}); the rest failed: ${msg}` : msg)
    } finally {
      busyRef.current = false
      setLimitBusy(false)
    }
  }
  const [burstPick, setBurstPick] = useState(DEFAULT_BURST)
  const [burstError, setBurstError] = useState(null)
  const auto = throttleQuery.data?.mode === 'auto'
  // The charts (history, curve, events) and the cost of the picked burst are two requests: changing the pick asks
  // only `projection_only`, which reads no series on the server.
  const budgetQuery = useQuery({
    queryKey: ['throughput', 'budget'],
    queryFn: async () => {
      const body = await ctx.rest('/v1/budget', { timeoutMs: REQUEST_TIMEOUT_MS })
      if (!body || typeof body.pools !== 'object' || !body.history) throw Error('Invalid budget response')
      return body
    },
    enabled: open && !!throttleQuery.data, refetchInterval: visible && open ? POLL_MS : false,
    refetchIntervalInBackground: false, refetchOnWindowFocus: false, retry: false, staleTime: 10000
  })
  const projectionQuery = useQuery({
    queryKey: ['throughput', 'budget', 'projection', burstPick.factor, burstPick.hours],
    queryFn: async () => {
      const q = `?factor=${encodeURIComponent(burstPick.factor)}&hours=${encodeURIComponent(burstPick.hours)}&projection_only=1`
      const body = await ctx.rest(`/v1/budget${q}`, { timeoutMs: REQUEST_TIMEOUT_MS })
      if (!body || !body.projection) throw Error('Invalid projection response')
      return body
    },
    enabled: open && auto && !!budgetQuery.data, refetchInterval: visible && open && auto ? POLL_MS : false,
    refetchIntervalInBackground: false, refetchOnWindowFocus: false, retry: false, staleTime: 10000,
    placeholderData: (prev) => prev
  })
  const budget = budgetQuery.data ? { ...budgetQuery.data, projection: projectionQuery.data?.projection ?? null } : null
  const changeBurst = async (opts) => {
    try {
      const body = await ctx.rest('/v1/throttle/burst', { ...opts, timeoutMs: REQUEST_TIMEOUT_MS })
      if (!body || typeof body.percent !== 'number') throw Error('Invalid throttle response')
      client.setQueryData(['throughput', 'throttle'], body)
      client.invalidateQueries({ queryKey: ['throughput', 'budget'] })
      setBurstError(null)
    } catch (e) { setBurstError(String(e?.message || e).slice(0, 200)) }
  }
  const onBurst = (pick) => changeBurst({ method: 'PUT', body: { factor: pick.factor, hours: pick.hours } })
  const onEndBurst = () => changeBurst({ method: 'DELETE' })
  const lastModel = useRef(null)
  if (modelQuery.data) lastModel.current = modelQuery.data
  const modelPayload = modelQuery.data && !modelQuery.isPlaceholderData && modelQuery.data.range === view.range ? modelQuery.data : null
  const modelStatus = modelQuery.error
    ? `Models ${view.range} unavailable: ${String(modelQuery.error.message || modelQuery.error).slice(0, 200)}`
    : `Loading models ${view.range}…`
  useEffect(() => {
    const show = () => setOpen(true)
    window.addEventListener('hermes-show-throughput', show)
    return () => window.removeEventListener('hermes-show-throughput', show)
  }, [])
  // The chip and the legend keep the last good payload (any range) through a range switch or a failed poll;
  // the charts draw only the selected range's own data.
  const last = useRef(null)
  if (query.data) last.current = query.data
  const frame = last.current
  const payload = query.data && !query.isPlaceholderData && query.data.range === view.range ? query.data : null
  const status = payload ? null : query.error
    ? `${view.range} unavailable: ${String(query.error.message || query.error).slice(0, 200)}`
    : `Loading ${view.range}\u2026`
  const n = frame ? chipNumbers(frame, view) : null
  const label = n ? `${fmt(n.tok)} tok/${view.unit} \u00b7 ${fmt(n.cards)} cards/${view.unit}` : 'Throughput \u2026'
  // In the titlebar the chip sits inside the titlebar tool's own <button>: render a span trigger, not a nested button.
  const inTitlebar = placement === 'titlebar'
  const chip = h(inTitlebar ? 'span' : 'button', { ...(inTitlebar ? {} : { type: 'button' }), className: inTitlebar ? TB_CHIP : BAR, 'data-slot': 'throughput-chip', 'aria-label': `Show throughput: ${label}`, title: 'Throughput: tokens and cards done per ' + { s: 'second', m: 'minute', h: 'hour' }[view.unit] + ' (rate now) \u00b7 right-click: limit and raise' },
    n ? h(Spark, { values: n.spark }) : null,
    h('span', { className: 'hermes-tp-chiptext' }, label))
  return h(Popover, { open, onOpenChange: setOpen },
    h(ContextMenu, { onOpenChange: setMenuOpen },
      h(ContextMenuTrigger, { asChild: true }, h(PopoverTrigger, { asChild: true }, chip)),
      h(ContextMenuContent, { className: 'hermes-tp-menu', 'data-slot': 'throughput-menu' },
        h(ChipMenu, { throttle: throttleQuery.data || null, failed: throttleQuery.isError && !throttleQuery.data, busy: limitBusy, error: throttleError || burstError, onLimit, onBurst, onEndBurst, openPanel: () => setOpen(true) }))),
    h(PopoverContent, {
      side: inTitlebar ? 'bottom' : 'top', align: 'end', sticky: 'always', 'aria-label': 'Throughput', className: 'hermes-tp-popover',
      style: { width: 'min(680px, calc(100vw - 24px))', maxHeight: '80vh', overflowY: 'auto' }
    }, frame
      ? h(Panel, { payload, frame, status, modelPayload, modelFrame: lastModel.current, modelStatus, modelFilter, setModelFilter, view, setView, filter, setFilter, onHoverRange: prefetch, throttle: throttleQuery.data || null, onLimit, limitBusy, throttleError, budget, burstPick, setBurstPick, onBurst, onEndBurst, burstError })
      : h('div', { className: 'hermes-tp-note' }, query.error ? `Unavailable: ${String(query.error.message || query.error).slice(0, 200)}` : 'Loading\u2026')))
}

export const CSS = `
.hermes-tp-spark{color:var(--ui-green,#3ddc97);flex-shrink:0}
.hermes-tp-chiptext{white-space:nowrap;font-variant-numeric:tabular-nums}
.hermes-tp-tool{padding:0!important}
.hermes-tp-tbchip{display:inline-flex;align-items:center;justify-content:flex-end;gap:6px;width:100%;height:100%;padding:0 6px;font-size:11px;color:var(--ui-text-tertiary);overflow:hidden}
.hermes-tp-tbchip .hermes-tp-chiptext{overflow:hidden;text-overflow:ellipsis}
.hermes-tp-tool:hover .hermes-tp-tbchip{color:var(--foreground)}
.hermes-tp{display:flex;flex-direction:column;gap:6px;padding:6px 4px;font-size:12px;color:var(--ui-text-primary,inherit)}
.hermes-tp-top{display:flex;align-items:center;gap:8px}
.hermes-tp-top h2{font-size:13px;font-weight:600;margin:0}
.hermes-tp-ctl{margin-left:auto;display:flex;gap:8px}
.hermes-tp-seg{display:inline-flex;border:1px solid var(--ui-stroke-secondary);border-radius:5px;overflow:hidden}
.hermes-tp-seg button{padding:1px 7px;font:inherit;font-size:11px;background:transparent;border:0;color:var(--ui-text-secondary);cursor:pointer}
.hermes-tp-seg button:hover{background:var(--chrome-action-hover)}
.hermes-tp-seg button.is-on{background:var(--ui-stroke-secondary);color:var(--ui-text-primary,inherit)}
.hermes-tp-legend{display:flex;flex-wrap:wrap;align-items:center;gap:2px 10px;font-size:11px}
.hermes-tp-legend button{display:inline-flex;align-items:center;gap:4px;padding:0;border:0;background:transparent;font:inherit;color:var(--ui-text-secondary);cursor:pointer}
.hermes-tp-legend button:hover,.hermes-tp-legend button.is-solo{color:var(--ui-text-primary,inherit)}
.hermes-tp-legend button.is-off{opacity:.35}
.hermes-tp-hint{margin-left:auto;color:var(--ui-text-tertiary)}
.hermes-tp-sw{display:inline-block;width:8px;height:8px;border-radius:2px;flex-shrink:0}
.hermes-tp-chart{margin-top:8px}
.hermes-tp-chead{display:flex;align-items:baseline;gap:14px}
.hermes-tp-chead h3{font-size:12.5px;font-weight:600;margin:0;min-width:64px}
.hermes-tp-now{display:inline-flex;align-items:baseline;gap:4px}
.hermes-tp-big{font-size:17px;font-weight:600;font-variant-numeric:tabular-nums}
.hermes-tp-big.is-down{color:var(--ui-text-secondary)}
.hermes-tp-pending{padding:24px 0;text-align:center}
.hermes-tp-lbl,.hermes-tp-foot,.hermes-tp-note{font-size:11px;color:var(--ui-text-tertiary)}
.hermes-tp-info{margin-left:auto;position:relative;box-sizing:border-box;width:13px;height:13px;border:1px solid var(--ui-text-tertiary);border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font:italic 600 9px/1 Georgia,serif;color:var(--ui-text-secondary);cursor:help;align-self:center}
.hermes-tp-info:hover::after,.hermes-tp-info:focus-visible::after{content:attr(data-tip);position:absolute;right:-2px;top:calc(100% + 5px);z-index:5;padding:4px 8px;border:1px solid var(--ui-stroke-secondary);border-radius:5px;background:var(--ui-bg,#1b1b1f);color:var(--ui-text-primary,inherit);font:normal 400 11px/1.4 system-ui,sans-serif;white-space:normal;width:max-content;max-width:min(360px,calc(100vw - 32px));box-shadow:0 2px 8px rgba(0,0,0,.35);pointer-events:none}
.hermes-tp-info:hover,.hermes-tp-info:focus-visible{border-color:var(--ui-text-secondary);color:inherit;outline:none}
.hermes-tp-plot{position:relative;margin-top:2px}
.hermes-tp-plot svg{display:block;width:100%}
.hermes-tp-axis{fill:var(--ui-text-tertiary);font-size:10px}
.hermes-tp-ticks{display:flex;justify-content:space-between;font-size:10px;color:var(--ui-text-tertiary);font-variant-numeric:tabular-nums;margin-top:-12px;pointer-events:none}
.hermes-tp-tip{position:absolute;z-index:4;pointer-events:none;min-width:150px;max-width:240px;padding:6px 8px;border:1px solid var(--ui-stroke-secondary);border-radius:5px;background:var(--ui-bg,#1b1b1f);font-size:11.5px;line-height:1.45;box-shadow:0 2px 8px rgba(0,0,0,.35)}
.hermes-tp-tiptime{color:var(--ui-text-tertiary);font-variant-numeric:tabular-nums}
.hermes-tp-tiphead{font-weight:600}
.hermes-tp-tiphead.is-down{margin-top:4px}
.hermes-tp-tiprow{display:flex;align-items:center;gap:5px}
.hermes-tp-tiprow.is-missing{color:var(--ui-text-tertiary);align-items:flex-start}
.hermes-tp-num{margin-left:auto;padding-left:12px;font-variant-numeric:tabular-nums}
.hermes-tp-capl{fill:#ef4444}
.hermes-tp-trow{display:flex;align-items:center;gap:8px}
.hermes-tp-ttext{font-variant-numeric:tabular-nums}
.hermes-tp-tiles-wrap{display:flex;flex-direction:column;gap:12px;margin:4px 0 14px}
.hermes-tp-tiles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.hermes-tp-tile{background:var(--ui-surface-secondary,rgba(127,127,127,.08));border-radius:10px;padding:12px 14px;min-width:0}
.hermes-tp-tiles-wrap[data-state=at] [data-tile=limit]{box-shadow:inset 0 0 0 1px var(--ui-orange,#d9822b)}
.hermes-tp-tiles-wrap[data-state=holding] [data-tile=limit]{box-shadow:inset 0 0 0 1px var(--ui-red,#dc3545)}
.hermes-tp-tlabel{font-size:11px;color:var(--ui-text-secondary)}
.hermes-tp-tbig{font-size:24px;font-weight:600;margin-top:4px;font-variant-numeric:tabular-nums;line-height:1.15}
.hermes-tp-tiles-wrap[data-state=at] [data-tile=limit] .hermes-tp-tbig{color:var(--ui-orange,#d9822b)}
.hermes-tp-tof{font-size:14px;font-weight:500;color:var(--ui-text-secondary)}
.hermes-tp-tnote{font-size:11px;color:var(--ui-text-secondary)}
.hermes-tp-ctlrow{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.hermes-tp-limitctl{display:flex;align-items:center;gap:8px}
.hermes-tp-limit{display:inline-flex;align-items:center;gap:5px}
.hermes-tp-limit input{width:64px;font:inherit;font-size:12px;padding:3px 6px;border:1px solid var(--ui-stroke-secondary);border-radius:6px;background:transparent;color:inherit}
.hermes-tp-limit input[aria-invalid=true]{border-color:var(--ui-red,#dc3545)}
.hermes-tp-burst{display:flex;flex-direction:column;align-items:flex-end;gap:4px;margin-left:auto}
.hermes-tp-brow{display:flex;align-items:center;gap:6px;white-space:nowrap}
.hermes-tp-btn.is-quiet{border-color:transparent}
.hermes-tp-wline{font-size:11px}
.hermes-tp-accts{display:flex;flex-direction:column;gap:7px}
.hermes-tp-acct{display:grid;grid-template-columns:56px minmax(0,1fr) 108px;align-items:center;gap:12px;font-size:12px}
.hermes-tp-aname{font-weight:600}
.hermes-tp-blocks{display:flex;gap:3px}
.hermes-tp-blocks>span{flex:1;height:9px;border-radius:2px;background:var(--ui-stroke-secondary,#2f3440)}
.hermes-tp-blocks>span.is-on{background:var(--ui-blue,#7aa2ff)}
.hermes-tp-acct.is-tight .hermes-tp-blocks>span.is-on,.hermes-tp-acct.is-paced .hermes-tp-blocks>span.is-on{background:var(--ui-orange,#d9822b)}
.hermes-tp-aleft{text-align:right;color:var(--ui-text-secondary);font-variant-numeric:tabular-nums}
.hermes-tp-acct.is-tight .hermes-tp-aleft,.hermes-tp-acct.is-paced .hermes-tp-aleft{color:var(--ui-orange,#d9822b)}
.hermes-tp-acct.is-used .hermes-tp-aleft,.hermes-tp-acct.is-held .hermes-tp-aleft{color:var(--ui-red,#dc3545)}
.hermes-tp-budget>summary{display:flex;align-items:center;gap:8px;cursor:pointer;list-style:none}
.hermes-tp-budget>summary::before{content:'▸';font-size:10px;color:var(--ui-text-tertiary)}
.hermes-tp-budget[open]>summary::before{content:'▾'}
.hermes-tp-budget>summary h3{margin:0}
.hermes-tp-runway{color:var(--ui-text-secondary);font-variant-numeric:tabular-nums}
.hermes-tp-budget{margin-top:10px;display:flex;flex-direction:column;gap:4px}
.hermes-tp-bchart{margin-top:4px}
.hermes-tp-blegend{margin-left:auto;display:flex;flex-wrap:wrap;gap:2px 10px;font-size:10.5px;color:var(--ui-text-tertiary)}
.hermes-tp-bkey{display:inline-flex;align-items:center;gap:4px}
.hermes-tp-solid,.hermes-tp-dash{display:inline-block;width:12px;height:0;border-top:2px solid}
.hermes-tp-dash{border-top-style:dashed}
.hermes-tp-tick{display:inline-block;width:0;height:8px;border-left:2px solid}
.hermes-tp-burst{display:flex;flex-direction:column;gap:2px;font-size:11px}
.hermes-tp-btn{padding:1px 8px;font:inherit;font-size:11px;border:1px solid var(--ui-stroke-secondary);border-radius:5px;background:transparent;color:var(--ui-text-primary,inherit);cursor:pointer}
.hermes-tp-btn:hover:not(:disabled){background:var(--chrome-action-hover)}
.hermes-tp-btn:disabled{opacity:.5;cursor:default}
.hermes-tp-warn{color:var(--ui-orange,#c2410c)}
`

// Additive plugin-owned area protocol, shared with lanes; no dependency on it.
export function stripArea() {
  const key = Symbol.for('hermes.lanes.stripRight.v1')
  return globalThis[key] ||= { hosts: new Set(), item: null, listeners: new Set(),
    notify() { for (const fn of [...this.listeners]) fn() } }
}

// The lease is released only after the old placement has committed its unmount.
// Two independently committed roots must never each run Throughput's subscriptions.
export function ExclusiveChip({ ctx, component = Throughput }) {
  const area = stripArea(), token = useRef({}).current
  const [owned, setOwned] = useState(false)
  useEffect(() => {
    const acquire = () => {
      if (!area.lease) { area.lease = token; setOwned(true) }
    }
    area.listeners.add(acquire); acquire()
    return () => {
      area.listeners.delete(acquire)
      if (area.lease === token) { area.lease = null; area.notify() }
    }
  }, [area, token])
  return owned ? h(component, { ctx }) : null
}

export function ChipPlacement({ ctx, component }) {
  const area = stripArea()
  const [, update] = useState(0)
  useEffect(() => {
    const changed = () => update((n) => n + 1)
    area.listeners.add(changed)
    return () => area.listeners.delete(changed)
  }, [area])
  return area.hosts.size ? null : h(ExclusiveChip, { ctx, component })
}

export function registerStripChip(ctx, component = Throughput) {
  const area = stripArea()
  const item = { render: () => h(ExclusiveChip, { ctx, component }) }
  area.item = item; area.notify()
  return () => { if (area.item === item) { area.item = null; area.notify() } }
}

// Titlebar placement: hosts the chip (so the status-bar fallback and the lanes strip stand down) and takes the
// single-subscription lease like any other placement.
export function TitlebarHost({ ctx, component = Throughput }) {
  const area = stripArea(), token = useRef({}).current
  useEffect(() => {
    area.hosts.add(token); area.notify()
    return () => { area.hosts.delete(token); area.notify() }
  }, [area, token])
  return h(ExclusiveChip, { ctx, component: component === Throughput ? TitlebarThroughput : component })
}
const TitlebarThroughput = ({ ctx }) => h(Throughput, { ctx, placement: 'titlebar' })

export function titlebarTool(ctx, component) {
  return {
    id: 'throughput',
    label: 'Throughput',
    title: 'Throughput: tokens and cards done (rate now). Click for the panel; right-click: limit and raise',
    span: TITLEBAR_SPAN,
    className: 'hermes-tp-tool',
    icon: h(TitlebarHost, { ctx, component }),
    // Mouse clicks reach the span's own popover trigger; Enter/Space on the focused button arrive with detail 0.
    onSelect: (event) => { if (!event || event.detail === 0) window.dispatchEvent(new window.Event(SHOW_EVENT)) }
  }
}

export default {
  id: 'throughput',
  name: 'Throughput',
  description: 'Titlebar chip + panel: tokens, sessions and cards per gateway over time.',
  register(ctx) {
    const style = document.createElement('style')
    style.textContent = CSS + `.hermes-tp-tool{width:calc(${TITLEBAR_SPAN}*var(--titlebar-control-size,24px))!important}`
    document.head.append(style)
    ctx.onDispose(() => style.remove())
    // Titlebar, left of the llm-split chip; the status bar keeps a fallback for when no titlebar host is mounted.
    ctx.register({ id: 'titlebar-chip', area: 'titleBar.tools.right', order: 40, data: titlebarTool(ctx) })
    ctx.register({ id: 'chip', area: STATUSBAR_AREAS.right, order: 7, render: () => h(ChipPlacement, { ctx }) })
    ctx.register({
      id: 'show-throughput',
      area: PALETTE_AREA,
      data: {
        id: 'show-throughput', label: 'Show throughput', keywords: ['throughput', 'tokens', 'cards', 'sessions', 'rate'],
        run: () => window.dispatchEvent(new window.Event('hermes-show-throughput'))
      }
    })
  }
}

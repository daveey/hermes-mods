// Throughput: tokens, sessions and cards per gateway over time (direction B "Flow").
// Chip: 34 px token sparkline + "56k tok/m · 1.5 cards/m". Popover: three mirrored stacked charts
// (inflow above the line, outflow below), stacked by gateway. Data: GET /v1/series?range=… on the
// local gateway (plugins/throughput/series.py): per-bucket counts, null = gateway doesn't report.
import { useQuery, useQueryClient, STATUSBAR_AREAS, PALETTE_AREA, Popover, PopoverTrigger, PopoverContent } from '@hermes/plugin-sdk'
import { createElement as h, useState, useEffect, useMemo, useRef } from 'react'

export const POLL_MS = 30000
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

// ── pure helpers (exported for tests) ─────────────────────────────────────

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
function Chart({ id, title, up, down, upLabel, downLabel, payload, view, gws, colors }) {
  const [hover, setHover] = useState(null)
  const U = layers(payload, up, gws, view)
  const D = down ? layers(payload, down, gws, view) : null
  const H = down ? 120 : 84
  const mid = down ? H * 0.55 : H - 14
  const len = Math.max(U.len, D?.len ?? 0, 2)
  const mx = Math.max(...U.total.map((x) => x || 0), ...(D ? D.total.map((x) => x || 0) : [0]), 1e-9)
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
        D ? tipRows(D, hover.i) : null) : null))
}

// `payload` is the selected range's data, or null while it loads / after it failed (`status` says which); the
// previous range's data is never drawn under the new range's label. `frame` (the last good payload, any range)
// only supplies the gateway list for the legend, so the controls stay usable meanwhile.
export function Panel({ payload, frame = payload, status = null, modelPayload = null, modelFrame = null, modelStatus = null, modelFilter = { solo: null, hidden: [] }, setModelFilter, view, setView, filter, setFilter, onHoverRange }) {
  const colors = useMemo(() => colorFor(frame.gateways), [frame.gateways])
  const gws = visibleGateways(frame.gateways, filter)
  const models = modelFrame?.models || []
  const modelColors = useMemo(() => colorFor(models), [modelFrame?.models])
  const modelGws = visibleGateways(models, modelFilter)
  const set = (k) => (v) => setView({ ...view, [k]: v })
  return h('div', { className: 'hermes-tp' },
    h('div', { className: 'hermes-tp-top' },
      h('h2', null, 'Throughput'),
      h('div', { className: 'hermes-tp-ctl' },
        h(Seg, { label: 'Token grouping', value: view.by || 'gateway', options: GROUPS, onPick: set('by') }),
        h(Seg, { label: 'Unit', value: view.unit, options: UNITS, onPick: set('unit'), render: (u) => '/' + u }),
        h(Seg, { label: 'Range', value: view.range, options: RANGES, onPick: set('range'), onHover: onHoverRange }),
        h(Seg, { label: 'Tokens', value: view.cache, options: [false, true], onPick: set('cache'), render: (c) => (c ? '+cache' : 'in+out') }))),
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
      modelPayload ? h(Chart, { key: 'tokens-model', id: 'tokens', title: 'Tokens by model', up: 'tokens', upLabel: view.cache ? 'in+out+cache' : 'in+out', payload: modelPayload, view, gws: modelGws, colors: modelColors })
        : h('div', { key: 'model-pending', className: 'hermes-tp-note hermes-tp-pending', role: 'status' }, modelStatus || `Loading models ${view.range}…`)
    ] : [h(Chart, { key: 'tokens', id: 'tokens', title: 'Tokens', up: 'tokens', upLabel: view.cache ? 'in+out+cache' : 'in+out', payload, view, gws, colors })]),
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

export function Throughput({ ctx }) {
  const [open, setOpen] = useState(false)
  const [view, setViewState] = useState(loadView)
  const [filter, setFilter] = useState({ solo: null, hidden: [] })
  const [modelFilter, setModelFilter] = useState({ solo: null, hidden: [] })
  const setView = (v) => { setViewState(v); saveView(v) }
  const visible = useVisible()
  const fetchRange = (range, by = 'gateway') => async () => {
    const body = await ctx.rest(`/v1/series?range=${encodeURIComponent(range)}${by === 'model' ? '&by=model' : ''}`, { timeoutMs: REQUEST_TIMEOUT_MS })
    if (!body || !Array.isArray(body.gateways) || (by === 'model' && !Array.isArray(body.models)) || !body.series || !body.bucket_s) throw Error('Invalid throughput response')
    return body
  }
  const client = useQueryClient()
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
    refetchInterval: visible ? POLL_MS : false,
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
    refetchInterval: visible && view.by === 'model' ? POLL_MS : false,
    refetchIntervalInBackground: false, refetchOnWindowFocus: false, retry: false, staleTime: 10000,
    placeholderData: (prev) => prev
  })
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
  return h(Popover, { open, onOpenChange: setOpen },
    h(PopoverTrigger, { asChild: true },
      h('button', { type: 'button', className: BAR, 'data-slot': 'throughput-chip', 'aria-label': `Show throughput: ${label}`, title: 'Throughput: tokens and cards done per ' + { s: 'second', m: 'minute', h: 'hour' }[view.unit] + ' (rate now)' },
        n ? h(Spark, { values: n.spark }) : null,
        h('span', { className: 'hermes-tp-chiptext' }, label))),
    h(PopoverContent, {
      side: 'top', align: 'end', sticky: 'always', 'aria-label': 'Throughput', className: 'hermes-tp-popover',
      style: { width: 'min(680px, calc(100vw - 24px))', maxHeight: '80vh', overflowY: 'auto' }
    }, frame
      ? h(Panel, { payload, frame, status, modelPayload, modelFrame: lastModel.current, modelStatus, modelFilter, setModelFilter, view, setView, filter, setFilter, onHoverRange: prefetch })
      : h('div', { className: 'hermes-tp-note' }, query.error ? `Unavailable: ${String(query.error.message || query.error).slice(0, 200)}` : 'Loading\u2026')))
}

export const CSS = `
.hermes-tp-spark{color:var(--ui-green,#3ddc97);flex-shrink:0}
.hermes-tp-chiptext{white-space:nowrap;font-variant-numeric:tabular-nums}
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
`

export default {
  id: 'throughput',
  name: 'Throughput',
  description: 'Status-bar chip + panel: tokens, sessions and cards per gateway over time.',
  register(ctx) {
    const style = document.createElement('style')
    style.textContent = CSS
    document.head.append(style)
    ctx.onDispose(() => style.remove())
    ctx.register({ id: 'chip', area: STATUSBAR_AREAS.right, order: 7, render: () => h(Throughput, { ctx }) })
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

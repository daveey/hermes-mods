/**
 * Hub Status — status-bar chip: is the hub up, restarting or unreachable, and for how long.
 *
 *   ▮▮▮▮▮▮▮▮▮▮▮▮ Hub 6h 36m     up: time since the hub gateway process started
 *   ▮▮▮▮▮▮▮▮▮▮▮▮ Hub 0:42       restarting (yellow): time since the restart began
 *   ▮▮▮▮▮▮▮▮▮▮▮▮ Hub 3m         down / unreachable (red): time since it was last seen up
 *
 * The strip is the last 24 h in 2 h slots, each painted with the worst state seen in it.
 * Click → popover: a 24 h band (or 7 day rows), legend, and the list of restarts and outages.
 *
 * Data: GET /api/plugins/hub-status/{status,events} on the hub connection (plugins/hub-status: the
 * hub's always-on dashboard backend, which keeps answering while the gateway restarts). When the
 * hub can't be reached at all (two failed polls in a row) the chip goes red "unreachable", timed
 * from the last good poll; those spans are kept in plugin storage (30 days) and merged into the
 * timeline, since the hub can't log its own unreachability.
 *
 * Route: the gateway registered as `hermes-hub` (else the first non-local gateway whose id/label
 * says "hub"). No hub connection → no chip.
 */

import { atom, cn, host, Popover, PopoverContent, PopoverTrigger, STATUSBAR_AREAS, useValue } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'hub-status'
export const POLL_MS = 10_000
export const EVENTS_POLL_MS = 60_000
export const FAILS_FOR_UNREACHABLE = 2
export const SLOTS = 12
export const DAY_MS = 86_400_000
export const RETAIN_MS = 30 * DAY_MS
const UP = 'up'
const RESTARTING = 'restarting'
const DOWN = 'down'
const NONE = 'none'
const FUTURE = 'future'
const RANK = { [NONE]: 0, [UP]: 1, [RESTARTING]: 2, [DOWN]: 3 }
const COLOR = { [UP]: 'var(--hs-g)', [RESTARTING]: 'var(--hs-y)', [DOWN]: 'var(--hs-r)', [NONE]: 'var(--hs-n)' }
const WORD = { [UP]: 'up', [RESTARTING]: 'restarting', [DOWN]: 'unreachable' }

const BAR_CLASS =
  'inline-flex h-full items-center gap-1.5 rounded-none px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground'

const CSS = `
.hs-root{--hs-g:#3fb950;--hs-y:#e3b341;--hs-r:#f85149;--hs-n:color-mix(in srgb,currentColor 22%,transparent)}
.hs-strip{display:inline-flex;gap:1px;align-items:center;height:9px}
.hs-strip i{width:3px;height:9px;border-radius:1px;opacity:.85}
.hs-dur{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:10.5px;font-variant-numeric:tabular-nums}
.hs-pop{font-size:12px}
.hs-hd{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.hs-hd b{font-size:13px;font-weight:600}
.hs-sub{color:var(--muted-foreground);font-size:11px}
.hs-seg{margin-left:auto;display:inline-flex;border:1px solid var(--border);border-radius:6px;overflow:hidden;font-size:11px}
.hs-seg button{padding:1px 8px;color:var(--muted-foreground);background:transparent;border:0;cursor:pointer;font:inherit}
.hs-seg button[aria-pressed=true]{background:var(--border);color:var(--foreground)}
.hs-band{display:flex;height:16px;border-radius:3px;overflow:hidden;background:var(--hs-n);margin:4px 0 2px}
.hs-band span{height:100%;min-width:1px}
.hs-axis{display:flex;justify-content:space-between;font-family:ui-monospace,Menlo,monospace;font-size:10px;color:var(--muted-foreground)}
.hs-day{display:grid;grid-template-columns:52px 1fr 56px;gap:8px;align-items:center;font-size:11px;color:var(--muted-foreground);margin:3px 0}
.hs-day .hs-band{margin:0;height:12px}
.hs-day .hs-pct{font-family:ui-monospace,Menlo,monospace;text-align:right}
.hs-legend{display:flex;gap:12px;font-size:11px;color:var(--muted-foreground);margin:6px 0 2px}
.hs-legend i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.hs-list{max-height:240px;overflow-y:auto;margin-top:6px}
.hs-ev{display:grid;grid-template-columns:10px 1fr auto;gap:2px 8px;align-items:start;padding:5px 0;border-top:1px solid var(--border)}
.hs-ev:first-child{border-top:0}
.hs-ev .hs-m{width:8px;height:8px;border-radius:50%;margin-top:4px}
.hs-ev .hs-w{color:var(--muted-foreground);font-size:11px}
.hs-ev .hs-t{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:var(--muted-foreground);text-align:right}
.hs-ftr{display:flex;justify-content:space-between;margin-top:8px;color:var(--muted-foreground);font-size:11px}
.hs-empty{color:var(--muted-foreground);font-size:11px;padding:6px 0}
`

// ── pure helpers (exported for tests) ─────────────────────────────────────

/** The hub's connection id among the registered gateways, or null. */
export function hubConnection(gateways) {
  const list = Array.isArray(gateways) ? gateways.filter(g => g && g.id) : []
  const exact = list.find(g => g.id === 'hermes-hub')
  if (exact) return exact.id
  const named = list.find(g => g.kind !== 'local' && /\bhub\b/i.test(`${g.id} ${g.label || ''}`))
  return named ? named.id : null
}

/** "45s", "12m", "6h 36m", "3d 4h". */
export function fmtSpan(ms) {
  const s = Math.max(0, Math.floor(ms / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ${m % 60}m`
  return `${Math.floor(h / 24)}d ${h % 24}h`
}

/** "0:42", "12:05", "1:02:05": a running restart counter. */
export function fmtClock(ms) {
  const s = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(s / 3600)
  const mm = String(Math.floor((s % 3600) / 60))
  const ss = String(s % 60).padStart(2, '0')
  return h ? `${h}:${mm.padStart(2, '0')}:${ss}` : `${mm}:${ss}`
}

export const chipCounter = (state, ms) => (state === RESTARTING ? fmtClock(ms) : fmtSpan(ms))

/** What the chip shows: the hub's own answer, or "unreachable" after FAILS_FOR_UNREACHABLE misses. */
export function deriveState({ status, fails, lastOkAt, firstFailAt }, now) {
  // Two misses in a row, also on a cold start: one dropped poll is not an outage.
  if (fails >= FAILS_FOR_UNREACHABLE) {
    return { state: DOWN, since: lastOkAt || firstFailAt || now, reason: 'no reply from the hub', unreachable: true }
  }
  if (!status) return null
  const st = [UP, RESTARTING, DOWN].includes(status.state) ? status.state : DOWN
  const sinceMs = Number(status.since) > 0 ? Number(status.since) * 1000 : now
  return { state: st, since: Math.min(sinceMs, now), reason: status.reason || '', unreachable: false }
}

/** Track local unreachable spans: open one when unreachable begins, close it when the hub answers. */
export function updateSpans(spans, unreachable, startAt, now) {
  const out = (Array.isArray(spans) ? spans : []).filter(s => s && Number(s.start) > 0 && (s.end == null || s.end >= now - RETAIN_MS))
  const open = out.length && out[out.length - 1].end == null ? out[out.length - 1] : null
  if (unreachable && !open) out.push({ start: startAt, end: null })
  if (!unreachable && open) open.end = now
  return out
}

/** Hub events (ts in seconds) + local unreachable spans (ms) → ordered, non-overlapping segments in [start, now]. */
export function buildSegments(events, spans, start, now) {
  const ev = (Array.isArray(events) ? events : [])
    .filter(e => e && Number.isFinite(Number(e.ts)) && RANK[e.state] != null)
    .map(e => ({ t: Number(e.ts) * 1000, state: e.state, reason: e.reason || '' }))
    .sort((a, b) => a.t - b.t)
  let segs = []
  for (let i = 0; i < ev.length; i++) {
    const a = Math.max(ev[i].t, start)
    const b = Math.min(i + 1 < ev.length ? ev[i + 1].t : now, now)
    if (b > a) segs.push({ start: a, end: b, state: ev[i].state, reason: ev[i].reason })
  }
  for (const sp of Array.isArray(spans) ? spans : []) {
    const a = Math.max(Number(sp.start), start)
    const b = Math.min(sp.end == null ? now : Number(sp.end), now)
    if (!(b > a)) continue
    const next = []
    for (const s of segs) {
      if (s.end <= a || s.start >= b) { next.push(s); continue }
      if (s.start < a) next.push({ ...s, end: a })
      if (s.end > b) next.push({ ...s, start: b })
    }
    next.push({ start: a, end: b, state: DOWN, reason: 'unreachable from this Mac', local: true })
    segs = next.sort((x, y) => x.start - y.start)
  }
  // merge touching segments in the same state
  const merged = []
  for (const s of segs) {
    const p = merged[merged.length - 1]
    if (p && p.state === s.state && p.end >= s.start && p.reason === s.reason) p.end = Math.max(p.end, s.end)
    else merged.push({ ...s })
  }
  return merged
}

/** Worst state per slot over [start, now]; a slot nothing covers is 'none'. */
export function stripSlots(segments, start, now, n = SLOTS) {
  const w = (now - start) / n
  const slots = Array(n).fill(NONE)
  for (const s of segments) {
    for (let i = Math.max(0, Math.floor((s.start - start) / w)); i < n && start + i * w < s.end; i++) {
      if (RANK[s.state] > RANK[slots[i]]) slots[i] = s.state
    }
  }
  return slots
}

/** Up share of the covered time, and counts of restarts / outages, in [start, now]. */
export function summarize(segments, start, now) {
  let up = 0
  let covered = 0
  let restarts = 0
  let outages = 0
  for (const s of segments) {
    const a = Math.max(s.start, start)
    const b = Math.min(s.end, now)
    if (b <= a) continue
    covered += b - a
    if (s.state === UP) up += b - a
    if (s.state === RESTARTING) restarts++
    if (s.state === DOWN) outages++
  }
  return { pct: covered ? (100 * up) / covered : null, restarts, outages, covered }
}

export function fmtPct(p) {
  if (p == null) return '—'
  if (p >= 100) return '100%'
  return `${p >= 99.95 ? p.toFixed(2) : p.toFixed(1)}%`
}

const pad = n => String(n).padStart(2, '0')
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
export function fmtTime(ms, now = Date.now()) {
  const d = new Date(ms)
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  return new Date(now).toDateString() === d.toDateString() ? hm : `${DAYS[d.getDay()]} ${hm}`
}

// ── store ─────────────────────────────────────────────────────────────────

const isHidden = () => typeof document !== 'undefined' && !!document.hidden

export function createStore(ctx) {
  const $poll = atom({ status: null, fails: 0, lastOkAt: 0, firstFailAt: 0 })
  const $events = atom({ range: '24h', data: null, error: null })
  // The chip's strip always reads the last 24 h, whatever range the popover shows.
  const $day = atom(null)
  const $now = atom(Date.now())
  let spans = (() => { try { return ctx.storage?.get?.('spans', []) || [] } catch { return [] } })()
  const $spans = atom(spans)
  let inflight = null

  const conn = () => { try { return hubConnection(host.state?.gateways?.get?.()) } catch { return null } }

  const saveSpans = next => {
    spans = next
    $spans.set(next)
    try { ctx.storage?.set?.('spans', next) } catch { /* best effort */ }
  }

  const refresh = () => {
    const connectionId = conn()
    if (!connectionId) return Promise.resolve()
    if (inflight) return inflight
    inflight = Promise.resolve()
      .then(() => ctx.rest('/status', { connectionId, passive: true }))
      .then(s => {
        if (!s || typeof s !== 'object' || !s.state) throw new Error('bad status')
        const now = Date.now()
        $poll.set({ status: s, fails: 0, lastOkAt: now, firstFailAt: 0 })
        saveSpans(updateSpans(spans, false, now, now))
      })
      .catch(() => {
        const p = $poll.get()
        const now = Date.now()
        const next = { ...p, fails: p.fails + 1, firstFailAt: p.firstFailAt || now }
        $poll.set(next)
        const d = deriveState(next, now)
        if (d && d.unreachable) saveSpans(updateSpans(spans, true, d.since, now))
      })
      .finally(() => { inflight = null })
    return inflight
  }

  const loadEvents = (range = $events.get().range) => {
    const connectionId = conn()
    if (!connectionId) return Promise.resolve()
    $events.set({ ...$events.get(), range })
    return Promise.resolve()
      .then(() => ctx.rest(`/events?range=${encodeURIComponent(range)}`, { connectionId, passive: true }))
      .then(d => {
        if (range === '24h') $day.set(d)
        if ($events.get().range === range) $events.set({ range, data: d, error: null })
      })
      .catch(e => { if ($events.get().range === range) $events.set({ range, data: $events.get().data, error: String(e?.message || e) }) })
  }

  const loadDay = () => {
    const connectionId = conn()
    if (!connectionId) return Promise.resolve()
    return Promise.resolve()
      .then(() => ctx.rest('/events?range=24h', { connectionId, passive: true }))
      .then(d => $day.set(d))
      .catch(() => {})
  }

  return { $poll, $events, $day, $now, $spans, refresh, loadEvents, loadDay, conn }
}

// ── view ──────────────────────────────────────────────────────────────────

function Strip({ slots }) {
  return jsx('span', {
    className: 'hs-strip',
    'aria-hidden': true,
    children: slots.map((s, i) => jsx('i', { style: { background: COLOR[s] } }, i))
  })
}

/** Band pieces over [start, end]: gaps are 'none', time after `now` is 'future' (keeps its width). */
export function bandParts(segments, start, end, now) {
  const parts = []
  let cursor = start
  for (const s of segments) {
    const a = Math.max(s.start, start)
    const b = Math.min(s.end, end)
    if (b <= a) continue
    if (a > cursor) parts.push({ start: cursor, end: a, state: NONE })
    parts.push({ ...s, start: a, end: b })
    cursor = b
  }
  if (cursor < Math.min(end, now)) parts.push({ start: cursor, end: Math.min(end, now), state: NONE })
  // Today's row: the rest of the day hasn't happened yet; keep its width so elapsed time isn't stretched.
  const later = Math.max(cursor, Math.min(end, now))
  if (end > later) parts.push({ start: later, end, state: FUTURE })
  return parts
}

function Band({ segments, start, end, now }) {
  const total = end - start
  const parts = bandParts(segments, start, end, now)
  return jsx('div', {
    className: 'hs-band',
    children: parts.map((p, i) => jsx('span', {
      style: { flex: (p.end - p.start) / total, background: p.state === NONE || p.state === FUTURE ? 'transparent' : COLOR[p.state] },
      title: p.state === FUTURE ? 'later today' : p.state === NONE
        ? `no data · ${fmtTime(p.start, now)} → ${fmtTime(p.end, now)}`
        : `${WORD[p.state]} · ${fmtTime(p.start, now)} → ${p.end >= now - 1000 ? 'now' : fmtTime(p.end, now)} · ${fmtSpan(p.end - p.start)}${p.reason ? ` · ${p.reason}` : ''}`
    }, i))
  })
}

function Popup({ store, cur, now }) {
  const ev = useValue(store.$events)
  const spans = useValue(store.$spans)
  const range = ev.range
  const span = range === '7d' ? 7 * DAY_MS : DAY_MS
  const start = now - span
  const segs = buildSegments(ev.data?.events, spans, start, now)
  const sum = summarize(segs, start, now)
  const incidents = segs.filter(s => s.state !== UP).slice().reverse()
  const head = cur
    ? `Hub ${WORD[cur.state]} ${chipCounter(cur.state, now - cur.since)}`
    : 'Hub status'
  const setRange = r => { if (r !== range) store.loadEvents(r) }

  const axis = range === '24h'
    ? [0, 6, 12, 18].map(h => fmtTime(start + h * 3_600_000, now)).concat('now')
    : null

  let days = null
  if (range === '7d') {
    days = []
    const today = new Date(now)
    today.setHours(0, 0, 0, 0)
    for (let i = 0; i < 7; i++) {
      const a = today.getTime() - i * DAY_MS
      const b = a + DAY_MS
      const daySegs = segs.filter(s => s.end > a && s.start < b)
      const ds = summarize(daySegs, a, Math.min(b, now))
      days.push(jsxs('div', {
        className: 'hs-day',
        children: [
          jsx('span', { children: i === 0 ? 'Today' : `${DAYS[new Date(a).getDay()]} ${new Date(a).getDate()}` }),
          jsx(Band, { segments: daySegs, start: a, end: b, now }),
          jsx('span', { className: 'hs-pct', children: fmtPct(ds.pct) })
        ]
      }, i))
    }
  }

  return jsxs('div', {
    className: 'hs-pop hs-root',
    children: [
      jsxs('div', {
        className: 'hs-hd',
        children: [
          jsx('span', { style: { width: 8, height: 8, borderRadius: '50%', background: cur ? COLOR[cur.state] : COLOR[NONE] } }),
          jsx('b', { children: head }),
          cur ? jsx('span', { className: 'hs-sub', children: `since ${fmtTime(cur.since, now)}` }) : null,
          jsxs('span', {
            className: 'hs-seg',
            role: 'group',
            'aria-label': 'Range',
            children: ['24h', '7d'].map(r => jsx('button', { type: 'button', 'aria-pressed': range === r, onClick: () => setRange(r), children: r }, r))
          })
        ]
      }),
      range === '24h' ? jsx(Band, { segments: segs, start, end: now, now }) : null,
      axis ? jsx('div', { className: 'hs-axis', children: axis.map((t, i) => jsx('span', { children: t }, i)) }) : null,
      days,
      jsxs('div', {
        className: 'hs-legend',
        children: [UP, RESTARTING, DOWN].map(s => jsxs('span', { children: [jsx('i', { style: { background: COLOR[s] } }), WORD[s]] }, s))
      }),
      ev.error && !ev.data ? jsx('div', { className: 'hs-empty', children: `Couldn't load history: ${ev.error}` }) : null,
      jsx('div', {
        className: 'hs-list',
        children: incidents.length
          ? incidents.map((s, i) => jsxs('div', {
            className: 'hs-ev',
            children: [
              jsx('span', { className: 'hs-m', style: { background: COLOR[s.state] } }),
              jsxs('div', {
                children: [
                  `${s.state === RESTARTING ? 'Restarted' : 'Unreachable'} · ${fmtSpan(s.end - s.start)}${s.end >= now - 1000 ? ' (ongoing)' : ''}`,
                  s.reason ? jsx('div', { className: 'hs-w', children: s.reason }) : null
                ]
              }),
              jsx('div', { className: 'hs-t', children: fmtTime(s.start, now) })
            ]
          }, i))
          : jsx('div', { className: 'hs-empty', children: ev.data ? `No restarts or outages in the last ${range === '7d' ? '7 days' : '24 h'}.` : 'Loading…' })
      }),
      jsxs('div', {
        className: 'hs-ftr',
        children: [
          jsx('span', { children: `${range === '7d' ? '7 d' : '24 h'} · ${fmtPct(sum.pct)} up · ${sum.restarts} restart${sum.restarts === 1 ? '' : 's'} · ${sum.outages} outage${sum.outages === 1 ? '' : 's'}` }),
          cur && cur.reason ? jsx('span', { children: cur.reason }) : null
        ]
      })
    ]
  })
}

export function chipModel(poll, day, spans, now) {
  const cur = deriveState(poll, now)
  if (!cur) return null
  const start = now - DAY_MS
  const segs = buildSegments(day?.events, spans, start, now)
  // the current state always paints the newest slot, even before history loads
  segs.push({ start: Math.max(cur.since, start), end: now, state: cur.state, reason: cur.reason })
  const slots = stripSlots(segs, start, now)
  const counter = chipCounter(cur.state, now - cur.since)
  const label = `Hub ${WORD[cur.state]} ${counter}`
  const sum = summarize(buildSegments(day?.events, spans, start, now), start, now)
  const sub = cur.state === UP
    ? `${sum.restarts} restart${sum.restarts === 1 ? '' : 's'}, ${sum.outages} outage${sum.outages === 1 ? '' : 's'} in 24 h`
    : `${cur.reason || ''}${cur.reason ? ' · ' : ''}since ${fmtTime(cur.since, now)}`
  return { cur, slots, counter, label, title: `${label}\n${sub}` }
}

function Chip({ store }) {
  const poll = useValue(store.$poll)
  const day = useValue(store.$day)
  const spans = useValue(store.$spans)
  const now = useValue(store.$now)
  const [open, setOpen] = useOpen()
  if (!store.conn()) return null
  const m = chipModel(poll, day, spans, now)
  if (!m) return null
  const durColor = m.cur.state === UP ? undefined : COLOR[m.cur.state]
  return jsxs(Popover, {
    open,
    onOpenChange: v => { setOpen(v); if (v) store.loadEvents() },
    children: [
      jsx(PopoverTrigger, {
        asChild: true,
        children: jsxs('button', {
          type: 'button',
          className: cn(BAR_CLASS, 'hs-root', open && 'text-foreground'),
          'data-slot': 'hub-status-chip',
          'data-state': m.cur.state,
          'aria-label': m.label,
          title: m.title,
          children: [
            jsx(Strip, { slots: m.slots }),
            jsx('span', { children: 'Hub' }),
            jsx('span', { className: 'hs-dur', style: durColor ? { color: durColor } : undefined, children: m.counter })
          ]
        })
      }),
      jsx(PopoverContent, {
        side: 'top',
        align: 'end',
        style: { width: 'min(400px, calc(100vw - 24px))' },
        children: open ? jsx(Popup, { store, cur: m.cur, now }) : null
      })
    ]
  })
}

// Popover open state without importing React hooks into the test shim: an atom per chip instance is fine
// (there is one chip).
const $open = atom(false)
function useOpen() {
  const v = useValue($open)
  return [v, x => $open.set(!!x)]
}

export default {
  id: ID,
  name: 'Hub Status',
  description: 'Status-bar chip: hub up / restarting / unreachable with live duration and a 24 h / 7 d timeline.',
  register(ctx) {
    if (typeof document !== 'undefined' && document.createElement) {
      const style = document.createElement('style')
      style.textContent = CSS
      document.head.append(style)
      ctx.onDispose?.(() => style.remove())
    }
    const store = createStore(ctx)
    store.refresh()
    store.loadDay()
    ctx.setInterval(() => { if (!isHidden() || store.$poll.get().fails > 0) store.refresh() }, POLL_MS)
    ctx.setInterval(() => { if (!isHidden()) store.loadDay() }, EVENTS_POLL_MS)
    ctx.setInterval(() => { if (!isHidden()) store.$now.set(Date.now()) }, 1000)
    if (typeof document !== 'undefined' && document.addEventListener) {
      const onVis = () => { if (!isHidden()) { store.$now.set(Date.now()); store.refresh() } }
      document.addEventListener('visibilitychange', onVis)
      ctx.onDispose?.(() => document.removeEventListener('visibilitychange', onVis))
    }
    // Same slot the Tailscale chip used (order 130).
    ctx.register({ id: 'chip', area: STATUSBAR_AREAS.right, order: 130, render: () => jsx(Chip, { store }) })
  }
}

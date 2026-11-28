/**
 * Hub Status — titlebar chip (left of the settings gear): is the hub up, restarting or unreachable, and for how long.
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

import { atom, cn, host, Popover, PopoverContent, PopoverTrigger, useValue } from '@hermes/plugin-sdk'
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

// Titlebar placement: right of llm-split (order 50), so it sits just left of the settings gear.
export const TITLEBAR_AREA = 'titleBar.tools.right'
export const TITLEBAR_ORDER = 60
export const TITLEBAR_SPAN = 6 // titlebar cells (6 x 24 px)
// The chip lives inside the titlebar's own <button>: a mouse click reaches the Radix trigger (the span); Enter/Space
// on the focused button arrive as a click with detail 0 and toggle the popover through onSelect instead.
const TB_CHIP = 'hs-root hs-tb inline-flex h-full w-full items-center justify-center gap-1.5 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary)'

const CSS = `
.hs-tool{width:calc(${TITLEBAR_SPAN}*var(--titlebar-control-size,24px))!important;padding:0!important}
.hs-tool:empty{display:none!important}
.hs-tb{user-select:none;white-space:nowrap}
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
.hs-version{margin-top:12px;padding-top:10px;border-top:1px solid var(--border)}
.hs-ver-hd{display:flex;align-items:baseline;justify-content:space-between;gap:8px;margin-bottom:6px}
.hs-ver-hd b{font-size:12px;font-weight:600}
.hs-ver-sub{color:var(--muted-foreground);font-size:11px;text-align:right}
.hs-version button{border:1px solid var(--border);border-radius:5px;padding:3px 8px;margin:6px 6px 0 0;cursor:pointer;background:transparent;color:var(--foreground);font:inherit;font-size:11px}
.hs-version button:hover:not(:disabled){background:color-mix(in srgb,var(--foreground) 7%,transparent)}
.hs-changes{max-height:200px;overflow-y:auto;overscroll-behavior:contain;margin:4px 0;padding:6px 4px 6px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border)}
.hs-bullets{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.hs-bullet{display:grid;grid-template-columns:10px minmax(0,1fr);gap:6px;align-items:start;font-size:11.5px;line-height:1.38;text-wrap:pretty}
.hs-bullet-dot{width:5px;height:5px;border-radius:50%;margin-top:5px;background:color-mix(in srgb,var(--foreground) 45%,transparent)}
.hs-bullet[data-impact=unblock] .hs-bullet-dot{background:var(--hs-g);box-shadow:0 0 0 2px color-mix(in srgb,var(--hs-g) 22%,transparent)}
.hs-version button.hs-upd{position:relative;display:block;width:100%;height:28px;margin:8px 0 0;padding:0 10px;overflow:hidden;background:transparent;color:var(--foreground);font:inherit;font-size:12px;font-weight:500;text-align:center}
.hs-version button.hs-upd:disabled{cursor:default}
.hs-upd-fill{position:absolute;inset:0 auto 0 0;background:color-mix(in srgb,var(--accent,#3fb950) 35%,transparent);transition:width .6s ease}
.hs-upd[data-phase=done] .hs-upd-fill{background:color-mix(in srgb,#3fb950 35%,transparent)}
.hs-upd[data-phase=error]{border-color:color-mix(in srgb,#f85149 60%,var(--border))}
.hs-upd[data-indeterminate=true] .hs-upd-fill{background-image:linear-gradient(90deg,transparent,color-mix(in srgb,var(--foreground) 18%,transparent),transparent);background-size:60% 100%;background-repeat:no-repeat;animation:hs-upd-sweep 1.4s linear infinite}
@keyframes hs-upd-sweep{from{background-position:-60% 0}to{background-position:160% 0}}
.hs-upd-label{position:relative;font-variant-numeric:tabular-nums}
.hs-blockers{margin-top:8px;padding:7px 8px;border:1px solid var(--border);border-radius:6px;background:color-mix(in srgb,var(--foreground) 3%,transparent)}
.hs-blk-hd{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:5px;font-size:11px;color:var(--muted-foreground)}
.hs-blk-hd b{color:var(--foreground);font-weight:600}
.hs-version button.hs-blk-all{margin:0;padding:1px 7px;height:20px;font-size:10.5px;border-color:color-mix(in srgb,var(--hs-r) 45%,var(--border));color:var(--foreground)}
.hs-blk-list{display:flex;flex-direction:column;gap:5px;max-height:150px;overflow-y:auto}
.hs-blk-row{display:grid;grid-template-columns:8px minmax(0,1fr) auto;gap:7px;align-items:center;padding-top:5px;border-top:1px solid color-mix(in srgb,var(--border) 65%,transparent);font-size:11px}
.hs-blk-row:first-child{padding-top:0;border-top:0}
.hs-blk-dot{width:6px;height:6px;border-radius:50%;background:var(--hs-y)}
.hs-blk-row[data-kind=build] .hs-blk-dot{background:var(--accent,#3fb950)}
.hs-blk-name{font-weight:500;line-height:1.28;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hs-blk-meta{color:var(--muted-foreground);font-size:10.5px;line-height:1.25}
.hs-version button.hs-blk-btn{margin:0;padding:1px 7px;height:21px;font-size:10.5px;white-space:nowrap}
.hs-version button.hs-blk-btn[data-action=stop]{border-color:color-mix(in srgb,var(--hs-r) 45%,var(--border))}
.hs-version button.hs-blk-btn:disabled,.hs-version button.hs-blk-all:disabled{opacity:.55;cursor:default}
.hs-confirm{margin-top:8px;padding:7px 8px;border:1px solid color-mix(in srgb,var(--hs-y) 50%,var(--border));border-radius:6px;font-size:11px;line-height:1.38}
.hs-upd-msg{margin-top:5px;font-size:11px;color:var(--muted-foreground)}
.hs-upd-msg[data-phase=error]{color:#f85149}
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
  // One spell per state: touching segments in the same state merge (a down spell whose
  // reason changed is still one outage); the reasons are kept in order.
  const merged = []
  for (const s of segs) {
    const p = merged[merged.length - 1]
    if (p && p.state === s.state && p.end >= s.start) {
      p.end = Math.max(p.end, s.end)
      if (s.reason && !p.reason.split(' → ').includes(s.reason)) p.reason = p.reason ? `${p.reason} → ${s.reason}` : s.reason
      if (s.local) p.local = true
    } else merged.push({ ...s })
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

  const version = createVersionStore()
  return { $poll, $events, $day, $now, $spans, refresh, loadEvents, loadDay, conn, version }
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

// Updates belong to the Desktop installation, never the selected remote gateway.
export const updatePending = s => ['ready', 'building'].includes(s?.state) && Number(s?.commits) > 0
export const updateTooltip = s => updatePending(s) ? `Update ready · ${s.commits} ${s.commits === 1 ? 'change' : 'changes'}` : null

const SHA_RE = /\b(?:(?=[0-9a-f]*\d)[0-9a-f]{7,40}|[0-9a-f]{12,40})\b/gi
const CARD_RE = /\bt_[0-9a-z]{8,24}\b/gi
const TAG_RE = /\s*\[[A-Za-z0-9_.-]+\]\s*/g
const CONV_RE = /^(?:(feat|fix|perf|refactor|docs|test|tests|chore|build|ci|style|revert)(?:\(([^)]+)\))?!?\s*:\s*)/i
const INTERNAL_RE = /^(?:test|tests|chore|ci|style)(?:\([^)]*\))?!?:\s*|^cover\b.*\b(?:clauses|branches|cases)\b|\b(?:test fixture|ratchet|lint gate)\b/i
const GATEWAY_CENSUS_RE = /^gateway\s+(\S+)\s+pid=\d+:\s+active=(\d+)\s+units=(\d+)\s+\(([a-z_,]*)\)/

export function stripHashes(text) {
  return String(text || '')
    .replace(TAG_RE, ' ')
    .replace(CARD_RE, '')
    .replace(/\bPR\s*#?\d+\b|\(#\d+\)/gi, '')
    .replace(SHA_RE, '')
    .replace(/\(\s*\)/g, '')
    .replace(/\s+/g, ' ')
    .replace(/[\s:;,-]+$/, '')
    .trim()
}

export function humanizeSubject(subject) {
  const raw = String(subject || '').trim()
  const internal = INTERNAL_RE.test(raw)
  const untagged = raw.replace(TAG_RE, ' ').trim()
  const m = CONV_RE.exec(untagged)
  const kind = (m?.[1] || '').toLowerCase()
  let s = stripHashes(untagged.replace(CONV_RE, ''))
    .replace(/\bkanban\b/gi, 'task board')
    .replace(/\bsession-state\b|\bSessionState\b/g, 'session status')
  const lead = /^([A-Za-z][A-Za-z0-9_-]{1,24}):\s+(.+)$/.exec(s)
  if (lead) s = lead[2]
  if (!s) return { text: '', internal: true }
  const low = s.toLowerCase()
  if (kind === 'fix' || /^(fix|avoid|prevent|repair)\s+/i.test(s)) {
    if (low.startsWith('fix ')) s = 'Fixes ' + s.slice(4)
    else if (low.startsWith('avoid ')) s = 'Prevents ' + s.slice(6)
    else if (low.startsWith('prevent ')) s = 'Prevents ' + s.slice(8)
    else if (low.startsWith('repair ')) s = 'Fixes ' + s.slice(7)
    else if (!low.startsWith('fixes ')) s = 'Fixes: ' + s[0].toLowerCase() + s.slice(1)
  } else if (low.startsWith('wake ')) s = 'Wakes ' + s.slice(5)
  else if (low.startsWith('retain ')) s = 'Keeps ' + s.slice(7)
  else if (low.startsWith('preserve ')) s = 'Preserves ' + s.slice(9)
  else if (low.startsWith('recover ')) s = 'Recovers ' + s.slice(8)
  else s = s[0].toUpperCase() + s.slice(1)
  return { text: s.replace(/\.$/, ''), internal }
}

/** Human-oriented bullets for "What's in the update" — never git hashes. */
export function summaryBullets(v) {
  if (Array.isArray(v?.summary?.bullets) && v.summary.bullets.length) {
    return v.summary.bullets.map(stripHashes).filter(Boolean)
  }
  const commits = Array.isArray(v?.changes) ? v.changes : []
  if (!commits.length) {
    return v?.desktop_swap_pending === true ? ['Finishes updating the Hermes Desktop app when it restarts'] : []
  }
  const parsed = commits.map(c => humanizeSubject(c?.subject)).filter(x => x.text)
  const visible = parsed.filter(x => !x.internal).map(x => x.text)
  const unique = [...new Set(visible)]
  if (unique.length) return unique.slice(0, 8)
  const n = commits.length
  return [`Internal reliability and test checks (${n} ${n === 1 ? 'change' : 'changes'}) — no visible workflow changes`]
}

/** Structured blockers when waiting, falling back to parsing legacy deferral_reason strings cleanly. */
export function deriveBlockers(v) {
  if (Array.isArray(v?.blockers)) return v.blockers
  const active = v?.progress || v?.force_request
  if (!active || v?.progress?.state === 'applying') return []
  const raw = String(v?.deferral_reason || '').trim()
  if (!raw) {
    return v?.progress?.state === 'batching' && !v?.force_request
      ? [{ key: 'window:batch', kind: 'window', id: 'batch', profile: 'default', label: 'Scheduled quiet update window', detail: 'Batching updates to avoid interrupting your work', action: 'force', action_label: 'Skip wait' }]
      : []
  }
  const parts = raw.split(';').map(s => s.trim()).filter(Boolean)
  const items = []
  let hasCronOrKanban = false
  for (const part of parts) {
    if (part.startsWith('cron:')) {
      const rest = part.slice(5)
      const [profile, id] = rest.includes(':') ? rest.split(':', 2) : ['default', rest]
      const key = `cron:${profile}:${id}`
      if (!items.some(x => x.key === key)) {
        items.push({ key, kind: 'cron', id, profile, label: profile === 'default' ? 'Scheduled background job' : `Scheduled background job (${profile})`, detail: 'Scheduled job · running', action: 'stop', action_label: 'Stop' })
        hasCronOrKanban = true
      }
      continue
    }
    const gw = GATEWAY_CENSUS_RE.exec(part)
    if (gw) {
      const kinds = gw[4].split(',').filter(Boolean)
      if (hasCronOrKanban && kinds.length && kinds.every(k => k === 'cron' || k === 'managed')) continue
      const units = Number(gw[3] || gw[2] || 1)
      items.push({ key: `gateway:${gw[1]}`, kind: 'gateway', id: gw[1], profile: 'default', label: `Background gateway finishing ${units} active ${units === 1 ? 'task' : 'tasks'}`, detail: 'Waits for in-flight step to finish', action: null, action_label: null })
      continue
    }
    const clean = stripHashes(part) || part
    items.push({ key: `reason:${clean}`, kind: 'reason', id: clean, profile: 'default', label: clean[0].toUpperCase() + clean.slice(1), detail: null, action: null, action_label: null })
  }
  return items
}

export function versionStatusText(v) {
  if (!v) return 'Checking…'
  if (v.version_status) return stripHashes(v.version_status)
  const n = Array.isArray(v.changes) ? v.changes.length : Number(v.commits || 0)
  if (n > 0) return `${n} ${n === 1 ? 'change' : 'changes'} ready${v.desktop_swap_pending ? ' · restarts Desktop' : ''}`
  if (v.desktop_swap_pending) return 'Restart Desktop to finish update'
  return 'Up to date'
}

function waitingLabel(v, blockers) {
  if (v?.progress?.state === 'batching' && !v?.force_request) return 'Waiting for update window…'
  if (!blockers.length) return 'Waiting for turns…'
  if (blockers.every(b => b.kind === 'cron')) {
    return blockers.length === 1 ? 'Waiting on 1 background job…' : `Waiting on ${blockers.length} background jobs…`
  }
  if (blockers.every(b => b.kind === 'turn')) {
    return blockers.length === 1 ? 'Waiting on 1 chat turn…' : `Waiting on ${blockers.length} chat turns…`
  }
  if (blockers.length === 1) {
    if (blockers[0].kind === 'build') return 'Building app update…'
    if (blockers[0].kind === 'desktop') return 'Waiting for app restart…'
    return 'Waiting on 1 item…'
  }
  return `Waiting on ${blockers.length} items…`
}

/**
 * The Update now button's progress: a stage-based bar, since the updater publishes stages, not percentages.
 *   waiting 8-45% (filled along the drainer's cap when it published one) → installing 60% → restarting 85% → done 100%.
 * `phase`: idle | active | done | error. `indeterminate`: the stage has no measurable fill, so the bar sweeps.
 */
export function updateProgress(v, busy = false) {
  const operation = v && (!v.force_request || v.operation?.requested_at >= v.force_request.requested_at) ? v.operation : null
  const p = v?.progress
  if (['failed', 'deferred'].includes(operation?.phase)) return { phase: 'error', pct: 0, label: 'Retry update', indeterminate: false }
  if (operation?.phase === 'restarting') return { phase: 'active', pct: 85, label: 'Restarting…', indeterminate: true }
  if (p?.state === 'applying') return { phase: 'active', pct: 60, label: 'Installing…', indeterminate: true }
  if (p || v?.force_request) {
    const cap = Number(p?.cap_s), el = Number(p?.state_elapsed_s)
    const frac = cap > 0 && el >= 0 ? Math.min(1, el / cap) : null
    const pct = frac === null ? 15 : Math.round(8 + 37 * frac)
    const blockers = deriveBlockers(v)
    return { phase: 'active', pct, label: waitingLabel(v, blockers), indeterminate: frac === null }
  }
  if (operation?.phase === 'updating') return { phase: 'active', pct: 60, label: 'Installing…', indeterminate: true }
  if (busy) return { phase: 'active', pct: 4, label: 'Starting…', indeterminate: true }
  if (operation?.phase === 'updated' && !(v?.changes?.length)) return { phase: 'done', pct: 100, label: 'Updated', indeterminate: false }
  return { phase: 'idle', pct: 0, label: 'Update now', indeterminate: false }
}

export function createVersionStore(api = options => window.hermesDesktop.api(options)) {
  const $version = atom(null)
  const $message = atom('')
  const $confirm = atom(null)
  const $busy = atom(false)
  const $stopping = atom({})
  let busy = false
  let apiLevel = 2
  const setBusy = b => { busy = b; $busy.set(b) }
  const setStoppingKey = (key, active) => {
    const cur = { ...$stopping.get() }
    if (active) cur[key] = true
    else delete cur[key]
    $stopping.set(cur)
  }
  const request = (path, options = {}) => api({ path: `/api/plugins/core-update/${path}`, connectionId: 'local', ...options })
  const cancelCronFallback = item => api({
    path: `/api/cron/jobs/${encodeURIComponent(item.id)}/cancel?profile=${encodeURIComponent(item.profile || 'default')}`,
    method: 'POST',
    connectionId: 'local'
  })
  const refresh = async () => {
    try {
      const ping = await request('ping', { passive: true })
      if (!(ping?.api >= 2)) throw new Error('Version panel awaits backend activation')
      apiLevel = Number(ping.api) || 2
      const v = await request('version', { passive: true })
      $version.set(v)
      const operation = !v.force_request || v.operation?.requested_at >= v.force_request.requested_at ? v.operation : null
      if (['failed', 'deferred'].includes(operation?.phase)) {
        $message.set(stripHashes(operation.reason || 'Update deferred') || 'Update deferred'); setBusy(false)
      } else if (v.human_deferral) $message.set(v.human_deferral)
      else if (v.deferral_reason) {
        const parsed = deriveBlockers(v)
        $message.set(parsed.length ? (parsed.length === 1 ? parsed[0].label : `${parsed[0].label} +${parsed.length - 1} more`) : (stripHashes(v.deferral_reason) || v.deferral_reason))
      } else if (v.progress) $message.set(v.progress.state === 'applying' ? 'Updating' : 'Waiting for turns')
      else if (v.force_request) $message.set('Waiting for turns')
      else if (operation?.phase === 'updated') {
        $message.set('Updated'); setBusy(false)
      } else if (['updating', 'restarting'].includes(operation?.phase)) $message.set('Updating')
      else { $message.set(''); setBusy(false) }
      return v
    } catch (e) { $message.set(String(e?.message || e)); return null }
  }
  const apply = async () => {
    if (busy) return
    setBusy(true)
    $confirm.set(null)
    $message.set('Waiting for turns')
    try {
      const response = await request('update', { method: 'POST' })
      if (!response?.ok) throw new Error(response?.reason || 'Update request refused')
      await refresh()
    } catch (e) { setBusy(false); $message.set(String(e?.message || e)) }
  }
  const updateNow = async () => {
    if (busy) return
    const v = await refresh()
    if (!v) return
    if (v.running_turns !== 0) $confirm.set(v.running_turns ?? 'Unknown number of')
    else await apply()
  }
  const stopBlocker = async item => {
    if (!item?.action) return
    if (item.action === 'force') { await apply(); return }
    if (item.action === 'restart') {
      setStoppingKey(item.key, true)
      try {
        const res = await request('restart', { method: 'POST' })
        if (!res?.ok) throw new Error(res?.reason || 'Could not restart Desktop')
        await refresh()
      } catch (e) { $message.set(String(e?.message || e)) }
      finally { setStoppingKey(item.key, false) }
      return
    }
    if (item.action !== 'stop') return
    setStoppingKey(item.key, true)
    try {
      let handled = false
      if (apiLevel >= 3) {
        try {
          const res = await request('blocker', {
            method: 'POST',
            body: { action: 'stop', kind: item.kind, id: item.id, execution_id: item.execution_id, holder: item.holder, profile: item.profile }
          })
          if (res?.ok) handled = true
        } catch { /* fall back to core cron route when mounted api predates /blocker */ }
      }
      if (!handled && item.kind === 'cron') {
        await cancelCronFallback(item)
        handled = true
      }
      if (!handled) throw new Error('Could not stop blocker')
      await refresh()
    } catch (e) { $message.set(String(e?.message || e)) }
    finally { setStoppingKey(item.key, false) }
  }
  const stopAllBlockers = async () => {
    const v = $version.get()
    const blockers = deriveBlockers(v).filter(b => b.action === 'stop')
    if (!blockers.length) return
    setStoppingKey('__all__', true)
    try {
      let handled = false
      if (apiLevel >= 3) {
        try {
          const res = await request('blocker', { method: 'POST', body: { action: 'stop_all' } })
          if (res?.ok) handled = true
        } catch { /* fall back per cron item */ }
      }
      if (!handled) {
        await Promise.all(blockers.filter(b => b.kind === 'cron').map(cancelCronFallback))
      }
      if (!v?.force_request) await request('update', { method: 'POST' }).catch(() => {})
      await refresh()
    } catch (e) { $message.set(String(e?.message || e)) }
    finally { setStoppingKey('__all__', false) }
  }
  return {
    $version, $message, $confirm, $busy, $stopping,
    active: () => busy || updateProgress($version.get()).phase === 'active',
    refresh, updateNow, apply, stopBlocker, stopAllBlockers,
    cancel: () => $confirm.set(null)
  }
}

export function VersionSection({ store }) {
  const v = useValue(store.$version)
  const message = useValue(store.$message)
  const count = useValue(store.$confirm)
  const busy = useValue(store.$busy || { get: () => false })
  const stopping = useValue(store.$stopping || { get: () => ({}) })
  const prog = updateProgress(v, busy)
  const bullets = summaryBullets(v)
  const blockers = prog.phase === 'active' ? deriveBlockers(v) : []
  const stoppable = blockers.filter(b => b.action === 'stop')
  const forcible = blockers.filter(b => b.action === 'force')
  const stoppingAll = !!stopping.__all__

  const bulletRow = (text, i) => {
    const isUnblock = /^(Unblocks|Resumes)\b/i.test(text)
    return jsxs('li', {
      className: 'hs-bullet',
      'data-impact': isUnblock ? 'unblock' : 'change',
      children: [
        jsx('span', { className: 'hs-bullet-dot', 'aria-hidden': true }),
        jsx('span', { children: text })
      ]
    }, i)
  }

  const blockerRow = b => {
    const busyRow = stoppingAll || !!stopping[b.key]
    return jsxs('div', {
      className: 'hs-blk-row',
      'data-kind': b.kind,
      children: [
        jsx('span', { className: 'hs-blk-dot', 'aria-hidden': true }),
        jsxs('div', {
          children: [
            jsx('div', { className: 'hs-blk-name', title: b.label, children: b.label }),
            b.detail ? jsx('div', { className: 'hs-blk-meta', children: b.detail }) : null
          ]
        }),
        b.action ? jsx('button', {
          type: 'button',
          className: 'hs-blk-btn',
          'data-action': b.action,
          disabled: busyRow,
          onClick: () => store.stopBlocker?.(b),
          children: busyRow ? 'Stopping…' : (b.action_label || 'Stop')
        }) : null
      ]
    }, b.key)
  }

  const showStatusMsg = message
    && !blockers.length
    && message.replace(/…$/, '') !== prog.label.replace(/…$/, '')
    && !/^(Updating|Waiting for turns|Waiting on |Updated)$/.test(message)

  return jsxs('section', { className: 'hs-version', 'aria-label': "What's in the update", children: [
    jsxs('div', { className: 'hs-ver-hd', children: [
      jsx('b', { children: "What’s in the update" }),
      jsx('span', { className: 'hs-ver-sub', children: versionStatusText(v) })
    ] }),
    bullets.length
      ? jsx('div', {
        className: 'hs-changes',
        children: jsx('ul', { className: 'hs-bullets', 'aria-label': 'Expected changes in this update', children: bullets.map(bulletRow) })
      })
      : jsx('div', { className: 'hs-empty', children: v ? 'Everything is up to date' : 'Checking for updates…' }),
    jsxs('button', {
      type: 'button', className: 'hs-upd', 'data-phase': prog.phase, 'data-indeterminate': String(prog.indeterminate),
      disabled: prog.phase === 'active', 'aria-busy': prog.phase === 'active',
      onClick: () => store.updateNow(),
      children: [
        jsx('span', { className: 'hs-upd-fill', role: 'progressbar', 'aria-label': 'Update progress', 'aria-valuemin': 0, 'aria-valuemax': 100,
          'aria-valuenow': prog.indeterminate ? undefined : prog.pct, style: { width: `${prog.pct}%` } }),
        jsx('span', { className: 'hs-upd-label', children: prog.label })
      ]
    }),
    blockers.length ? jsxs('div', {
      className: 'hs-blockers',
      role: 'region',
      'aria-label': 'Waiting on',
      children: [
        jsxs('div', { className: 'hs-blk-hd', children: [
          jsxs('span', { children: [jsx('b', { children: 'Waiting on' }), ` · ${blockers.length} ${blockers.length === 1 ? 'item' : 'items'}`] }),
          stoppable.length >= 2 ? jsx('button', {
            type: 'button',
            className: 'hs-blk-all',
            disabled: stoppingAll,
            onClick: () => store.stopAllBlockers?.(),
            children: stoppingAll ? 'Stopping…' : `Stop all (${stoppable.length})`
          }) : (!stoppable.length && forcible.length ? jsx('button', {
            type: 'button',
            className: 'hs-blk-all',
            onClick: () => store.stopBlocker?.(forcible[0]),
            children: forcible[0].action_label || 'Skip wait'
          }) : null)
        ] }),
        jsx('div', { className: 'hs-blk-list', children: blockers.map(blockerRow) })
      ]
    }) : null,
    count !== null ? jsxs('div', { className: 'hs-confirm', role: 'alert', children: [
      `${count} active chat ${count === 1 ? 'turn' : 'turns'} on this Mac. Chats pause cleanly after their current step and resume automatically; background jobs finish or can be stopped below. `,
      jsx('div', { children: [
        jsx('button', { type: 'button', onClick: () => store.apply(), children: 'Confirm update' }),
        jsx('button', { type: 'button', onClick: () => store.cancel(), children: 'Cancel' })
      ] })
    ] }) : null,
    showStatusMsg
      ? jsx('div', { className: 'hs-upd-msg', 'data-phase': prog.phase, role: 'status', children: message }) : null
  ] })
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
      }),
      jsx(VersionSection, { store: store.version })
    ]
  })
}

/** Minute refresh: the popover's selected range, plus the strip's 24 h when the popover shows another range. */
export function refreshHistory(store) {
  if (store.$events.get().range !== '24h') store.loadDay()
  return store.loadEvents()
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

function Chip({ store, placement = 'titlebar' }) {
  const version = useValue(store.version.$version)
  const poll = useValue(store.$poll)
  const day = useValue(store.$day)
  const spans = useValue(store.$spans)
  const now = useValue(store.$now)
  const [open, setOpen] = useOpen()
  if (!store.conn()) return null
  const m = chipModel(poll, day, spans, now)
  if (!m) return null
  const durColor = m.cur.state === UP ? undefined : COLOR[m.cur.state]
  const inTitlebar = placement === 'titlebar'
  // Events inside the portaled popover still bubble through React's tree to the titlebar button: stop them there.
  const stop = e => e.stopPropagation()
  return jsxs(Popover, {
    open,
    onOpenChange: v => { setOpen(v); if (v) store.loadEvents() },
    children: [
      jsx(PopoverTrigger, {
        asChild: true,
        children: jsxs(inTitlebar ? 'span' : 'button', {
          ...(inTitlebar ? {} : { type: 'button' }),
          className: cn(inTitlebar ? TB_CHIP : cn(BAR_CLASS, 'hs-root'), open && 'text-foreground'),
          'data-slot': 'hub-status-chip',
          'data-state': m.cur.state,
          'aria-label': m.label,
          title: [updateTooltip(version), m.title].filter(Boolean).join('\n'),
          children: [
            jsx(Strip, { slots: m.slots }),
            jsx('span', { children: 'Hub' }),
            updatePending(version) ? jsx('span', { className: 'codicon codicon-arrow-up', 'data-slot': 'hub-update-arrow', 'aria-label': updateTooltip(version) }) : null,
            jsx('span', { className: 'hs-dur', style: durColor ? { color: durColor } : undefined, children: m.counter })
          ]
        })
      }),
      jsx(PopoverContent, {
        side: inTitlebar ? 'bottom' : 'top',
        align: 'end',
        sideOffset: 6,
        className: 'hs-panel',
        ...(inTitlebar ? {
          onClick: stop, onPointerDown: stop, onPointerMove: stop, onPointerUp: stop,
          onFocus: stop, // portal focus must not open the outer titlebar Tooltip
          onKeyDown: e => { if (e.key !== 'Escape') e.stopPropagation() },
          onOpenAutoFocus: e => e.preventDefault(),
          onFocusOutside: e => e.preventDefault()
        } : {}),
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

// The titlebar button can stop the pointer events Radix uses to detect an outside click, so close on a captured
// click that lands outside both the chip and its panel.
export const isOpenForTest = () => $open.get()

export function closeOnOutsideClick(target) {
  if (!$open.get() || !target?.closest) return
  if (target.closest('[data-slot="hub-status-chip"]') || target.closest('.hs-panel')) return
  // The titlebar tool's own button (keyboard Enter/Space clicks target it) is inside too: onSelect toggles there.
  if (target.closest('.hs-tool')) return
  $open.set(false)
}

export function titlebarTool(store) {
  return {
    id: ID,
    label: 'Hub status',
    title: 'Hub status: up / restarting / unreachable. Click for the timeline',
    span: TITLEBAR_SPAN,
    className: 'hs-tool',
    icon: jsx(Chip, { store, placement: 'titlebar' }),
    onSelect: event => {
      if (!event || event.detail === 0 || (event.currentTarget && event.target === event.currentTarget)) {
        $open.set(!$open.get())
        if ($open.get()) store.loadEvents()
      }
    }
  }
}

export default {
  id: ID,
  name: 'Hub Status',
  description: 'Titlebar chip: hub up / restarting / unreachable with live duration and a 24 h / 7 d timeline.',
  register(ctx) {
    if (typeof document !== 'undefined' && document.createElement) {
      const style = document.createElement('style')
      style.textContent = CSS
      document.head.append(style)
      ctx.onDispose?.(() => style.remove())
    }
    const store = createStore(ctx)
    store.version.refresh()
    // 30 s at rest; every 2 s while an update runs so the button's bar moves.
    let lastVersion = Date.now()
    ctx.setInterval(() => {
      if (isHidden()) return
      if (store.version.active() || Date.now() - lastVersion >= 30_000) { lastVersion = Date.now(); store.version.refresh() }
    }, 2_000)
    store.refresh()
    store.loadDay()
    ctx.setInterval(() => { if (!isHidden() || store.$poll.get().fails > 0) store.refresh() }, POLL_MS)
    // The popover's history (any range) refreshes too; a 24 h load also feeds the strip.
    ctx.setInterval(() => { if (!isHidden()) refreshHistory(store) }, EVENTS_POLL_MS)
    ctx.setInterval(() => { if (!isHidden()) store.$now.set(Date.now()) }, 1000)
    if (typeof document !== 'undefined' && document.addEventListener) {
      const onVis = () => { if (!isHidden()) { store.$now.set(Date.now()); store.refresh() } }
      document.addEventListener('visibilitychange', onVis)
      ctx.onDispose?.(() => document.removeEventListener('visibilitychange', onVis))
    }
    if (typeof document !== 'undefined' && document.addEventListener) {
      const onClick = e => closeOnOutsideClick(e.target)
      document.addEventListener('click', onClick, true)
      ctx.onDispose?.(() => document.removeEventListener('click', onClick, true))
    }
    ctx.register({ id: 'chip', area: TITLEBAR_AREA, order: TITLEBAR_ORDER, data: titlebarTool(store) })
  }
}

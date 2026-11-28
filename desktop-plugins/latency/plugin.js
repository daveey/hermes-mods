// Ledger: server-owned quantiles and counts; renderer emits identifier-only spans.
import { host, useValue, useQuery, useQueryClient, STATUSBAR_AREAS, PALETTE_AREA, Popover, PopoverTrigger, PopoverContent, Dialog, DialogContent, DialogTitle } from '@hermes/plugin-sdk'
import { createElement as h, useState, useEffect, useRef } from 'react'

export const RANGES = ['24h', '7d', '30d', '90d']
export const UI_METRICS = ['ui.session_switch', 'ui.new_session', 'ui.card_create', 'ui.card_read', 'ui.decision_submit']
const SHOW = 'hermes-show-latency'
const SAFE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$/
export const CSS = `
.hermes-latency{--lat-bg:#000;--lat-surface:#121214;--lat-line:#2c2c31;--lat-fg:#ededed;--lat-muted:#7c7c84;--lat-cyan:#7cf2ff;--lat-ok:#3ddc84;--lat-warn:#ffb020;--lat-bad:#ff4d5e;color:var(--lat-fg);background:var(--lat-bg);font:12px/1.4 Inter,-apple-system,system-ui,sans-serif;box-sizing:border-box;min-width:0}
.hermes-latency *{box-sizing:border-box}.hermes-latency button{font:inherit;color:inherit;cursor:pointer}.lat-chip{display:inline-flex;align-items:center;gap:6px;white-space:nowrap;flex:none;border:1px solid var(--lat-line);border-radius:5px;padding:2px 7px;height:24px}.lat-chip svg{width:44px;height:14px;flex:none}.lat-pop{padding:8px 10px;border:1px solid var(--lat-line);border-radius:8px;width:380px;max-width:calc(100vw - 16px);max-height:calc(100vh - 48px);overflow:auto;box-shadow:0 10px 30px #000a}.lat-pop header{font-size:11px;color:var(--lat-muted);margin-bottom:6px}.lat-pop-row{display:grid;grid-template-columns:minmax(0,1fr) 48px 96px;gap:6px;align-items:center;width:100%;background:none;border:0;text-align:left;padding:4px 0}.lat-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}.lat-value{text-align:right;font-variant-numeric:tabular-nums}.lat-muted{color:var(--lat-muted)}.lat-dot{display:inline-block;width:7px;height:7px;border-radius:2px;margin-right:6px}.lat-dot[data-status=none]{border:1px dashed var(--lat-muted)}.lat-dot[data-status=ok]{background:var(--lat-ok)}.lat-dot[data-status=warn]{background:var(--lat-warn)}.lat-dot[data-status=bad]{background:var(--lat-bad)}.lat-bad{color:var(--lat-bad)}.lat-warn{color:var(--lat-warn)}.lat-ok{color:var(--lat-ok)}
.lat-dialog{padding:0!important;width:min(640px,calc(100vw - 24px))!important;max-width:calc(100vw - 24px)!important;max-height:calc(100vh - 48px);overflow:auto;border:1px solid var(--lat-line);border-radius:8px;gap:0!important}.lat-header{display:flex;align-items:center;gap:8px;padding:10px;border-bottom:1px solid var(--lat-line);padding-right:38px}.lat-header h2{font-size:13px;font-weight:600;margin:0}.lat-range{display:flex;margin-left:auto;border:1px solid var(--lat-line);border-radius:5px;overflow:hidden}.lat-range button{background:none;border:0;padding:2px 7px;color:var(--lat-muted)}.lat-range button[aria-pressed=true]{color:var(--lat-fg);background:var(--lat-surface)}.lat-group{padding:8px 10px 3px;text-transform:uppercase;letter-spacing:.06em;font-size:10.5px;color:var(--lat-muted);margin:0}.lat-row{display:grid;grid-template-columns:minmax(0,1fr) 50px 52px 110px;align-items:center;gap:6px;padding:4px 10px;width:100%;background:none;border:0;text-align:left;font-size:12px}.lat-row[aria-expanded=true]{background:var(--lat-surface);box-shadow:inset 2px 0 0 var(--lat-cyan)}.lat-row-head{color:var(--lat-muted);font-size:10.5px}.lat-row svg{width:100%;height:22px}.lat-empty{color:var(--lat-muted);font-size:10.5px}.lat-detail{padding:8px 10px 10px;background:var(--lat-surface);border-block:1px solid var(--lat-line);min-width:0}.lat-chart{width:100%;height:auto;display:block;overflow:visible}.lat-chart text{font-size:10px;fill:var(--lat-muted)}.lat-legend{display:flex;gap:10px;flex-wrap:wrap;color:var(--lat-muted);font-size:10.5px}.lat-slow{display:flex;align-items:center;gap:8px;padding:3px 0;min-width:0}.lat-slow button{background:none;border:0;padding:0;color:var(--lat-cyan);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.lat-slow time{color:var(--lat-muted);margin-left:auto;white-space:nowrap;font-size:10px}.lat-stages{display:flex;height:16px;border-radius:3px;overflow:hidden;margin:8px 0 4px}.lat-notice{padding:8px 10px;color:var(--lat-muted);font-size:11px}.lat-retry{background:none;border:1px solid var(--lat-line);border-radius:4px}.lat-chip:focus-visible,.hermes-latency button:focus-visible{outline:1px solid var(--lat-cyan);outline-offset:2px}
@media(max-width:500px){.lat-row{grid-template-columns:minmax(0,1fr) 40px 44px 90px;gap:4px}.lat-pop-row{grid-template-columns:minmax(0,1fr) 42px 70px}.lat-slow time{display:none}}
`

export function duration(ms) {
  if (ms == null || !Number.isFinite(ms)) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60000) return `${+(ms / 1000).toFixed(1)}s`
  if (ms < 3600000) return `${+(ms / 60000).toFixed(1)}m`
  return `${+(ms / 3600000).toFixed(1)}h`
}

// Gaps stay gaps. Zero is measured; missing is never drawn at zero.
export function pathFor(points, field, width, height, max, left = 2, top = 2) {
  let drawing = false
  return points.map((point, i) => {
    if (point[field] == null) { drawing = false; return '' }
    const x = left + i * (width - left - 2) / Math.max(1, points.length - 1)
    const y = top + (height - top - 2) * (1 - point[field] / max)
    const part = `${drawing ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`
    drawing = true
    return part
  }).join(' ')
}

// An isolated observation has no line segment; draw it explicitly (zero too).
export function isolatedPoints(points, field, width, height, max, left = 2, top = 2) {
  return points.flatMap((p, i) => p[field] != null && points[i - 1]?.[field] == null && points[i + 1]?.[field] == null
    ? [h('circle', { key: `${field}-${i}`, 'data-observation': field, cx: left + i * (width - left - 2) / Math.max(1, points.length - 1), cy: top + (height - top - 2) * (1 - p[field] / max), r: 2, fill: field === 'p90' ? 'var(--lat-cyan)' : 'var(--lat-muted)' })] : [])
}

export function Spark({ points = [], target, width = 96, height = 22 }) {
  const max = Math.max(1, target || 0, ...points.map(p => p.p90 || 0)) * 1.1
  return h('svg', { width, height, viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': '8-day first-reply p90 history', 'data-testid': 'spark' },
    target > 0 && h('line', { x1: 0, x2: width, y1: height - 2 - target / max * (height - 4), y2: height - 2 - target / max * (height - 4), stroke: 'var(--lat-line)', strokeDasharray: '2 2' }),
    h('path', { d: pathFor(points, 'p50', width, height, max), fill: 'none', stroke: 'var(--lat-muted)', strokeWidth: 1 }),
    h('path', { d: pathFor(points, 'p90', width, height, max), fill: 'none', stroke: 'var(--lat-cyan)', strokeWidth: 1.5 }),
    isolatedPoints(points, 'p50', width, height, max), isolatedPoints(points, 'p90', width, height, max))
}

function History({ row, range }) {
  const points = range === '24h' ? row.hours || [] : row.series || []
  const max = Math.max(1, row.target_p90 || 0, ...points.map(p => p.p90 || 0)) * 1.1
  const y = v => 118 - v / max * 108
  return h('svg', { className: 'lat-chart', viewBox: '0 0 600 144', role: 'img', 'aria-label': `${row.label} history with target` },
    [0, max / 2, max].map(v => h('g', { key: v }, h('line', { x1: 48, x2: 590, y1: y(v), y2: y(v), stroke: 'var(--lat-line)' }), h('text', { x: 44, y: y(v) + 3, textAnchor: 'end' }, duration(v)))),
    h('line', { x1: 48, x2: 590, y1: y(row.target_p90), y2: y(row.target_p90), stroke: 'var(--lat-warn)', strokeDasharray: '4 3', 'data-testid': 'target-line' }),
    h('text', { x: 590, y: Math.max(12, y(row.target_p90) - 4), textAnchor: 'end' }, `target p90 ${duration(row.target_p90)}`),
    h('path', { d: pathFor(points, 'p50', 592, 120, max, 48, 10), fill: 'none', stroke: 'var(--lat-muted)', strokeWidth: 1.2 }),
    h('path', { d: pathFor(points, 'p90', 592, 120, max, 48, 10), fill: 'none', stroke: 'var(--lat-cyan)', strokeWidth: 1.8 }),
    isolatedPoints(points, 'p50', 592, 120, max, 48, 10), isolatedPoints(points, 'p90', 592, 120, max, 48, 10),
    points.length > 0 && [0, points.length - 1].map((i, n) => h('text', { key: n, x: n ? 590 : 48, y: 138, textAnchor: n ? 'end' : 'start' }, points[i].day || new Date(points[i].hour * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }))))
}

function Dot({ status }) { return h('span', { className: 'lat-dot', 'data-status': status, title: { none: 'Not measured', ok: 'Within target', warn: 'Above target', bad: 'More than 2× target' }[status] }) }

export async function openSample(ctx, sample) {
  // The server supplies identity, the local registry supplies transport routes.
  const link = sample.link
  if (['pr', 'run'].includes(link?.kind) && /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/(?:pull|actions\/runs)\/[1-9]\d*$/.test(link.url)) return ctx.os.openExternal(link.url)
  const gateway = host.state.gateways.get().find(g => g.id === link?.gateway)
  if (!gateway || !SAFE.test(link?.profile || '') || !SAFE.test(link?.id || '')) throw Error('Sample owner route is not available')
  if (link.kind === 'session') return host.openSession(link.id, { route: { connectionId: gateway.id, mode: gateway.kind === 'local' ? 'local' : 'remote', profile: link.profile, targetProfile: link.profile } })
  if (link.kind === 'card' && SAFE.test(link.board || '') && typeof window.__hermesKanbanOpenTask === 'function' && window.__hermesKanbanOpenTask.supportsQualifiedOwners === true) return window.__hermesKanbanOpenTask(link.id, link.board, gateway.id, link.profile)
  throw Error('Sample owner route is not available')
}

function Detail({ ctx, row, range }) {
  const [error, setError] = useState('')
  const stages = row.stages || []
  return h('section', { className: 'lat-detail', id: `lat-detail-${row.id}`, 'data-testid': 'metric-detail' },
    row.n ? h(History, { row, range }) : h('p', { className: 'lat-muted' }, 'not measured yet'),
    h('div', { className: 'lat-legend' }, h('span', { style: { color: 'var(--lat-cyan)' } }, '— p90'), h('span', null, '— p50'), h('span', null, '-- target'), h('span', null, `n=${row.n}`)),
    stages.length > 0 && h('div', null,
      row.turn_trace && h('p', { className: 'lat-muted' }, row.turn_trace.trace_id ? `Latest traced turn · ${duration(row.turn_trace.end_to_end_ms)} end to end` : 'Turn stages not measured yet'),
      h('div', { className: 'lat-stages', 'aria-label': 'Stage split' }, stages.map((s, i) => h('span', { key: s.name, 'data-measured': s.dur_ms != null, title: `${s.name}: ${s.dur_ms == null ? 'unmeasured' : duration(s.dur_ms)}`, style: { flexGrow: s.dur_ms == null ? 0 : s.dur_ms, flexBasis: s.dur_ms == null ? '8px' : 0, background: s.dur_ms == null ? 'var(--lat-line)' : 'var(--lat-cyan)', opacity: .35 + .6 * (i + 1) / stages.length } }))),
      h('div', { className: 'lat-legend' }, stages.map(s => h('span', { key: s.name }, `${s.name} ${s.dur_ms == null ? 'unmeasured' : duration(s.dur_ms)}`)))),
    row.slowest?.length > 0 && h('div', null, h('p', { className: 'lat-muted', style: { margin: '8px 0 3px', fontSize: 11 } }, 'Slowest in this range'), row.slowest.map(s => h('div', { className: 'lat-slow', key: s.id }, h('span', { className: 'lat-value', style: { width: 52, flex: 'none' } }, duration(s.dur_ms)), h('button', { onClick: () => Promise.resolve(openSample(ctx, s)).catch(() => setError('Sample owner route is not available')) }, s.attrs?.session || s.attrs?.card || (s.attrs?.pr ? `PR #${s.attrs.pr}` : s.id)), h('time', { dateTime: new Date(s.start * 1000).toISOString() }, new Date(s.start * 1000).toLocaleString())))),
    error && h('p', { role: 'alert', className: 'lat-muted' }, error))
}

export function LedgerPanel({ ctx, payload, range, setRange, selected, setSelected, live, error, retry }) {
  return h('div', { className: 'hermes-latency', 'data-testid': 'ledger-panel' },
    h('header', { className: 'lat-header' }, h(DialogTitle, null, 'Latency'), h('div', { className: 'lat-range', 'aria-label': 'History range' }, RANGES.map(r => h('button', { key: r, 'aria-pressed': range === r, onClick: () => setRange(r) }, r)))),
    h('div', { className: 'lat-row lat-row-head' }, h('span', null, 'operation'), h('span', { className: 'lat-value' }, 'p50'), h('span', { className: 'lat-value' }, 'p90'), h('span', null, '8 days')),
    error ? h('div', { className: 'lat-notice', role: 'alert' }, 'Latency is unavailable. ', h('button', { className: 'lat-retry', onClick: retry }, 'Retry')) : !payload ? h('div', { className: 'lat-notice' }, 'Loading latency…') : payload.groups.map(group => h('section', { key: group.label }, h('h3', { className: 'lat-group' }, group.label), group.rows.map(row => h('div', { key: row.id },
      h('button', { className: 'lat-row', 'data-metric': row.id, 'aria-expanded': selected === row.id, 'aria-controls': `lat-detail-${row.id}`, onClick: () => setSelected(selected === row.id ? null : row.id) },
        h('span', { className: 'lat-name', title: row.label }, h(Dot, { status: row.status }), row.label),
        h('span', { className: 'lat-value lat-muted' }, duration(row.p50)), h('span', { className: `lat-value lat-${row.status}` }, duration(row.p90)),
        row.n ? h(Spark, { points: row.spark, target: row.target_p90 }) : h('span', { className: 'lat-empty' }, 'not measured yet')),
      selected === row.id && h(Detail, { ctx, row, range }))))),
    h('footer', { className: 'lat-notice' }, live ? 'Live · hub measurements' : 'Not live · refreshes every 30s', payload && ` · observed ${new Date(payload.at * 1000).toLocaleTimeString()}`))
}

// IDs generated without an OTel SDK; no prompts, bodies, URLs, query or credentials.
export function makeSpan(event, crypto = globalThis.crypto) {
  if (!UI_METRICS.includes(event?.name) || !Number.isFinite(event.start_ms) || !Number.isFinite(event.duration_ms) || event.duration_ms < 0 || event.duration_ms > 3600000) return null
  const id = bytes => Array.from(crypto.getRandomValues(new Uint8Array(bytes)), n => n.toString(16).padStart(2, '0')).join('')
  const attributes = { 'service.name': 'hermes-desktop', 'host.name': SAFE.test(event.host || '') && event.host.length <= 100 ? event.host : 'desktop' }
  for (const [source, target] of [['session_id', 'hermes.session_id'], ['card_id', 'hermes.card_id'], ['pr', 'hermes.pr'], ['gateway', 'hermes.gateway'], ['profile', 'hermes.profile'], ['board', 'hermes.board']]) if (typeof event[source] === 'string' && SAFE.test(event[source])) attributes[target] = event[source]
  const start = BigInt(Math.round(event.start_ms * 1e6))
  const result = { trace_id: /^[a-f0-9]{32}$/.test(event.trace_id || '') && !/^0+$/.test(event.trace_id) ? event.trace_id : id(16), span_id: id(8), name: event.name, start_time_unix_nano: String(start), end_time_unix_nano: String(start + BigInt(Math.round(event.duration_ms * 1e6))), attributes }
  if (/^[a-f0-9]{16}$/.test(event.parent_span_id || '') && !/^0+$/.test(event.parent_span_id)) result.parent_span_id = event.parent_span_id
  return result
}

// The core emits this only after the first matching assistant prose painted.
// Preserve its pre-send root identity; never manufacture a replacement trace.
export function makeTurnSpans(event, crypto = globalThis.crypto) {
  if (event?.name !== 'turn.send' || !/^[a-f0-9]{32}$/.test(event.trace_id || '') || /^0+$/.test(event.trace_id) || !/^[a-f0-9]{16}$/.test(event.root_span_id || '') || /^0+$/.test(event.root_span_id)) return []
  const span = makeSpan({ ...event, name: 'ui.session_switch' }, crypto)
  if (!span) return []
  const root = { ...span, name: 'turn.send', span_id: event.root_span_id }
  delete root.parent_span_id
  const painted = { ...span, name: 'turn.painted', parent_span_id: root.span_id, start_time_unix_nano: root.end_time_unix_nano }
  return [root, painted]
}

export function hubConnection(gateways = host.state.gateways.get()) {
  const list = Array.isArray(gateways) ? gateways : []
  return list.find(g => g.id === 'hermes-hub')?.id || list.find(g => g.kind !== 'local' && /\bhub\b/i.test(`${g.id} ${g.label || ''}`))?.id || null
}
export function hubRest(ctx, path, options = {}) {
  const connectionId = hubConnection()
  if (!connectionId) return Promise.reject(Error('No hub connection configured'))
  return ctx.rest(path, { ...options, connectionId, profile: 'default' })
}

export function spanQueue(ctx, now = Date.now) {
  let queue = ctx.storage.get('pending-spans', []), sending = false, disposed = false
  if (!Array.isArray(queue)) queue = []
  const fresh = () => { queue = queue.filter(s => { try { const age = now() - Number(BigInt(s.start_time_unix_nano) / 1000000n); return age >= -60000 && age < 3600000 } catch { return false } }).slice(-500); ctx.storage.set('pending-spans', queue) }
  const flush = async () => {
    if (sending || disposed) return
    fresh()
    if (!queue.length) return
    sending = true
    const batch = queue.slice(0, 100)
    try { await hubRest(ctx, '/v1/spans', { method: 'POST', body: { spans: batch } }); queue = queue.filter(s => !batch.includes(s)); fresh() } catch { /* keep identifiers only, for at most 1h */ } finally { sending = false }
  }
  return { add(event) { const spans = event?.name === 'turn.send' ? makeTurnSpans(event) : [makeSpan(event)].filter(Boolean); if (spans.length) { queue.push(...spans); fresh() } }, flush, dispose() { disposed = true }, size() { fresh(); return queue.length } }
}

function useLedger(ctx, range) {
  const client = useQueryClient()
  const activeConnection = useValue(host.state.connectionId)
  const gateways = useValue(host.state.gateways)
  const connection = hubConnection(gateways)
  const [live, setLive] = useState(false)
  const key = ['latency', 'ledger', connection, range]
  const query = useQuery({ queryKey: key, queryFn: () => hubRest(ctx, `/v1/ledger?range=${range}`), refetchInterval: live ? false : 30000, refetchOnWindowFocus: true })
  useEffect(() => {
    setLive(false)
    let alive = true, last = 0
    // ctx.socket currently targets the active connection only. Never listen to
    // another gateway's series while reading/posting to the hub.
    const dispose = activeConnection === connection && connection ? ctx.socket(`/v1/events?range=${range}`, payload => {
      if (!alive || !payload?.groups || payload.range !== range) return
      last = Date.now(); setLive(true); client.setQueryData(['latency', 'ledger', connection, range], payload)
    }) : null
    // Unchanged snapshots need no push. The core socket has no close callback;
    // a quiet five-minute safety read both refreshes data and rechecks fallback.
    const timer = setInterval(() => { if (Date.now() - last > 300000) { setLive(false); client.invalidateQueries({ queryKey: ['latency', 'ledger', connection, range] }) } }, 30000)
    return () => { alive = false; clearInterval(timer); if (typeof dispose === 'function') dispose() }
  }, [ctx, range, client, activeConnection, connection])
  return { ...query, live }
}

export function LedgerSurface({ ctx }) {
  const [range, setRange] = useState('7d'), [selected, setSelected] = useState(null), [panel, setPanel] = useState(false), [hover, setHover] = useState(false)
  const closeTimer = useRef(null)
  const query = useLedger(ctx, range)
  const show = id => { setHover(false); if (id) setSelected(id); setPanel(true) }
  useEffect(() => { const listener = e => show(e.detail?.metric); window.addEventListener(SHOW, listener); return () => { window.removeEventListener(SHOW, listener); clearTimeout(closeTimer.current) } }, [])
  const enter = () => { clearTimeout(closeTimer.current); if (!panel) setHover(true) }
  const leave = () => { clearTimeout(closeTimer.current); closeTimer.current = setTimeout(() => setHover(false), 150) }
  const chip = query.data?.chip
  return h('div', null,
    h(Popover, { open: hover && !panel, onOpenChange: setHover },
      h(PopoverTrigger, { asChild: true }, h('button', { className: 'hermes-latency lat-chip', type: 'button', 'aria-label': 'Latency', onPointerEnter: enter, onPointerLeave: leave, onFocus: enter, onBlur: leave, onClick: () => show() }, h('b', null, 'Latency'), h(Spark, { points: chip?.spark, width: 44, height: 14 }), h('span', { className: 'lat-bad', 'data-testid': 'latency-count', title: 'Metrics more than 2× target (24h p90)' }, chip ? chip.over_target_count : '—'))),
      h(PopoverContent, { className: 'hermes-latency lat-pop', side: 'top', align: 'end', sideOffset: 6, collisionPadding: 8, sticky: 'always', onPointerEnter: enter, onPointerLeave: leave, onFocus: enter, onBlur: leave, onOpenAutoFocus: e => e.preventDefault(), onCloseAutoFocus: e => e.preventDefault(), 'data-testid': 'latency-popover' },
        h('header', null, 'Latency · worst vs target · 24h p90'), chip?.worst.map(row => h('button', { className: 'lat-pop-row', key: row.id, onClick: () => show(row.id) }, h('span', { className: 'lat-name' }, h(Dot, { status: row.status }), row.label), h('span', { className: `lat-value lat-${row.status}` }, duration(row.p90)), h(Spark, { points: row.spark, target: row.target_p90 }))), chip?.worst.length === 0 && h('p', { className: 'lat-muted' }, 'not measured yet'))),
    h(Dialog, { open: panel, onOpenChange: setPanel }, h(DialogContent, { className: 'hermes-latency lat-dialog', 'aria-describedby': undefined }, h(LedgerPanel, { ctx, payload: query.data, range, setRange, selected, setSelected, live: query.live, error: query.isError, retry: query.refetch }))))
}

export default {
  id: 'latency', name: 'Latency',
  register(ctx) {
    const style = document.createElement('style'); style.textContent = CSS; document.head.appendChild(style)
    ctx.register({ id: 'chip', area: STATUSBAR_AREAS.right, order: 8, render: () => h(LedgerSurface, { ctx }) })
    ctx.register({ id: 'show', area: PALETTE_AREA, data: { title: 'Show Latency', onSelect: () => window.dispatchEvent(new CustomEvent(SHOW)) } })
    const queue = spanQueue(ctx)
    const off = host.onEvent('desktop.ui_latency', event => queue.add(event.payload))
    const offTurn = host.onEvent('desktop.turn_latency', event => queue.add(event.payload))
    const timer = setInterval(queue.flush, 5000)
    const online = () => queue.flush()
    window.addEventListener('online', online)
    queue.flush()
    ctx.onDispose(() => { off?.(); offTurn?.(); clearInterval(timer); queue.dispose(); style.remove(); window.removeEventListener('online', online) })
  }
}

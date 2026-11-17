// Lanes: how worker time is split between project lanes, and the controls for it.
// Opens from the runners chip (which dispatches `hermes-show-lanes` where lanes are configured) and from the
// palette. Data: GET /v1/lanes?hours=… on the local gateway (plugins/lanes): per lane target and actual share of
// run-hours, running, ready, held at the gate, open, done, tokens and estimated cost. Controls: PUT /v1/lanes/<lane>
// {share | paused | admit_per_day}. Shares are relative volumes: the target percentage is share / sum of shares.
import { useQuery, useQueryClient, STATUSBAR_AREAS, PALETTE_AREA, Popover, PopoverTrigger, PopoverContent } from '@hermes/plugin-sdk'
import { createElement as h, useState, useEffect } from 'react'

export const POLL_MS = 15000
export const IDLE_POLL_MS = 300000
export const REQUEST_TIMEOUT_MS = 20000
export const WINDOWS = [[1, '1h'], [6, '6h'], [24, '24h'], [168, '7d']]
export const STORE_KEY = 'hermes-lanes-window'
export const LOADED_FLAG = '__hermesLanesPanel'
const COLORS = ['#f0a43a', '#8fb0ff', '#5fd4e6', '#d982f0', '#b4bac6', '#3ddc97', '#ef7b7b', '#c9d36a']

// ── pure helpers (exported for tests) ─────────────────────────────────────

export function loadWindow(storage = globalThis.localStorage) {
  try {
    const v = Number(storage?.getItem(STORE_KEY))
    return WINDOWS.some(([hours]) => hours === v) ? v : 6
  } catch { return 6 }
}

export function saveWindow(hours, storage = globalThis.localStorage) {
  try { storage?.setItem(STORE_KEY, String(hours)) } catch {}
}

/** Rows in server order with a stable color, the target each lane WOULD get with `draft` shares applied. */
export function rows(snap, draft = {}) {
  const names = Object.keys(snap?.lanes || {})
  const share = (name) => {
    const lane = snap.lanes[name]
    const paused = draft[name]?.paused ?? lane.paused
    return paused ? 0 : Number(draft[name]?.share ?? lane.share) || 0
  }
  const total = names.reduce((sum, name) => sum + share(name), 0) || 1
  return names.map((name, i) => {
    const lane = snap.lanes[name]
    return {
      ...lane,
      name,
      color: COLORS[i % COLORS.length],
      share: Number(draft[name]?.share ?? lane.share) || 0,
      paused: draft[name]?.paused ?? lane.paused,
      target: share(name) / total,
      dirty: draft[name] != null
    }
  })
}

/** Why a lane is off its target, in the user's terms; '' when it is within 10 points. */
export function drift(row) {
  const gap = row.actual - row.target
  if (Math.abs(gap) <= 0.1) return ''
  if (gap > 0) return 'over: using idle slots'
  return row.ready === 0 ? 'under: nothing ready' : 'under: waiting for slots'
}

export function pct(value) { return `${Math.round((Number(value) || 0) * 100)}%` }

export function compact(n) {
  n = Number(n) || 0
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`
  return String(Math.round(n))
}

export function hours(seconds) {
  const v = (Number(seconds) || 0) / 3600
  return v >= 100 ? Math.round(v).toLocaleString('en-US') : v.toFixed(1)
}

export function age(seconds) {
  if (seconds == null) return ''
  if (seconds >= 86400) return `${Math.round(seconds / 86400)}d`
  if (seconds >= 3600) return `${Math.round(seconds / 3600)}h`
  return `${Math.max(1, Math.round(seconds / 60))}m`
}

/** "$12.40" plus how much of the lane's runs that covers: cost is only what workers reported. */
export function cost(row) {
  if (!row.usage_runs) return { text: '–', title: 'No run in this window reported cost' }
  return {
    text: `$${Number(row.cost_usd).toFixed(2)}`,
    title: `Estimated, from ${row.usage_runs} of ${row.runs} runs that reported usage · ${compact(row.tokens)} tokens in+out`
  }
}

/** The PUT bodies a draft needs, one per changed lane. */
export function changes(snap, draft) {
  return Object.entries(draft).map(([name, d]) => {
    const body = {}
    if (d.share != null && d.share !== snap.lanes[name]?.share) body.share = d.share
    if (d.paused != null && d.paused !== snap.lanes[name]?.paused) body.paused = d.paused
    return [name, body]
  }).filter(([, body]) => Object.keys(body).length)
}

// ── components ────────────────────────────────────────────────────────────

function Split({ label, list, field }) {
  return h('div', { className: 'hermes-lanes-split' },
    h('span', { className: 'hermes-lanes-splitlabel' }, label),
    h('div', { className: 'hermes-lanes-bar', role: 'img', 'aria-label': `${label} split: ${list.map((r) => `${r.name} ${pct(r[field])}`).join(', ')}` },
      list.map((r) => h('span', { key: r.name, title: `${r.name} ${pct(r[field])}`, style: { flex: `${Math.max(r[field], 0)} 1 0`, background: r.color } }))))
}

function Row({ row, maxOpen, onShare, onPause }) {
  const note = drift(row)
  const c = cost(row)
  return h('div', { className: 'hermes-lanes-row', 'data-lane': row.name },
    h('div', { className: 'hermes-lanes-name' },
      h('span', { className: 'hermes-lanes-dot', style: { background: row.color }, 'aria-hidden': 'true' }),
      h('div', null,
        h('div', { className: 'hermes-lanes-title' }, row.name),
        h('div', { className: 'hermes-lanes-sub', title: row.describe || undefined },
          row.gated ? `gated · ${row.admitted_today}${row.admit_per_day == null ? '' : `/${row.admit_per_day}`} admitted today` : (row.describe || '')))),
    h('div', { className: 'hermes-lanes-volume' },
      h('input', { type: 'range', min: 0, max: 100, step: 5, value: row.share, 'aria-label': `${row.name} volume`, onChange: (e) => onShare(row.name, Number(e.target.value)) }),
      h('button', { type: 'button', className: row.paused ? 'is-paused' : '', 'aria-pressed': row.paused, onClick: () => onPause(row.name, !row.paused) }, row.paused ? 'Paused' : 'Pause')),
    h('div', null,
      h('div', null, h('span', { className: 'hermes-lanes-big' }, pct(row.target)), h('span', { className: 'hermes-lanes-dim' }, ' target')),
      h('div', { className: note ? 'hermes-lanes-warn' : 'hermes-lanes-dim' }, `${pct(row.actual)} actual${note ? ` · ${note}` : ''}`)),
    h('div', null,
      h('div', null, h('span', { className: 'hermes-lanes-big' }, row.ready), h('span', { className: 'hermes-lanes-dim' }, ` ready${row.oldest_ready_s != null ? ` · oldest ${age(row.oldest_ready_s)}` : ''}`)),
      h('div', { className: 'hermes-lanes-dim' }, `${row.running} running · ${row.held} held · ${row.open} open`),
      h('div', { className: 'hermes-lanes-meter', 'aria-hidden': 'true' }, h('span', { style: { width: `${Math.round((row.open / maxOpen) * 100)}%`, background: row.color } }))),
    h('div', null,
      h('div', null, `${row.done} done · ${hours(row.run_seconds)} run-h`),
      h('div', { className: 'hermes-lanes-dim' }, `${row.runs} runs`)),
    h('div', { className: 'hermes-lanes-cost', title: c.title }, c.text))
}

export function Panel({ snap, windowHours, onWindow, onApply, saving, error, onRunners }) {
  const [draft, setDraft] = useState({})
  useEffect(() => { setDraft({}) }, [snap])
  if (!snap) return h('div', { className: 'hermes-lanes-note' }, error ? `Unavailable: ${error}` : 'Loading…')
  if (!snap.configured) return h('div', { className: 'hermes-lanes-note' }, snap.reason || 'No lanes are configured.')
  const list = rows(snap, draft)
  const pending = changes(snap, draft)
  const maxOpen = Math.max(1, ...list.map((r) => r.open))
  const edit = (name, patch) => setDraft((d) => ({ ...d, [name]: { ...d[name], ...patch } }))
  const notes = snap.planner?.notes || []
  return h('div', { className: 'hermes-lanes' },
    h('div', { className: 'hermes-lanes-head' },
      h('strong', null, 'Lanes'),
      h('span', { className: 'hermes-lanes-dim' }, `${list.reduce((s, r) => s + r.running, 0)} running · ${list.reduce((s, r) => s + r.open, 0)} open · ${snap.scope === 'fleet' ? 'whole fleet' : 'this board'}`),
      snap.shadow ? h('span', { className: 'hermes-lanes-warn', title: 'kanban.admission.lane_shadow is on: cards are classified and counted, but nothing is held, capped, reordered or paused' }, 'Shadow mode: reporting only') : null,
      h('span', { className: 'hermes-lanes-grow' }),
      onRunners ? h('button', { type: 'button', onClick: onRunners }, 'Runners') : null,
      h('div', { className: 'hermes-lanes-seg', role: 'group', 'aria-label': 'Window' },
        WINDOWS.map(([value, label]) => h('button', { key: value, type: 'button', className: value === windowHours ? 'is-on' : '', 'aria-pressed': value === windowHours, onClick: () => onWindow(value) }, label)))),
    h(Split, { label: 'Target', list, field: 'target' }),
    h(Split, { label: 'Actual', list, field: 'actual' }),
    h('div', { className: 'hermes-lanes-row hermes-lanes-cols' },
      h('span', null, 'Lane'), h('span', null, 'Volume'), h('span', null, 'Share of run-hours'), h('span', null, 'Backed up'), h('span', null, 'Throughput'), h('span', { className: 'hermes-lanes-cost' }, 'Cost')),
    list.map((row) => h(Row, { key: row.name, row, maxOpen, onShare: (name, share) => edit(name, { share }), onPause: (name, paused) => edit(name, { paused }) })),
    h('div', { className: 'hermes-lanes-foot' },
      h('div', { className: 'hermes-lanes-notes' },
        notes.length ? notes.map((n, i) => h('div', { key: i }, h('strong', null, `${n.lane}: `), n.text))
          : h('span', { className: 'hermes-lanes-dim' }, 'No planner notes yet.')),
      error ? h('span', { className: 'hermes-lanes-warn' }, error) : null,
      h('button', { type: 'button', disabled: !pending.length || saving, onClick: () => setDraft({}) }, 'Reset'),
      h('button', { type: 'button', className: 'is-primary', disabled: !pending.length || saving, onClick: () => onApply(pending) }, saving ? 'Applying…' : 'Apply')))
}

export function Lanes({ ctx }) {
  const [open, setOpen] = useState(false)
  const [windowHours, setWindowState] = useState(loadWindow)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const client = useQueryClient()
  const key = ['lanes', windowHours]
  const query = useQuery({
    queryKey: key,
    queryFn: () => ctx.rest(`/v1/lanes?hours=${windowHours}`, { timeoutMs: REQUEST_TIMEOUT_MS }),
    // Closed, a slow probe: it tells the runners chip whether lanes work here (LOADED_FLAG below).
    refetchInterval: open ? POLL_MS : IDLE_POLL_MS,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    retry: false,
    placeholderData: (prev) => prev
  })
  useEffect(() => {
    const show = () => setOpen(true)
    window.addEventListener('hermes-show-lanes', show)
    return () => window.removeEventListener('hermes-show-lanes', show)
  }, [])
  // The runners chip opens this panel only where lanes are configured and the API answers.
  const working = query.data?.configured === true
  useEffect(() => {
    if (!working) return undefined
    window[LOADED_FLAG] = true
    return () => { delete window[LOADED_FLAG] }
  }, [working])
  const apply = async (pending) => {
    setSaving(true); setError('')
    try {
      let last
      for (const [name, body] of pending) last = await ctx.rest(`/v1/lanes/${encodeURIComponent(name)}`, { method: 'PUT', body, timeoutMs: REQUEST_TIMEOUT_MS })
      if (last && windowHours === 6) client.setQueryData(key, last)
      else await query.refetch()
    } catch (e) { setError(String(e?.message || e).slice(0, 200)) } finally { setSaving(false) }
  }
  return h(Popover, { open, onOpenChange: setOpen },
    h(PopoverTrigger, { asChild: true }, h('span', { className: 'hermes-lanes-anchor', 'data-slot': 'lanes-anchor', 'aria-hidden': 'true' })),
    h(PopoverContent, { side: 'top', align: 'end', sticky: 'always', 'aria-label': 'Lanes', className: 'hermes-lanes-popover', style: { width: 'min(1040px, calc(100vw - 24px))', maxHeight: '76vh', overflow: 'auto' } },
      h(Panel, {
        snap: query.data, windowHours, saving, error: error || (query.error ? String(query.error.message).slice(0, 200) : ''),
        onWindow: (v) => { setWindowState(v); saveWindow(v) }, onApply: apply,
        onRunners: () => { setOpen(false); window.dispatchEvent(new window.Event('hermes-show-runners')) }
      })))
}

const CSS = `
.hermes-lanes-anchor{display:inline-block;width:0;height:100%}
.hermes-lanes{display:flex;flex-direction:column;gap:8px;font-size:12px;padding:6px}
.hermes-lanes-head,.hermes-lanes-foot{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.hermes-lanes-head strong{font-size:14px}
.hermes-lanes-grow{flex:1 1 0}
.hermes-lanes-dim{color:var(--ui-text-tertiary)}
.hermes-lanes-warn{color:#f0a43a}
.hermes-lanes-big{font-size:15px;font-weight:600;font-variant-numeric:tabular-nums}
.hermes-lanes button{height:28px;padding:0 10px;border:1px solid var(--ui-stroke-secondary);border-radius:6px;background:transparent;color:inherit;font:inherit;cursor:pointer}
.hermes-lanes button:disabled{opacity:.45;cursor:default}
.hermes-lanes button.is-primary{background:#8fb0ff;border-color:#8fb0ff;color:#0f1115;font-weight:600}
.hermes-lanes button.is-paused{border-color:#f0a43a;color:#f6c477}
.hermes-lanes-seg{display:flex;gap:2px}
.hermes-lanes-seg button.is-on{background:var(--chrome-action-hover);border-color:var(--ui-text-tertiary)}
.hermes-lanes-split{display:flex;align-items:center;gap:10px}
.hermes-lanes-splitlabel{width:48px;color:var(--ui-text-tertiary)}
.hermes-lanes-bar{flex:1 1 0;display:flex;gap:2px;height:10px;border-radius:3px;overflow:hidden}
.hermes-lanes-bar>span{min-width:0}
.hermes-lanes-row{display:grid;grid-template-columns:150px 230px 180px 200px 150px 70px;column-gap:16px;align-items:center;padding:8px 0;border-top:1px solid var(--ui-stroke-secondary);font-variant-numeric:tabular-nums}
.hermes-lanes-cols{border-top:0;padding:2px 0;color:var(--ui-text-tertiary);font-size:10.5px;text-transform:uppercase;letter-spacing:.04em}
.hermes-lanes-name{display:flex;align-items:center;gap:8px;min-width:0}
.hermes-lanes-dot{width:9px;height:9px;border-radius:3px;flex:none}
.hermes-lanes-title{font-weight:600;font-size:13px}
.hermes-lanes-sub{color:var(--ui-text-tertiary);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:125px}
.hermes-lanes-volume{display:flex;align-items:center;gap:8px}
.hermes-lanes-volume input{flex:1 1 0;min-width:0;accent-color:#8fb0ff}
.hermes-lanes-meter{height:3px;border-radius:2px;background:var(--ui-stroke-secondary);margin-top:4px}
.hermes-lanes-meter>span{display:block;height:100%;border-radius:2px}
.hermes-lanes-cost{text-align:right}
.hermes-lanes-notes{flex:1 1 320px;min-width:0}
.hermes-lanes-note{padding:10px;font-size:12px;color:var(--ui-text-secondary)}
`

export default {
  id: 'lanes',
  name: 'Lanes',
  description: 'Panel: the split of worker time between project lanes, backlog, throughput and cost, with volume controls.',
  register(ctx) {
    const style = document.createElement('style')
    style.textContent = CSS
    document.head.append(style)
    ctx.onDispose(() => style.remove())
    ctx.register({ id: 'anchor', area: STATUSBAR_AREAS.right, order: 6.5, render: () => h(Lanes, { ctx }) })
    ctx.register({
      id: 'show-lanes',
      area: PALETTE_AREA,
      data: {
        id: 'show-lanes', label: 'Show lanes', keywords: ['lanes', 'priority', 'schedule', 'share', 'backlog', 'cost'],
        run: () => window.dispatchEvent(new window.Event('hermes-show-lanes'))
      }
    })
  }
}

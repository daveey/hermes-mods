// Runners: machines with a fixed number of slots, and what is running in them.
// Gateway workers (card runs) and CI workers (GitHub Actions) from GET /v1/fleet.
import {
  host,
  useQuery,
  STATUSBAR_AREAS,
  PALETTE_AREA,
  Popover,
  PopoverTrigger,
  PopoverContent,
  StatusDot
} from '@hermes/plugin-sdk'
import { createElement as h, useState, useEffect, useMemo } from 'react'

const BAR =
  'inline-flex h-full items-center gap-1.5 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
export const POLL_OPEN_MS = 15000
// Closed too, so opening the panel always shows data at most 15s old. Cheap: the gateway
// serves a 5s cached build; nothing polls while the window is hidden.
export const POLL_CLOSED_MS = 15000
export const BACKOFF_MAX_MS = 300000
export const REQUEST_TIMEOUT_MS = 5000
const text = (v) => String(v ?? '').slice(0, 300)
const RANK = { ready: 0, stale: 1, unknown: 2, unavailable: 3, not_configured: 4 }

function validRoute(r) {
  return r && ['connectionId', 'targetProfile'].every((k) => typeof r[k] === 'string' && r[k].trim())
}
const routeKey = (r) => JSON.stringify([r.connectionId, r.targetProfile])

// Per-route last-known data and backoff. A failing route is skipped until its next
// allowed time (15s, 30s, 60s ... 5 min), so a dead network or gateway costs at most one
// request per backoff step, and its last data stays on screen marked stale.
export function createTransport(ctx, hostRef = host, clock = () => Date.now()) {
  const routes = new Map()
  async function fetchRoute(route) {
    const key = routeKey(route)
    const s = routes.get(key) || { route, failures: 0, nextAt: 0 }
    routes.set(key, { ...s, route })
    if (clock() < s.nextAt) return
    try {
      const body = await ctx.rest(
        `/v1/fleet?profile=${encodeURIComponent(route.targetProfile)}`,
        { connectionId: route.connectionId, timeoutMs: REQUEST_TIMEOUT_MS }
      )
      if (body?.contract_version !== 1 || !body.gateways || !body.ci) throw Error('Invalid fleet response')
      routes.set(key, { route, body, at: clock(), failures: 0, nextAt: 0 })
    } catch (e) {
      const failures = s.failures + 1
      const status = e?.status ?? e?.statusCode
      routes.set(key, {
        ...s, route, failures,
        error: status === 404 ? 'runners plugin not installed on this gateway' : text(e?.message || 'unreachable'),
        nextAt: clock() + Math.min(POLL_OPEN_MS * 2 ** (failures - 1), BACKOFF_MAX_MS)
      })
    }
  }
  async function poll() {
    const list = (await hostRef.profileRoutes()).filter(validRoute)
    const keys = new Set(list.map(routeKey))
    for (const k of routes.keys()) if (!keys.has(k)) routes.delete(k)
    await Promise.all(list.map(fetchRoute))
    return merge([...routes.values()], clock())
  }
  return { poll, routes }
}

// Each section comes from the route that has the best view of it (ready > stale > ...).
export function merge(sources, now = Date.now()) {
  const pick = (name) => {
    const options = sources
      .filter((s) => s.body?.[name])
      .map((s) => ({ ...s.body[name], connectionId: s.route.connectionId, at: s.at, error: s.error }))
      .sort((a, b) => (RANK[a.state] ?? 9) - (RANK[b.state] ?? 9) || b.at - a.at)
    const best = options[0]
    if (!best) {
      const err = sources.find((s) => s.error)?.error
      return { state: 'unknown', reason: err || 'no gateway answered yet', machines: [] }
    }
    // Last-known data from a route that is now failing is shown, but marked stale.
    if (best.error) return { ...best, state: 'stale', reason: `last update ${ago(now - best.at)} · ${best.error}` }
    return best
  }
  return { gateways: pick('gateways'), ci: pick('ci'), at: now }
}

export function ago(ms) {
  const s = Math.max(0, Math.round(ms / 1000))
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`
}

export function summarize(view) {
  const g = view.gateways.machines || [], c = view.ci.machines || []
  const gBusy = g.reduce((n, m) => n + m.running.length, 0)
  const gSlots = g.reduce((n, m) => n + (m.slots ?? m.running.length), 0)
  const slots = c.flatMap((m) => m.slots)
  const cBusy = slots.filter((s) => s.state === 'busy').length
  const cOffline = slots.filter((s) => s.state === 'offline').length
  const lost = g.filter((m) => m.state === 'lost').length
  const unknown = [view.gateways, view.ci].some((s) => s.state !== 'ready' && s.state !== 'not_configured')
  return {
    gBusy, gSlots, cBusy, cSlots: slots.length, cOffline, lost,
    tone: lost ? 'bad' : unknown ? 'warn' : gBusy + cBusy ? 'good' : 'muted'
  }
}

const SLOT_TITLE = { busy: 'busy', idle: 'free', offline: 'offline', unknown: 'state unknown' }

function Slot({ state, title, onClick }) {
  return h(onClick ? 'button' : 'span', {
    type: onClick ? 'button' : undefined,
    className: `hermes-fleet-slot is-${state}`,
    title, 'aria-label': title, onClick
  })
}

function openCard(run) {
  const hook = globalThis.__hermesKanbanOpenTask
  if (typeof hook === 'function' && hook(run.task, run.board, null) !== false) return
  host.navigate?.('/kanban')
}

function GatewayRow({ m, now }) {
  const free = m.slots == null ? 0 : Math.max(0, m.slots - m.running.length)
  const freeState = m.state === 'active' ? 'idle' : m.state === 'draining' ? 'draining' : 'unknown'
  const meta = [
    m.labels.filter((l) => !l.startsWith('machine:')).join(', '),
    m.pinned_only ? 'pinned only' : null,
    m.slots == null ? 'slots not set' : m.slots_source === 'local' ? 'slots from local cap' : null
  ].filter(Boolean).join(' · ')
  return h('div', { className: `hermes-fleet-row is-${m.state}`, 'data-machine': m.name },
    h('span', { className: 'hermes-fleet-name' }, text(m.name)),
    h('span', { className: 'hermes-fleet-slots' },
      ...m.running.map((r) => h(Slot, {
        key: `${r.board}/${r.task}`, state: m.state === 'lost' ? 'lost' : 'busy',
        title: `${r.task} · ${text(r.title)}\nboard ${r.board}${r.started_at ? ` · running ${ago(now - r.started_at * 1000)}` : ''}`,
        onClick: () => openCard(r)
      })),
      ...Array.from({ length: free }, (_, i) => h(Slot, { key: `free-${i}`, state: freeState, title: freeState === 'draining' ? 'draining: takes no new cards' : SLOT_TITLE[freeState] }))),
    h('span', { className: 'hermes-fleet-count' }, m.slots == null ? `${m.running.length}` : `${m.running.length}/${m.slots}`),
    h('span', { className: 'hermes-fleet-meta' }, [meta, m.reason].filter(Boolean).join(' · '))
  )
}

function CiRow({ m }) {
  const busy = m.slots.filter((s) => s.state === 'busy').length
  const offline = m.slots.filter((s) => s.state === 'offline').length
  return h('div', { className: 'hermes-fleet-row', 'data-machine': m.name },
    h('span', { className: 'hermes-fleet-name' }, text(m.name) + (m.ephemeral ? ' (ephemeral)' : '')),
    h('span', { className: 'hermes-fleet-slots' },
      ...m.slots.map((s, i) => h(Slot, { key: s.runner || i, state: s.state, title: `${text(s.runner)} · ${SLOT_TITLE[s.state]}\n${s.labels.join(', ')}` }))),
    h('span', { className: 'hermes-fleet-count' }, `${busy}/${m.slots.length}`),
    h('span', { className: 'hermes-fleet-meta' }, [m.labels.join(', '), offline ? `${offline} offline` : null].filter(Boolean).join(' · ')),
    ...m.warnings.map((w) => h('div', { key: w, className: 'hermes-fleet-warning' }, `⚠ ${text(w)}`))
  )
}

function SectionNote({ section }) {
  if (section.state === 'ready') return null
  const msg = {
    not_configured: 'Not configured on any connected gateway.',
    unknown: `Unknown: ${section.reason || 'no data yet'}`,
    unavailable: `Unavailable: ${section.reason || 'error'}`,
    stale: `Stale: ${section.reason || 'old data'}`
  }[section.state] || section.state
  return h('div', { className: 'hermes-fleet-note', role: 'status' }, msg)
}

export function Panel({ view, now }) {
  const s = summarize(view)
  return h('div', { className: 'hermes-fleet' },
    h('div', { className: 'hermes-fleet-head' },
      h('span', null, 'Gateway workers'),
      h('span', null, `${s.gBusy} / ${s.gSlots} slots busy`)),
    h(SectionNote, { section: view.gateways }),
    ...view.gateways.machines.map((m) => h(GatewayRow, { key: m.name, m, now })),
    h('div', { className: 'hermes-fleet-head' },
      h('span', null, 'CI workers (GitHub Actions)'),
      h('span', null, `${s.cBusy} / ${s.cSlots} busy${s.cOffline ? ` · ${s.cOffline} offline` : ''}`)),
    h(SectionNote, { section: view.ci }),
    ...view.ci.machines.map((m) => h(CiRow, { key: m.name, m })),
    view.ci.hidden_deregistered
      ? h('div', { className: 'hermes-fleet-note' }, `${view.ci.hidden_deregistered} deregistered runners (history) hidden`)
      : null,
    h('div', { className: 'hermes-fleet-foot' }, `Updated ${ago(now - view.at)} · every ${POLL_OPEN_MS / 1000}s`)
  )
}

function useVisible() {
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden')
  useEffect(() => {
    const on = () => setVisible(document.visibilityState !== 'hidden')
    document.addEventListener('visibilitychange', on)
    return () => document.removeEventListener('visibilitychange', on)
  }, [])
  return visible
}

function useNow(active) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => setNow(Date.now()), 5000)
    return () => clearInterval(t)
  }, [active])
  return now
}

// The React Query cache keeps the last view across refetches (no flicker, no spinner);
// one poll runs at a time and polling stops while the window is hidden.
export function Runners({ ctx }) {
  const [open, setOpen] = useState(false)
  const visible = useVisible()
  const transport = useMemo(() => createTransport(ctx), [ctx])
  const query = useQuery({
    queryKey: ['runners', 'fleet'],
    queryFn: () => transport.poll(),
    refetchInterval: visible ? (open ? POLL_OPEN_MS : POLL_CLOSED_MS) : false,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    retry: false,
    staleTime: 5000
  })
  useEffect(() => {
    const show = () => setOpen(true)
    window.addEventListener('hermes-show-runners', show)
    return () => window.removeEventListener('hermes-show-runners', show)
  }, [])
  useEffect(() => { if (open && visible) query.refetch() }, [open])
  const now = useNow(open && visible)
  const view = query.data
  const s = view ? summarize(view) : null
  const label = s ? `Runners ${s.gBusy + s.cBusy}/${s.gSlots + s.cSlots}` : 'Runners …'
  const title = s
    ? `Gateway workers ${s.gBusy}/${s.gSlots} busy · CI ${s.cBusy}/${s.cSlots} busy${s.lost ? ` · ${s.lost} machine lost` : ''}`
    : 'Loading runners'
  return h(Popover, { open, onOpenChange: setOpen },
    h(PopoverTrigger, { asChild: true },
      h('button', { type: 'button', className: BAR, 'data-slot': 'runners-chip', 'aria-label': `Show runners: ${label}`, title },
        h(StatusDot, { tone: s?.tone || 'muted' }), h('span', null, label))),
    h(PopoverContent, {
      side: 'top', align: 'end', sticky: 'always', 'aria-label': 'Runners',
      style: { width: 'min(640px, calc(100vw - 24px))', maxHeight: '70vh', overflowY: 'auto' }
    }, view ? h(Panel, { view, now }) : h('div', { className: 'hermes-fleet-note' }, query.error ? `Unavailable: ${text(query.error.message)}` : 'Loading…'))
  )
}

const CSS = `
.hermes-fleet{display:flex;flex-direction:column;gap:3px;font-size:12px;padding:4px}
.hermes-fleet-head{display:flex;justify-content:space-between;margin-top:8px;font-weight:600;text-transform:uppercase;font-size:11px;color:var(--ui-text-secondary)}
.hermes-fleet-head:first-child{margin-top:0}
.hermes-fleet-row{display:grid;grid-template-columns:150px minmax(0,1fr) 44px;column-gap:8px;align-items:center;min-height:20px}
.hermes-fleet-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hermes-fleet-slots{display:flex;flex-wrap:wrap;gap:2px}
.hermes-fleet-count{text-align:right;font-variant-numeric:tabular-nums;color:var(--ui-text-secondary)}
.hermes-fleet-meta,.hermes-fleet-warning{grid-column:2/4;font-size:11px;color:var(--ui-text-tertiary)}
.hermes-fleet-meta:empty{display:none}
.hermes-fleet-warning{color:var(--ui-text-secondary)}
.hermes-fleet-note,.hermes-fleet-foot{font-size:11px;color:var(--ui-text-tertiary)}
.hermes-fleet-foot{margin-top:8px}
.hermes-fleet-slot{display:inline-block;width:10px;height:10px;border-radius:2px;border:1px solid var(--ui-text-tertiary);padding:0;background:transparent}
button.hermes-fleet-slot{cursor:pointer}
.hermes-fleet-slot.is-busy{background:var(--ui-accent);border-color:var(--ui-accent)}
.hermes-fleet-slot.is-offline,.hermes-fleet-slot.is-unknown{border-style:dotted;opacity:.6}
.hermes-fleet-slot.is-draining{background:repeating-linear-gradient(45deg,transparent 0 2px,var(--ui-text-tertiary) 2px 3px)}
.hermes-fleet-slot.is-lost{background:#d64545;border-color:#d64545}
.hermes-fleet-row.is-asleep .hermes-fleet-name,.hermes-fleet-row.is-unknown .hermes-fleet-name{color:var(--ui-text-tertiary)}
`

export default {
  id: 'runners',
  name: 'Runners',
  description: 'Gateway and CI workers: fixed slots per machine and what is running in them.',
  register(ctx) {
    const style = document.createElement('style')
    style.textContent = CSS
    document.head.append(style)
    ctx.onDispose(() => style.remove())
    ctx.register({ id: 'chip', area: STATUSBAR_AREAS.right, order: 6, render: () => h(Runners, { ctx }) })
    ctx.register({
      id: 'show-runners',
      area: PALETTE_AREA,
      data: {
        id: 'show-runners', label: 'Show runners', keywords: ['runners', 'workers', 'slots', 'ci', 'gateways'],
        run: () => window.dispatchEvent(new window.Event('hermes-show-runners'))
      }
    })
  }
}

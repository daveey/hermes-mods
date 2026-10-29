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

const ID_PATTERN = /^[a-z]{2,12}_[0-9a-f]{16,64}$/
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const repairSafeError = (message) => Object.assign(new Error(message), { safe: true })
const repairRouteKey = (r) => JSON.stringify([r.connectionId, r.mode, r.profile, r.targetProfile])
const ownerQueryKey = (o) => ['runners', 'owner', o.gateway_id, o.profile]
const ownerKey = (o) => JSON.stringify(ownerQueryKey(o))

function repairValidRoute(r) {
  return (
    r &&
    ['connectionId', 'profile', 'targetProfile'].every((k) => typeof r[k] === 'string' && r[k].trim()) &&
    ['local', 'remote'].includes(r.mode)
  )
}

function verifyEnvelope(value, route, expected, discovery = false) {
  const o = value?.meta?.owner
  if (
    value?.meta?.contract_version !== 1 ||
    !UUID.test(o?.gateway_id || '') ||
    o.profile !== route.targetProfile ||
    (expected &&
      (ownerKey(o) !== ownerKey(expected) ||
        (discovery ? o.owner_epoch < expected.owner_epoch : o.owner_epoch !== expected.owner_epoch)))
  )
    throw repairSafeError('Owner identity mismatch')
  if (value.error)
    throw repairSafeError(
      {
        not_found: 'Plugin unavailable',
        forbidden: 'Partial permissions',
        configuration_error: 'Configuration error',
        dependency_unavailable: 'Provider outage'
      }[value.error.code] || 'Source unavailable'
    )
  if (!value.data) throw repairSafeError('Invalid inventory response')
  return value
}

// Port of the pre-fleet repair()/openRepair(): idempotency keys, owner/epoch
// checks, status reconciliation, and no prompt resend. The row's route and
// owner are copied before the first await.
export function createRepairClient(ctx, hostRef = host) {
  const starts = new Set(), repairPending = new Map(), repairKeys = new Map()
  async function routes() {
    const list = await hostRef.profileRoutes()
    if (!Array.isArray(list) || list.some((r) => !repairValidRoute(r)))
      throw repairSafeError('Invalid owner route registry')
    return [...new Map(list.map((r) => [repairRouteKey(r), { ...r }])).values()]
  }
  async function request(input, path, options = {}, expected) {
    if (!repairValidRoute(input)) throw repairSafeError('Invalid owner route')
    const route = { ...input }, key = repairRouteKey(route)
    if (!(await routes()).some((r) => repairRouteKey(r) === key))
      throw repairSafeError('Owner gateway disconnected: route removed')
    const response = await ctx.rest(
      `/v1${path}${path.includes('?') ? '&' : '?'}profile=${encodeURIComponent(route.targetProfile)}`,
      { ...options, connectionId: route.connectionId, timeoutMs: 10000 }
    )
    if (!(await routes()).some((r) => repairRouteKey(r) === key))
      throw repairSafeError('Owner gateway disconnected: route changed')
    if (path === '/enrollment') {
      if (
        response?.contract_version !== 1 || response.profile !== route.targetProfile ||
        typeof response.enrolled !== 'boolean' || !Array.isArray(response.capabilities) ||
        (response.enrolled && !response.capabilities.includes('inventory'))
      )
        throw repairSafeError('Invalid enrollment response')
      return response
    }
    return verifyEnvelope(response, route, expected, path === '/inventory' && !options.method)
  }
  async function assertRoute(route) {
    if (!(await routes()).some((r) => repairRouteKey(r) === repairRouteKey(route)))
      throw repairSafeError('Owner gateway disconnected: route removed')
  }
  async function openRepair(route, owner, repair) {
    if (
      !repair?.owner || ownerKey(repair.owner) !== ownerKey(owner) ||
      repair.owner.owner_epoch !== owner.owner_epoch ||
      !ID_PATTERN.test(repair.session?.start_request_id || '')
    )
      throw repairSafeError('Invalid repair owner or start request')
    if (typeof hostRef.requestProfile !== 'function' || typeof hostRef.openSession !== 'function')
      throw repairSafeError('Repair sessions unsupported: update Hermes Desktop')
    const params = {
      profile: route.targetProfile, namespace: 'runners', start_request_id: repair.session.start_request_id
    }
    async function status() {
      const response = await request(route, `/repairs/${encodeURIComponent(repair.id)}`, {}, owner)
      const current = response.data.repair
      if (current?.owner?.gateway_id !== owner.gateway_id || current.owner.profile !== owner.profile)
        throw repairSafeError('Repair session owner identity mismatch')
      return {
        gateway_id: current.owner.gateway_id, profile: current.owner.profile,
        stored_session_id: current.session.stored_session_id,
        resolved_session_id: current.session.lineage_tip_id,
        exists: !!current.session.stored_session_id,
        reconciliation: current.session.state, reconciled: true
      }
    }
    const key = JSON.stringify([ownerKey(owner), owner.owner_epoch, params.start_request_id])
    await assertRoute(route)
    let result
    if (starts.has(key)) {
      try {
        result = await status()
      } catch {
        throw repairSafeError('Repair outcome unknown; status reconciliation unavailable. No prompt resent.')
      }
    } else {
      starts.add(key)
      try {
        result = await hostRef.requestProfile(route, 'session.start_request', params, 10000, { spawnPriority: 'foreground' })
      } catch {
        await assertRoute(route)
        try {
          result = await status()
        } catch {
          throw repairSafeError('Repair outcome unknown; status reconciliation unavailable. No prompt resent.')
        }
      }
    }
    // Only an authoritative owner status of reserved/queued permits recovery of
    // a pre-create/pre-claim crash. Reuse the durable core ID, never send text.
    if (['reserved', 'queued'].includes(result?.reconciliation)) {
      await assertRoute(route)
      result = await hostRef.requestProfile(route, 'session.start_request', params, 10000, { spawnPriority: 'foreground' })
    }
    await assertRoute(route)
    // Core keys its start ledger by its own install authority, not the runners
    // owner UUID. Bind an RPC result to the verified runners owner through the
    // owner's reconciled status for the same repair before opening anything.
    if (result && !result.reconciled) {
      let server
      try {
        server = await status()
      } catch {
        throw repairSafeError('Repair session started; owner status unavailable. Open again to reconcile. No prompt resent.')
      }
      if (
        result.profile !== owner.profile || !server.exists ||
        server.stored_session_id !== result.stored_session_id
      )
        throw repairSafeError('Repair session owner identity mismatch')
      result = server
    }
    if (result?.gateway_id !== owner.gateway_id || result.profile !== owner.profile)
      throw repairSafeError('Repair session owner identity mismatch')
    const id = result.resolved_session_id || result.stored_session_id
    if (!result.exists || !/^[A-Za-z0-9_-]{1,128}$/.test(id || ''))
      throw repairSafeError('Repair session outcome unknown; no session available to open')
    await hostRef.openSession(id, { route: { ...route } })
    return repair
  }
  async function repair(row) {
    if (!row?.actionable || !row.source?.snapshot || row.source.error)
      throw repairSafeError('Owner gateway disconnected or ownership conflict')
    if (!ID_PATTERN.test(row.id)) throw repairSafeError('Invalid runner ID')
    const route = { ...row.source.route }, owner = { ...row.source.snapshot.meta.owner }, entity = row.id
    const key = JSON.stringify([ownerKey(owner), owner.owner_epoch, entity])
    const related = new Set([entity, ...row.source.snapshot.data.runners
      .filter((r) => row.host_id && r.host_id === row.host_id).map((r) => r.id)])
    const mappedHost = row.source.snapshot.data.hosts.find((item) => item.id === row.host_id)
    if (ID_PATTERN.test(mappedHost?.resource_id || '')) related.add(mappedHost.resource_id)
    if (repairPending.has(key)) return repairPending.get(key)
    const run = async () => {
      const detail = await request(route, `/runners/${encodeURIComponent(entity)}?history_limit=50`, {}, owner)
      const existing = (detail.data.repairs || []).find((r) =>
        related.has(r.entity_id) && !['closed', 'failed'].includes(r.state))
      if (existing) {
        starts.add(JSON.stringify([ownerKey(owner), owner.owner_epoch, existing.session?.start_request_id]))
        return openRepair(route, owner, existing)
      }
      const incident = (detail.data.incidents || []).find((i) =>
        i.entity_id === entity && !i.resolved_at && ID_PATTERN.test(i.id))
      if (!incident) throw repairSafeError('No current incident identity; refresh runner evidence before investigating')
      const previous = (detail.data.repairs || []).find((r) => related.has(r.entity_id))
      const attempt = `${key}:${incident.id}:${previous?.id || 'first'}`
      if (!repairKeys.has(attempt)) repairKeys.set(attempt, `repair_${crypto.randomUUID().replaceAll('-', '')}`)
      const response = await request(route, '/repairs', {
        method: 'POST',
        body: {
          entity_id: entity, incident_id: incident.id,
          idempotency_key: repairKeys.get(attempt), mode: row.issue ? 'repair' : 'diagnose', expected_owner: owner
        }
      }, owner)
      if (!related.has(response.data.repair?.entity_id))
        throw repairSafeError('Invalid repair entity')
      return openRepair(route, owner, response.data.repair)
    }
    const promise = run()
    repairPending.set(key, promise)
    try { return await promise } finally { repairPending.delete(key) }
  }
  // Popover enablement. Creating a session needs an open incident. Reopening an
  // active repair does not: the reservation outlives the incident row.
  async function prepare(route, slot, sourceError) {
    const label = 'Repair in new session'
    if (sourceError) return { enabled: false, label, reason: 'Owner gateway disconnected', row: null }
    if (!repairValidRoute(route) || !ID_PATTERN.test(slot?.id || ''))
      return { enabled: false, label, reason: 'No runner id on this slot', row: null }
    let enrollment
    try {
      enrollment = await request(route, '/enrollment')
    } catch (e) {
      if ((e?.status ?? e?.statusCode) === 404)
        return { enabled: false, label, reason: 'Repair sessions are not enabled for this gateway', row: null }
      throw e?.safe ? e : repairSafeError('Could not read enrollment')
    }
    const features = enrollment?.features?.repair_sessions === true
    if (!features)
      return { enabled: false, label, reason: 'Repair sessions are not enabled for this gateway', row: null }
    const detail = await request(route, `/runners/${encodeURIComponent(slot.id)}?history_limit=50`)
    const runner = detail.data.runner || { id: slot.id, host_id: null }
    const incident = (detail.data.incidents || []).find((i) =>
      i.entity_id === slot.id && !i.resolved_at && ID_PATTERN.test(i.id || ''))
    const active = (detail.data.repairs || []).find((r) =>
      r.entity_id === slot.id && !['closed', 'failed'].includes(r.state))
    const row = {
      actionable: true,
      id: slot.id,
      host_id: runner.host_id || null,
      issue: true,
      source: {
        route: { ...route },
        snapshot: {
          meta: { owner: { ...detail.meta.owner } },
          data: { runners: [runner], hosts: detail.data.hosts || [] }
        }
      }
    }
    return {
      enabled: !!(incident || active),
      label: active ? 'Open repair session' : label,
      reason: incident || active ? '' : 'No open incident',
      row
    }
  }
  return { prepare, repair, openRepair, request }
}

// Each section comes from the route that has the best view of it (ready > stale > ...).
export function merge(sources, now = Date.now()) {
  const pick = (name) => {
    const options = sources
      .filter((s) => s.body?.[name])
      .map((s) => ({ ...s.body[name], connectionId: s.route.connectionId, route: { ...s.route }, at: s.at, error: s.error }))
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
  return { gateways: withOwnCaps(pick('gateways'), sources), ci: pick('ci'), at: now }
}

// A gateway knows its own dispatcher cap; others only see the registry. Take each machine's
// local cap from the route that is that machine, so the view never flips between gateways.
function withOwnCaps(section, sources) {
  const own = new Map()
  for (const s of sources)
    for (const m of s.body?.gateways?.machines || [])
      if (m.me && m.slots_source === 'local') own.set(m.name, m.slots)
  if (!own.size) return section
  return {
    ...section,
    machines: section.machines.map((m) =>
      m.slots == null && own.has(m.name) ? { ...m, slots: own.get(m.name), slots_source: 'local' } : m)
  }
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

function Slot({ state, title, onClick, onMouseEnter, slotId }) {
  return h(onClick ? 'button' : 'span', {
    type: onClick ? 'button' : undefined,
    className: `hermes-fleet-slot is-${state}`,
    title, 'aria-label': title, onClick, onMouseEnter,
    'data-runner-slot': slotId || undefined
  })
}

// CPU as load per core when the core count is known; RAM used; disk free. Grey when absent, "N min old" when stale.
export function loadParts(load, now = Date.now()) {
  if (!load) return null
  const parts = []
  if (typeof load.cpu_load1 === 'number')
    parts.push(load.cpus
      ? { key: 'cpu', label: 'CPU', pct: Math.min(100, Math.round((100 * load.cpu_load1) / load.cpus)), text: `${Math.round((100 * load.cpu_load1) / load.cpus)}%` }
      : { key: 'cpu', label: 'load', pct: null, text: String(load.cpu_load1) })
  if (typeof load.mem_used_pct === 'number')
    parts.push({ key: 'ram', label: 'RAM', pct: Math.round(load.mem_used_pct), text: `${Math.round(load.mem_used_pct)}%` })
  if (typeof load.disk_free_gb === 'number') {
    const used = load.disk_total_gb ? Math.round(100 * (1 - load.disk_free_gb / load.disk_total_gb)) : null
    parts.push({ key: 'disk', label: 'Disk', pct: used, text: `${Math.round(load.disk_free_gb)} GB free` })
  }
  if (!parts.length) return null
  return { parts, stale: load.stale ? `${ago(now - load.at).replace(' ago', '')} old` : null }
}

function Meters({ load, now }) {
  const v = loadParts(load, now)
  if (!v) return h('span', { className: 'hermes-fleet-load is-none' }, 'load not reported')
  return h('span', { className: `hermes-fleet-load${v.stale ? ' is-stale' : ''}` },
    ...v.parts.map((p) => h('span', { key: p.key, className: 'hermes-fleet-meter', 'data-meter': p.key, title: `${p.label} ${p.text}` },
      `${p.label} `,
      p.pct == null ? null : h('span', { className: `hermes-fleet-bar${p.pct >= 90 ? ' is-high' : ''}` },
        h('span', { style: { width: `${p.pct}%` } })),
      ` ${p.text}`)),
    v.stale ? h('span', { className: 'hermes-fleet-meter' }, `(${v.stale})`) : null)
}

function openCard(run) {
  const hook = globalThis.__hermesKanbanOpenTask
  if (typeof hook === 'function' && hook(run.task, run.board, null) !== false) return
  host.navigate?.('/kanban')
}

function GatewayRow({ m, now, investigator, route }) {
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
    h('span', { className: 'hermes-fleet-meta' }, [meta, m.reason].filter(Boolean).join(' · ')),
    h(Meters, { load: m.load, now }),
    investigator && route ? h(Investigate, { m, now, investigator, route }) : null
  )
}

const INV_OPEN = ['queued', 'created', 'triage', 'todo', 'ready', 'scheduled', 'running', 'review']

// What the row says about its machine's latest investigation card, and what a click does.
export function investigationView(inv, now = Date.now()) {
  if (!inv) return { state: 'none', text: '', button: 'Investigate', opens: false }
  const id = inv.task ? ` · ${inv.task}` : ''
  if (INV_OPEN.includes(inv.status))
    return { state: 'running', text: `Investigating${id}`, button: inv.task ? 'Open investigation' : 'Investigation queued', opens: !!inv.task }
  if (inv.status === 'blocked')
    return { state: 'blocked', text: `Needs you${inv.verdict ? ` · ${text(inv.verdict)}` : id}`, button: 'Open investigation', opens: !!inv.task }
  const when = inv.completed_at || inv.created_at
  return {
    state: 'done',
    text: `Investigated${when ? ` ${ago(now - when * 1000)}` : ''}${inv.verdict ? ` · ${text(inv.verdict)}` : id}`,
    button: 'Investigate', opens: false, result: !!inv.task
  }
}

// POST /v1/investigations on the gateway that served the gateway section. The gateway creates the card
// ON the machine (placement=<machine>) or returns the one already open, so a second click never duplicates.
export function createInvestigator(ctx) {
  const pending = new Map()
  async function start(route, m) {
    if (!validRoute(route)) throw Object.assign(new Error('No gateway route for this machine'), { safe: true })
    if (pending.has(m.name)) return pending.get(m.name)
    const load = m.load ? Object.fromEntries(
      ['cpu_load1', 'cpus', 'mem_used_pct', 'disk_free_gb', 'disk_total_gb'].filter((k) => typeof m.load[k] === 'number').map((k) => [k, m.load[k]])) : null
    const p = ctx.rest(`/v1/investigations?profile=${encodeURIComponent(route.targetProfile)}`, {
      method: 'POST', body: { machine: m.name, load }, connectionId: route.connectionId, timeoutMs: 30000
    }).then((res) => {
      if (!res?.investigation) throw Object.assign(new Error('Invalid investigation response'), { safe: true })
      return res
    })
    pending.set(m.name, p)
    try { return await p } finally { pending.delete(m.name) }
  }
  return { start }
}

function Investigate({ m, now, investigator, route }) {
  const [local, setLocal] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  // The fleet poll is the truth once it knows about the card; until then show what the POST returned.
  const inv = m.investigation || local
  const v = investigationView(inv, now)
  const open = (i) => i?.task && openCard({ task: i.task, board: i.board || 'default' })
  return h('span', { className: `hermes-fleet-inv is-${v.state}`, 'data-investigate': m.name },
    h('button', {
      type: 'button', disabled: busy || (v.state === 'running' && !v.opens),
      'data-investigate-action': m.name,
      title: v.opens ? 'Open the investigation card' : `Start a card on ${m.name} that finds what is loading it and fixes what is safe`,
      onClick: async () => {
        if (v.opens) return open(inv)
        setBusy(true)
        setError('')
        try {
          const res = await investigator.start(route, m)
          setLocal(res.investigation)
          open(res.investigation)
        } catch (e) {
          setError(e?.safe ? e.message : `Could not start: ${text(e?.message || 'error')}`)
        } finally { setBusy(false) }
      }
    }, busy ? 'Starting…' : v.button),
    v.text ? h(v.result ? 'button' : 'span', {
      type: v.result ? 'button' : undefined, className: 'hermes-fleet-inv-text',
      title: v.result ? 'Open the investigation result' : undefined,
      onClick: v.result ? () => open(inv) : undefined
    }, v.text) : null,
    error ? h('span', { role: 'alert', className: 'hermes-fleet-inv-error' }, error) : null)
}

function CiRow({ m, now, repairClient, route, sourceError }) {
  const [openKey, setOpenKey] = useState(null)
  const interactive = !!(repairClient && route)
  const busy = m.slots.filter((s) => s.state === 'busy').length
  const offline = m.slots.filter((s) => s.state === 'offline').length
  const open = m.slots.find((s, i) => (s.id || s.runner || String(i)) === openKey)
  return h('div', { className: 'hermes-fleet-row', 'data-machine': m.name },
    h('span', { className: 'hermes-fleet-name' }, text(m.name) + (m.ephemeral ? ' (ephemeral)' : '')),
    h('span', { className: 'hermes-fleet-slots' },
      ...m.slots.map((s, i) => {
        const key = s.id || s.runner || String(i)
        return h(Slot, {
          key, state: s.state, slotId: s.id,
          title: `${text(s.runner)} · ${SLOT_TITLE[s.state]}\n${(s.labels || []).join(', ')}`,
          // Hover already opened this slot's popover, so a click must open, not toggle:
          // a real pointer always enters before it clicks.
          onClick: interactive ? () => setOpenKey(key) : undefined,
          onMouseEnter: interactive ? () => setOpenKey(key) : undefined
        })
      })),
    h('span', { className: 'hermes-fleet-count' }, `${busy}/${m.slots.length}`),
    h('span', { className: 'hermes-fleet-meta' }, [m.labels.join(', '), offline ? `${offline} offline` : null].filter(Boolean).join(' · ')),
    m.load ? h(Meters, { load: m.load, now }) : null,
    ...m.warnings.map((w) => h('div', { key: w, className: 'hermes-fleet-warning' }, `⚠ ${text(w)}`)),
    open ? h(SlotRepair, { key: openKey, slot: open, route, repairClient, sourceError }) : null
  )
}

function SlotRepair({ slot, route, repairClient, sourceError }) {
  const [prep, setPrep] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let cancel = false
    repairClient.prepare(route, slot, sourceError).then(
      (p) => { if (!cancel) setPrep(p) },
      (e) => { if (!cancel) setError(e?.safe ? e.message : 'Could not check repair status') }
    )
    return () => { cancel = true }
  }, [slot.id])
  return h('div', {
    className: 'hermes-fleet-pop', role: 'dialog', 'aria-label': `Actions for ${text(slot.runner)}`
  },
    h('div', { className: 'hermes-fleet-pop-title' }, text(slot.runner)),
    h('button', {
      type: 'button',
      'data-repair-action': slot.id || '',
      'data-repair-ready': prep ? '1' : '0',
      disabled: !prep?.enabled || busy,
      title: prep?.reason || prep?.label || 'Checking repair status',
      onClick: async () => {
        if (!prep?.enabled || busy || !prep.row) return
        setBusy(true)
        setError('')
        try { await repairClient.repair(prep.row) }
        catch (e) { setError(e?.safe ? e.message : 'Could not open repair session') }
        finally { setBusy(false) }
      }
    }, busy ? 'Opening repair session…' : (prep?.label || 'Repair in new session')),
    error ? h('div', { role: 'alert' }, error) : null,
    prep && !prep.enabled ? h('div', { className: 'hermes-fleet-note' }, prep.reason) : null
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

export function Panel({ view, now, repairClient, investigator }) {
  const s = summarize(view)
  return h('div', { className: 'hermes-fleet' },
    h('div', { className: 'hermes-fleet-head' },
      h('span', null, 'Gateway workers'),
      h('span', null, `${s.gBusy} / ${s.gSlots} slots busy`)),
    h(SectionNote, { section: view.gateways }),
    ...view.gateways.machines.map((m) => h(GatewayRow, { key: m.name, m, now, investigator, route: view.gateways.route })),
    h('div', { className: 'hermes-fleet-head' },
      h('span', null, 'CI workers (GitHub Actions)'),
      h('span', null, `${s.cBusy} / ${s.cSlots} busy${s.cOffline ? ` · ${s.cOffline} offline` : ''}`)),
    h(SectionNote, { section: view.ci }),
    ...view.ci.machines.map((m) => h(CiRow, {
      key: m.name, m, now, repairClient, route: view.ci.route, sourceError: view.ci.error
    })),
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
  const repairClient = useMemo(() => createRepairClient(ctx), [ctx])
  const investigator = useMemo(() => createInvestigator(ctx), [ctx])
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
    }, view ? h(Panel, { view, now, repairClient, investigator }) : h('div', { className: 'hermes-fleet-note' }, query.error ? `Unavailable: ${text(query.error.message)}` : 'Loading…'))
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
.hermes-fleet-load{grid-column:2/4;display:flex;flex-wrap:wrap;gap:10px;font-size:11px;color:var(--ui-text-secondary);font-variant-numeric:tabular-nums}
.hermes-fleet-load.is-none,.hermes-fleet-load.is-stale{color:var(--ui-text-tertiary)}
.hermes-fleet-meter{display:inline-flex;align-items:center;gap:3px;white-space:nowrap}
.hermes-fleet-bar{display:inline-block;width:36px;height:5px;border-radius:3px;background:var(--ui-stroke-secondary);overflow:hidden}
.hermes-fleet-bar>span{display:block;height:100%;background:var(--ui-text-secondary)}
.hermes-fleet-bar.is-high>span{background:#d9822b}
.hermes-fleet-warning{color:var(--ui-text-secondary)}
.hermes-fleet-inv{grid-column:2/4;display:flex;flex-wrap:wrap;align-items:center;gap:6px;font-size:11px;color:var(--ui-text-tertiary)}
.hermes-fleet-inv>button:first-child{font-size:11px;padding:0 6px;border:1px solid var(--ui-stroke-secondary);border-radius:4px;background:transparent;color:var(--ui-text-secondary);cursor:pointer}
.hermes-fleet-inv>button:disabled{opacity:.55;cursor:default}
.hermes-fleet-inv-text{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:420px;background:none;border:0;padding:0;color:inherit;font:inherit;text-align:left}
button.hermes-fleet-inv-text{cursor:pointer;text-decoration:underline dotted}
.hermes-fleet-inv.is-running .hermes-fleet-inv-text{color:var(--ui-text-secondary)}
.hermes-fleet-inv.is-blocked .hermes-fleet-inv-text{color:#d9822b}
.hermes-fleet-inv-error{color:#d64545}
.hermes-fleet-note,.hermes-fleet-foot{font-size:11px;color:var(--ui-text-tertiary)}
.hermes-fleet-foot{margin-top:8px}
.hermes-fleet-pop{grid-column:1/-1;display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:2px 0 4px;padding:6px;border:1px solid var(--ui-stroke-secondary);border-radius:6px;background:var(--ui-bg)}
.hermes-fleet-pop-title{font-weight:600}
.hermes-fleet-pop button{font-size:11px}
.hermes-fleet-pop button:disabled{opacity:.55}
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

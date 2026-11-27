// Runners: machines with a fixed number of slots, and what is running in them.
// Gateway workers (card runs) and CI workers (GitHub Actions) from GET /v1/fleet.
import {
  host,
  useQuery,
  useQueryClient,
  STATUSBAR_AREAS,
  PALETTE_AREA,
  Popover,
  PopoverTrigger,
  PopoverContent,
  StatusDot
} from '@hermes/plugin-sdk'
import { createElement as h, useState, useEffect, useMemo, useRef } from 'react'

const BAR =
  'inline-flex h-full items-center gap-1.5 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
export const POLL_OPEN_MS = 15000
// Closed too, so opening the panel always shows data at most 15s old. Cheap: the gateway
// serves a 5s cached build; nothing polls while the window is hidden.
export const POLL_CLOSED_MS = 15000
// Pushes (plugins/runners and plugins/throughput dashboards): `plugin.runners.fleet` carries a gateway's fleet when it
// changes (checked every 15 s there, a heartbeat every 60 s) and `plugin.throughput.snapshot` its throttle once a
// minute. While pushes from a route's gateway keep arriving (the last within PUSH_TRUST_MS) that route is not polled,
// only read every SAFETY_MS (inside the gateway's 5-minute interest window, so it keeps pushing); when they stop, the
// 15 s polls resume. A push is matched to a route by the gateway's `feed` id (host:pid, on every GET /v1/fleet answer)
// and the profile it was built for.
export const FLEET_EVENT = 'plugin.runners.fleet'
export const SNAPSHOT_EVENT = 'plugin.throughput.snapshot'
export const PUSH_TRUST_MS = 120000
export const SAFETY_MS = 240000
export const BACKOFF_MAX_MS = 300000
export const REQUEST_TIMEOUT_MS = 5000
const text = (v) => String(v ?? '').slice(0, 300)
const RANK = { ready: 0, stale: 1, unknown: 2, unavailable: 3, not_configured: 4 }

function validRoute(r) {
  return r && ['connectionId', 'targetProfile'].every((k) => typeof r[k] === 'string' && r[k].trim())
}
const routeKey = (r) => JSON.stringify([r.connectionId, r.targetProfile])

// Re-fetch every profile of a connection at most this often once its CI owner is known.
export const DISCOVERY_MS = 300000

// Per-route last-known data and backoff. A failing route is skipped until its next
// allowed time (15s, 30s, 60s ... 5 min), so a dead network or gateway costs at most one
// request per backoff step, and its last data stays on screen marked stale.
//
// One request per connection per poll: the gateway section is machine-wide and only the
// owner profile serves CI (the others answer ci.state 'unavailable'), so asking every
// profile of a gateway returns the same data N times, and on a slow link the N requests
// time out together. Each connection polls one lead route: the CI owner, else `default`,
// else the first. All its routes are fetched (a discovery sweep) until a sweep has had an
// answer and the owner is known, every DISCOVERY_MS, and on the poll after the lead fails.
// While the lead is backing off, one healthy route keeps the gateway section fresh.
export function createTransport(ctx, hostRef = host, clock = () => Date.now()) {
  const routes = new Map()
  const connections = new Map()
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
      const cur = routes.get(key) || {}
      routes.set(key, { route, body, at: clock(), fetchedAt: clock(), failures: 0, nextAt: 0, liveAt: cur.liveAt, pushHash: typeof body.hash === 'string' ? body.hash : undefined, dirty: false })
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
  async function pollConnection(id, list) {
    const now = clock()
    const c = connections.get(id) || { owner: null, sweptAt: -Infinity, answered: false, afterFailure: false }
    connections.set(id, c)
    const state = (r) => routes.get(routeKey(r))
    if (c.owner && !list.some((r) => routeKey(r) === c.owner)) c.owner = null
    const owner = c.owner && list.find((r) => routeKey(r) === c.owner)
    const byDefault = list.find((r) => r.targetProfile === 'default')
    const lead = owner || byDefault || list[0]
    // The lead's gateway is pushing this route's fleet: no request until the safety read (or a push says "changed").
    const ls = state(lead)
    if (ls?.body && !ls.dirty && now - (ls.liveAt ?? -Infinity) < PUSH_TRUST_MS && now - (ls.fetchedAt ?? -Infinity) < SAFETY_MS) return
    // Until the owner is known, sweep every poll; if a sweep got answers but no profile
    // serves CI, settle on the lead and keep re-sweeping only every DISCOVERY_MS.
    const sweep = (!owner && !c.answered) || c.afterFailure || now - c.sweptAt >= DISCOVERY_MS
    const targets = [lead]
    if (sweep) {
      targets.splice(0, 1, ...list)
      c.sweptAt = now
      c.afterFailure = false
    } else if (state(lead)?.failures) {
      const healthy = [byDefault, ...list].find((r) => r && r !== lead && state(r)?.body && !state(r).failures)
      if (healthy) targets.push(healthy)
    }
    const before = new Map(targets.map((r) => [routeKey(r), state(r)]))
    await Promise.all(targets.map(fetchRoute))
    for (const r of targets) {
      const s = state(r)
      if (!s?.body || s.failures || s.at < now) continue // skipped (backoff), failed or old
      c.answered = true
      if (s.body.ci.state !== 'unavailable') c.owner ||= routeKey(r)
      else if (c.owner === routeKey(r) && s.body.ci.reason === 'owner_profile_mismatch') c.owner = null
    }
    // A new lead failure (not a backoff skip): the next poll asks the other profiles.
    if (!sweep && (state(lead)?.failures || 0) > (before.get(routeKey(lead))?.failures || 0)) c.afterFailure = true
  }
  async function poll() {
    const list = (await hostRef.profileRoutes()).filter(validRoute)
    const keys = new Set(list.map(routeKey))
    for (const k of routes.keys()) if (!keys.has(k)) routes.delete(k)
    const groups = new Map()
    for (const r of list) groups.set(r.connectionId, [...(groups.get(r.connectionId) || []), r])
    for (const id of connections.keys()) if (!groups.has(id)) connections.delete(id)
    await Promise.all([...groups].map(([id, g]) => pollConnection(id, g)))
    return merge([...routes.values()], clock())
  }
  // One `plugin.runners.fleet` push: apply it to every route whose last answer came from that gateway process and
  // profile. True when it changed what the panel shows (the caller then re-renders from view()).
  function applyPush(p) {
    if (!p || typeof p.feed !== 'string') return false
    const now = clock()
    let changed = false
    for (const [key, s] of routes) {
      if (s.body?.feed !== p.feed || (s.body.profile || 'default') !== (p.profile || 'default')) continue
      if (p.fleet && p.fleet.contract_version === 1 && p.fleet.gateways && p.fleet.ci) {
        routes.set(key, { ...s, body: { ...p.fleet, feed: p.feed }, at: now, failures: 0, nextAt: 0, liveAt: now, pushHash: p.hash, dirty: false })
        changed = true
      } else if (p.heartbeat) {
        // The gateway's fleet is still the one we hold (same hash as our last answer or push): current again. Any
        // other hash means we missed a change: read it on the next poll.
        const same = !!s.pushHash && s.pushHash === p.hash
        routes.set(key, { ...s, liveAt: now, ...(same ? { at: now } : { dirty: true }) })
        changed = changed || same
      } else {
        routes.set(key, { ...s, liveAt: now, dirty: true }) // too big to push: read it on the next poll
      }
    }
    return changed
  }
  return { poll, routes, applyPush, view: () => merge([...routes.values()], clock()) }
}

// One transport per plugin ctx, not per mount. The status bar wraps each contribution in a
// fresh component type whenever its contribution list changes, so the chip remounts often;
// a per-mount transport forgot the CI owner and swept every profile on each remount.
const transports = new WeakMap()
export function transportFor(ctx) {
  let t = transports.get(ctx)
  if (!t) transports.set(ctx, (t = createTransport(ctx)))
  return t
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
  return { gateways: withEvents(withOwnCaps(pick('gateways'), sources), sources), ci: pick('ci'), at: now }
}

// Only the gateway on the autoscaler's host has lifecycle events; every other one answers []. Take them from
// the freshest route that has any, so the timeline does not come and go with whichever gateway answered last.
function withEvents(section, sources) {
  const has = (g) => Array.isArray(g?.events) && g.events.length > 0
  const from = sources.filter((s) => has(s.body?.gateways)).sort((a, b) => (b.at || 0) - (a.at || 0))[0]
  if (!from) return section
  const g = from.body.gateways
  const machines = section.machines.filter((m) => m.state !== 'launching')
  const names = new Set(machines.map((m) => m.name))
  return { ...section, machines: [...machines, ...(g.machines || []).filter((m) => m.state === 'launching' && !names.has(m.name))],
    events: g.events, events_since: g.events_since, launch_ready_s: g.launch_ready_s }
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

const isHub = (m) => m.name === 'hub' || (m.labels || []).includes('machine:hub')
const isLaunching = (m) => m.state === 'launching' || holdWord(m) === 'enrolling'

export function summarize(view, throttle = null) {
  // A stopped machine (held and off) has no capacity: it is left out of every slot total.
  const g = (view.gateways.machines || []).filter((m) => m.state !== 'stopped' && !isLaunching(m)), c = view.ci.machines || []
  const gBusy = g.reduce((n, m) => n + m.running.length, 0)
  const gSlots = g.reduce((n, m) => n + (m.slots ?? m.running.length), 0)
  const slots = c.flatMap((m) => m.slots)
  const cBusy = slots.filter((s) => s.state === 'busy').length
  const cOffline = slots.filter((s) => s.state === 'offline').length
  const lost = g.filter((m) => m.state === 'lost').length
  const unknown = [view.gateways, view.ci].some((s) => s.state !== 'ready' && s.state !== 'not_configured')
  const gWaiting = typeof view.gateways.waiting === 'number' ? view.gateways.waiting : null
  // Status-bar meters: the hub's card slots and the worker gateways' card slots. A machine
  // without a slot cap (pinned-only Macs) has no capacity to fill, so it is left out.
  const capped = g.filter((m) => typeof m.slots === 'number' && m.slots > 0)
  const hub = capped.filter(isHub), workers = capped.filter((m) => !isHub(m))
  // `usable`: slots a token or pacing limit leaves startable (all of them when no limit is known).
  // Present only with throttle data, so a view without the throughput plugin keeps its old shape.
  const meter = (ms) => ({
    busy: ms.reduce((n, m) => n + m.running.length, 0),
    slots: ms.reduce((n, m) => n + m.slots, 0),
    ...(throttle ? { usable: ms.reduce((n, m) => n + (slotLimit(m, limitRow(throttle, m.name))?.allowed ?? m.slots), 0) } : {})
  })
  return {
    gBusy, gSlots, gWaiting, cBusy, cSlots: slots.length, cOffline, lost,
    hub: meter(hub), workers: meter(workers),
    tone: lost ? 'bad' : unknown ? 'warn' : gBusy + cBusy ? 'good' : 'muted'
  }
}

// ── token throttle and pacing: slots a limit will not let run ────────────────────────────────────────────
// GET/PUT /api/plugins/throughput/v1/throttle (plugins/throughput: throttle.py, slots.py). `slots.gateways[name]`
// carries each gateway's token cost per slot and `allowed`, the slots its share of the token cap pays for.
// Slots beyond `allowed` are crossed out; pacing shows as text (pools paced, card starts held), not as slots. Without the throughput plugin (or with an
// older one) there is no `slots` and nothing is crossed out.
export const THROTTLE_PATH = '/api/plugins/throughput/v1/throttle'

function bridge(path, opts = {}) {
  const api = typeof window !== 'undefined' ? window.hermesDesktop?.api : null
  if (!api) return Promise.reject(new Error('Hermes desktop bridge unavailable'))
  let connectionId
  try { connectionId = host.activeConnectionId?.() || host.state?.connectionId?.get?.() || undefined } catch { connectionId = undefined }
  return api({ path, timeoutMs: REQUEST_TIMEOUT_MS, ...opts, ...(connectionId ? { connectionId } : {}) })
}

export const limitRow = (throttle, name) => throttle?.slots?.gateways?.[name] || null

export function fmtTokens(n) {
  if (typeof n !== 'number' || !isFinite(n)) return '–'
  return n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(Math.round(n))
}

// How many of a machine's slots its share of the token cap pays for. null = the throttle limits nothing here.
// The pacing governor is not turned into slots: its per-machine limits are not published (plugins/throughput/slots.py).
export function slotLimit(m, row) {
  if (!row || typeof row.allowed !== 'number' || typeof m.slots !== 'number') return null
  const allowed = Math.max(0, Math.min(m.slots, Math.floor(row.allowed)))
  const why = `token throttle allows ${allowed} of ${m.slots} slots here: ${fmtTokens((row.cap_per_s || 0) * 60)} tok/min share ÷ ${fmtTokens((row.tokens_per_slot_per_s || 0) * 60)} per slot${row.enforced === false ? ' (estimate; that gateway enforces its own count)' : ''}`
  return { allowed, throttled: Math.max(0, m.slots - allowed), why }
}

// The cap line: what caps the fleet (a person's cap, the subscription budget, or nothing), the fleet rate, this
// gateway's open slots, pacing. `user_cap_per_s` and `slot_gate` come from a throughput plugin with the slot gate.
export function throttleLine(throttle) {
  if (!throttle) return ''
  const capPS = typeof throttle.cap_per_s === 'number' ? throttle.cap_per_s : null
  const userPS = typeof throttle.user_cap_per_s === 'number' ? throttle.user_cap_per_s : null
  const budgetPS = typeof throttle.budget?.budget_per_s === 'number' ? throttle.budget.budget_per_s : null
  const by = capPS == null ? '' : userPS != null && userPS <= capPS + 1e-6 ? ' (yours)' : budgetPS != null ? ' (budget)' : ''
  const head = capPS == null ? 'No token cap' : capPS <= 0 ? 'Cap 0 · holding every call' : `Cap ${fmtTokens(capPS * 60)} tok/min${by}`
  const budget = budgetPS != null && capPS != null && capPS > 0 && Math.abs(budgetPS - capPS) > 1e-6 ? ` · budget ${fmtTokens(budgetPS * 60)}` : ''
  const rate = typeof throttle.rate_per_s === 'number' ? ` · fleet ${fmtTokens(throttle.rate_per_s * 60)} tok/min` : ''
  const g = throttle.slot_gate
  const gate = g && typeof g.allowed === 'number'
    ? ` · here ${typeof g.running === 'number' ? g.running : '?'}/${g.allowed} slots${typeof g.per_slot_per_s === 'number' ? ` at ${fmtTokens(g.per_slot_per_s * 60)}/slot` : ' (unmeasured)'}` : ''
  const paced = (throttle.pools || []).filter((x) => x.pacing === 'paced' || x.pacing === 'held')
    .map((x) => `${x.provider} ${x.pacing}${x.pacing === 'paced' && typeof x.pacing_factor === 'number' ? ` ${Math.round(x.pacing_factor * 100)}%` : ''}`)
  const held = throttle.slots?.pacing?.held_starts
  const holds = typeof held === 'number' && held > 0
    ? ` · ${held} card start${held === 1 ? '' : 's'} held by pacing in the last ${Math.round((throttle.slots.pacing.window_s || 300) / 60)} min` : ''
  return `${head}${budget}${rate}${gate}${paced.length ? ` · pacing: ${paced.join(', ')}` : ''}${holds}`
}

// Thousands of tokens per minute typed in the cap field: '150' or '150k' = 150k, '1.5m' = 1.5M; '' = no cap.
export function parseCapK(raw) {
  const s = String(raw ?? '').trim().toLowerCase()
  if (s === '' || s === 'off' || s === 'none') return null
  const m = s.match(/^([0-9]+(?:\.[0-9]+)?)\s*([km]?)$/)
  if (!m) return undefined
  return Number(m[1]) * (m[2] === 'm' ? 1e6 : 1e3)
}

// A person's token cap for the whole fleet, in thousands of tokens per minute. Enter (or leaving the field) sets
// it; "No cap" removes it (the subscription budget alone then caps). onThrottle receives tokens/min or null.
export function ThrottleBar({ throttle, onThrottle, error }) {
  const [draft, setDraft] = useState(null)
  const [bad, setBad] = useState(false)
  if (!throttle) return null
  const userPS = typeof throttle.user_cap_per_s === 'number' ? throttle.user_cap_per_s : null
  const shown = userPS == null ? '' : String(Math.round(userPS * 60 / 1000))
  const commit = () => {
    if (draft == null) return
    const v = parseCapK(draft)
    if (v === undefined) { setBad(true); return }
    setBad(false); setDraft(null)
    const same = v == null ? userPS == null : userPS != null && Math.abs(userPS * 60 - v) < 500
    if (!same) onThrottle?.(v)
  }
  return h('div', { className: 'hermes-fleet-throttle', 'data-slot': 'runners-throttle' },
    h('label', { className: 'hermes-fleet-throttle-cap' }, 'Token cap ',
      h('input', {
        type: 'text', inputMode: 'decimal', size: 6, placeholder: 'none', value: draft ?? shown, disabled: !onThrottle,
        'aria-label': 'Fleet token cap, thousands of tokens per minute', 'aria-invalid': bad ? 'true' : undefined,
        onChange: (e) => setDraft(e.target.value), onBlur: commit,
        onKeyDown: (e) => { if (e.key === 'Enter') commit(); if (e.key === 'Escape') { setDraft(null); setBad(false) } }
      }), 'k tok/min'),
    h('button', { type: 'button', className: 'hermes-fleet-throttle-off', disabled: !onThrottle || userPS == null, onClick: () => { setDraft(null); setBad(false); onThrottle?.(null) } }, 'No cap'),
    h('span', { className: 'hermes-fleet-throttle-text' }, bad ? 'Type thousands of tokens per minute, e.g. 150 or 1.5m' : throttleLine(throttle)),
    error ? h('span', { className: 'hermes-fleet-warning' }, `⚠ ${text(error)}`) : null)
}

const SLOT_TITLE = { busy: 'busy', idle: 'free', offline: 'offline', unknown: 'state unknown' }

function Slot({ state, title, onClick, onMouseEnter, slotId, throttled }) {
  return h(onClick ? 'button' : 'span', {
    type: onClick ? 'button' : undefined,
    className: `hermes-fleet-slot is-${state}${throttled ? ' is-throttled' : ''}`,
    'data-throttled': throttled ? 'true' : undefined,
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

// Column headers for the gateway grid: the unit of each number lives here, not in every row.
const GW_COLS = [
  ['name', 'Machine'], ['slots', 'Slots'],
  ['waiting', 'Waiting', 'Unleased ready cards that match this machine and need a slot here'],
  ['ineligible', "Can't run", 'Unleased ready cards whose placement, skills or pins do not match this machine'],
  ['cpu', 'CPU', '1-min load ÷ cores'], ['ram', 'RAM', 'Memory in use'], ['disk', 'Disk free'],
  ['tok', 'Tok/slot', 'In+out tokens per busy slot per minute, last 15 min'],
  ['count', 'Busy'], ['info', '']
]

function GatewayHead() {
  return h('div', { className: 'hermes-fleet-cols' },
    ...GW_COLS.map(([k, label, title]) => h('span', { key: k, 'data-col': k, title }, label)))
}

// One grid cell per meter, so the bars and numbers line up under their header whatever the value.
function MeterCell({ k, v }) {
  const p = v?.parts.find((x) => x.key === k)
  const cls = `hermes-fleet-cell hermes-fleet-meter${v?.stale ? ' is-stale' : ''}`
  if (!p) return h('span', { className: `${cls} is-none`, 'data-meter': k, title: v ? undefined : 'load not reported' }, '–')
  const value = k === 'disk' ? p.text.replace(' free', '') : p.text
  return h('span', { className: cls, 'data-meter': k, title: `${p.label} ${p.text}${v.stale ? ` (${v.stale})` : ''}` },
    p.pct == null ? null : h('span', { className: `hermes-fleet-bar${p.pct >= 90 ? ' is-high' : ''}` },
      h('span', { style: { width: `${p.pct}%` } })),
    h('span', { className: 'hermes-fleet-num' }, value))
}

// A held machine's word: the autoscaler holds a new machine while it enrolls ("worker-scaler: enrolling <id>: ..."),
// which is not a drain; any other hold is.
export function holdWord(m) {
  return m?.state === 'draining' && typeof m.hold_reason === 'string' && /^worker-scaler: enrolling\b/.test(m.hold_reason)
    ? 'enrolling' : m?.state
}

function GatewayRow({ m, now, limit }) {
  const lim = slotLimit(m, limit)
  const over = (i) => !!lim && i >= lim.allowed
  const free = m.slots == null ? 0 : Math.max(0, m.slots - m.running.length)
  const freeState = m.state === 'active' ? 'idle' : m.state === 'draining' ? 'draining' : 'unknown'
  const v = loadParts(m.load, now)
  // A held machine that is still up or still holds a card. Once it is off ('stopped') it has no row: StoppedNote.
  const held = m.state === 'draining'
  const word = holdWord(m)
  const holdTail = word === 'enrolling' ? 'a new machine, takes cards once it passes its checks' : 'finishes running cards, takes no new ones'
  const drainNote = held
    ? m.hold_reason === undefined
      ? `${word}: reason unavailable (dashboard older than hold metadata); ${holdTail}`
      : `${word}: ${m.hold_reason ? text(m.hold_reason) : 'no reason recorded'} (held by ${m.hold_owner ? text(m.hold_owner) : m.hold_by ? text(m.hold_by) : 'unknown'}, ${typeof m.hold_at === 'number' ? ago(now - m.hold_at) : 'time unknown'}); ${holdTail}`
    : null
  const meta = [
    m.pinned_only ? 'pinned only' : null,
    m.slots == null ? 'slots not set' : m.slots_source === 'local' ? 'slots from local cap' : null,
    lim && lim.throttled ? `${lim.throttled} slot(s) crossed out: ${lim.why}` : null,
    drainNote || m.reason,
    v ? (v.stale ? `load ${v.stale}` : null) : 'load not reported'
  ].filter(Boolean).join(' · ')
  // Flags and reasons are a small (i) at the end of the row; its tooltip is drawn by CSS from data-tip
  // (native title tooltips do not show inside the Desktop popover).
  return h('div', { className: `hermes-fleet-row is-${m.state}`, 'data-machine': m.name },
    h('span', { className: 'hermes-fleet-name' }, h('span', { className: 'hermes-fleet-machine-name' }, text(m.name)),
      drainNote ? h('span', { className: 'hermes-fleet-drain-label', title: drainNote }, word) : null),
    h('span', { className: 'hermes-fleet-slots' },
      ...m.running.map((r, i) => h(Slot, {
        key: `${r.board}/${r.task}`, state: m.state === 'lost' ? 'lost' : 'busy', throttled: over(i),
        title: `${r.task} · ${text(r.title)}\nboard ${r.board}${r.started_at ? ` · running ${ago(now - r.started_at * 1000)}` : ''}${over(i) ? `\nover the limit: ${lim.why}` : ''}`,
        onClick: () => openCard(r)
      })),
      ...Array.from({ length: free }, (_, i) => {
        const x = over(m.running.length + i)
        return h(Slot, { key: `free-${i}`, state: freeState, throttled: x, title: drainNote || (x ? `cannot start: ${lim.why}` : SLOT_TITLE[freeState]) })
      })),
    h('span', { className: 'hermes-fleet-cell hermes-fleet-waiting', title: m.waiting != null ? `${m.waiting} unleased ready card(s) for gateway-dispatched profiles match placement labels (including skill labels) on ${m.name}. Need a slot here; portable cards can also fit other machines. Does not include transient admission limits or machine availability.` : undefined }, m.waiting != null ? `${m.waiting}` : ''),
    h('span', { className: 'hermes-fleet-cell hermes-fleet-ineligible', title: m.ineligible != null ? `${m.ineligible} unleased ready card(s) for gateway-dispatched profiles cannot run here: placement labels, skill labels, machine pins or pinned-only policy do not match ${m.name}, or the card is another gateway's default-placement card that ${m.name} never picks up.` : undefined }, m.ineligible != null ? `${m.ineligible}` : ''),
    h(MeterCell, { k: 'cpu', v }), h(MeterCell, { k: 'ram', v }), h(MeterCell, { k: 'disk', v }),
    h('span', {
      className: `hermes-fleet-cell hermes-fleet-tok${limit?.cost_source === 'measured' ? '' : ' is-none'}`,
      title: typeof limit?.tokens_per_slot_per_s === 'number'
        ? (limit.cost_source === 'measured' ? 'measured on this machine, last 15 min' : 'fleet median: too little run time here to measure')
        : 'not measured'
    }, typeof limit?.tokens_per_slot_per_s === 'number' ? fmtTokens(limit.tokens_per_slot_per_s * 60) : '–'),
    h('span', { className: 'hermes-fleet-count' },
      m.slots == null ? `${m.running.length}` : `${m.running.length}/${m.slots}`),
    meta ? h('span', { className: 'hermes-fleet-info', role: 'img', tabIndex: 0, 'data-tip': meta, 'aria-label': `${text(m.name)}: ${meta}` }, 'i')
      : h('span', { className: 'hermes-fleet-info is-empty', 'aria-hidden': 'true' })
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

export const CI_GROUPS = ['Macs', 'EC2 Linux', 'Metta', 'Ephemeral', 'Other']

// Same rule as fleet.category on the gateway; used when an older gateway sends no category.
export function ciCategory(m) {
  if (CI_GROUPS.includes(m.category)) return m.category
  const name = (m.name || '').toLowerCase()
  const labels = new Set((m.slots || []).flatMap((s) => s.labels || []).map((l) => l.toLowerCase()))
  if (m.ephemeral) return 'Ephemeral'
  if (name.startsWith('metta')) return 'Metta'
  if (labels.has('macos') || /(^|-)mac(-|$)/.test(name)) return 'Macs'
  if (name.startsWith('ec2-') || name.startsWith('hermes-hub') || labels.has('linux')) return 'EC2 Linux'
  return 'Other'
}

// CI machines -> one group per category with its capacity and health, in CI_GROUPS order.
export function groupCi(machines) {
  const by = new Map()
  for (const m of machines) {
    const k = ciCategory(m)
    if (!by.has(k)) by.set(k, [])
    by.get(k).push(m)
  }
  return CI_GROUPS.filter((k) => by.has(k)).map((name) => {
    const list = by.get(name), slots = list.flatMap((m) => m.slots)
    const busy = slots.filter((s) => s.state === 'busy').length
    const offline = slots.filter((s) => s.state === 'offline').length
    const warnings = list.reduce((n, m) => n + m.warnings.length, 0)
    const cpus = list.map((m) => m.load?.cpu_load1).filter((v) => typeof v === 'number')
    return {
      name, machines: list, busy, total: slots.length, offline, warnings,
      health: warnings ? 'bad' : offline ? 'warn' : 'good',
      load1Max: cpus.length ? Math.max(...cpus) : null
    }
  })
}


const CI_PROVIDERS = { blacksmith: 'Blacksmith', namespace: 'Namespace', 'hub-ci': 'hub CI VMs' }
export const CI_STALE_MS = 5 * 60000

// The backend supplies dimensions. Fallbacks keep older connected gateways usable during rollout.
function ciDimensions(m) {
  const labels = (m.slots || []).flatMap((s) => s.labels || [])
  const name = (m.name || '').toLowerCase()
  return {
    os: m.os || (m.category === 'Macs' || labels.some((l) => /macos|(^|-)mac(-|$)/i.test(l)) || ciCategory(m) === 'Macs' ? 'mac' : 'linux'),
    provider: m.provider === undefined ? (name.startsWith('blacksmith') ? 'blacksmith' : name.startsWith('nsc-') ? 'namespace' : m.ephemeral ? 'hub-ci' : null) : m.provider,
    size: m.size || null
  }
}

export function ciModel(ci, now = Date.now()) {
  const sections = ['mac', 'linux'].map((os) => ({ os, name: os === 'mac' ? 'Mac' : 'Linux', fixed: [], elastic: [], unobserved: 0, readinessWarnings: [] }))
  let hidden = 0, historical = false
  const historicalTimes = []
  const displaySlot = (slot) => {
    // Source age applies to live slots too. A transport heartbeat or collector
    // tick cannot renew the GitHub observation behind them.
    if (slot.last_observed) historicalTimes.push(Date.parse(slot.last_observed.observed_at))
    if (slot.state !== 'unknown' || !['busy', 'idle', 'offline'].includes(slot.last_observed?.state)) return slot
    historical = true
    return { ...slot, state: slot.last_observed.state, authoritativeSlot: slot }
  }
  for (const m of ci.machines || []) {
    const dim = ciDimensions(m), section = sections.find((s) => s.os === dim.os) || sections[1]
    section.readinessWarnings.push(...(m.warnings || []))
    if (dim.provider) {
      let pool = section.elastic.find((p) => p.id === dim.provider)
      if (!pool) section.elastic.push(pool = { id: dim.provider, name: CI_PROVIDERS[dim.provider] || dim.provider, busy: 0, sizes: {} })
      for (const raw of m.slots || []) {
        const slot = displaySlot(raw)
        if (!['busy', 'idle', 'offline'].includes(slot.state)) { section.unobserved++; continue }
        // Idle/offline, current or last observed, is finished history; only a slot never read is unobserved.
        if (slot.state !== 'busy') { hidden++; continue }
        pool.busy++
        if (dim.size) pool.sizes[dim.size] = (pool.sizes[dim.size] || 0) + 1
      }
    } else for (const raw of m.slots || []) {
      const slot = displaySlot(raw)
      if (!['busy', 'idle', 'offline'].includes(slot.state)) { section.unobserved++; continue }
      const warning = slot.warning ?? (m.warnings || []).some((w) => w.startsWith(`${slot.runner}:`))
      section.fixed.push({ ...slot, machine: m.name, labels: m.labels || slot.labels || [], warning })
    }
  }
  for (const s of sections) {
    s.fixed.sort((a, b) => Number(b.warning) - Number(a.warning) || ({ busy: 0, idle: 1, offline: 2 }[a.state] - { busy: 0, idle: 1, offline: 2 }[b.state]) || (a.runner || '').localeCompare(b.runner || ''))
    s.online = s.fixed.filter((r) => r.state !== 'offline')
    s.offline = s.fixed.filter((r) => r.state === 'offline')
    s.busy = s.fixed.filter((r) => r.state === 'busy').length
    const ready = s.online.filter((r) => !r.warning)
    s.full = ready.length > 0 && ready.every((r) => r.state === 'busy')
    s.elastic.sort((a, b) => Object.keys(CI_PROVIDERS).indexOf(a.id) - Object.keys(CI_PROVIDERS).indexOf(b.id))
    s.running = s.elastic.reduce((n, p) => n + p.busy, 0)
    s.warnings = s.fixed.filter((r) => r.warning)
    s.title = `${s.name}: ${[
      `${s.busy}/${s.online.length} self-hosted busy`,
      s.offline.length ? `${s.offline.length} offline` : null,
      s.warnings.length ? `${s.warnings.map((r) => r.runner).join(', ')}: readiness check failed` : null,
      ...s.readinessWarnings.filter((w) => !s.warnings.some((r) => w.startsWith(`${r.runner}:`))),
      ...s.elastic.filter((p) => p.busy).map((p) => `${p.name} ${p.busy} running`),
      s.unobserved ? `${s.unobserved} not observed` : null
    ].filter(Boolean).join(' · ')}`
  }
  const times = [Date.parse(ci.observed_at), ...historicalTimes]
  const at = times.every(Number.isFinite) ? Math.min(...times) : NaN
  const age = Number.isFinite(at) ? Math.max(0, now - at) : null
  const stale = historical || !['ready', 'not_configured'].includes(ci.state) || (age != null && age > CI_STALE_MS)
  const minutes = age == null ? null : Math.round(age / 60000)
  return { sections, stale, minutes, age, hidden, total: sections.reduce((n, s) => n + s.busy + s.running, 0),
    tone: sections.some((s) => s.warnings.length || s.readinessWarnings.length) ? 'bad' : stale ? 'warn' : null,
    title: sections.map((s) => s.title).join('\n') + (stale ? `\nCI data ${minutes == null ? 'age unknown' : `${minutes} min old`}` : '') }
}

function CiPips({ section }) {
  return h('span', { className: 'hermes-ci-grid', 'aria-hidden': true }, ...section.fixed.map((r) => h('span', {
    key: r.id || r.runner, title: `${r.runner}: ${r.warning ? 'readiness check failed' : r.state}`,
    className: `hermes-ci-pip is-${r.warning ? 'warn' : r.state === 'busy' && section.full ? 'full' : r.state}${r.warning && r.state === 'busy' ? ' is-busy-warn' : ''}`
  })))
}

export function CiChip({ model }) {
  return h('span', { className: `hermes-ci-chip${model.stale ? ' is-stale' : ''}`, title: model.title },
    h('span', { className: 'hermes-ci-label' }, 'CI'),
    model.tone ? h('span', { className: `hermes-ci-dot is-${model.tone}`, 'data-ci-tone': model.tone }) : null,
    ...model.sections.flatMap((s, i) => [i ? h('span', { key: 'divider', className: 'hermes-ci-divider' }) : null,
      h('span', { key: s.os, className: 'hermes-ci-section', 'data-ci-os': s.os }, h(CiPips, { section: s }),
        s.running ? h('span', { className: 'hermes-ci-number' }, s.running) : null)]))
}

function CiFixedRow({ slot, section, repairClient, route, onOpen }) {
  const interactive = !!(repairClient && route)
  const title = `${slot.runner} · ${slot.warning ? 'readiness check failed' : SLOT_TITLE[slot.state]}\n${slot.labels.join(', ')}`
  return h('div', { className: 'hermes-ci-row', 'data-machine': slot.machine },
    h('span', { className: 'hermes-ci-name', title: slot.runner }, text(slot.runner)),
    h('span', { className: 'hermes-ci-row-detail' },
      h(interactive ? 'button' : 'span', { type: interactive ? 'button' : undefined, className: 'hermes-ci-slot-action',
        title, 'aria-label': title, 'data-runner-slot': slot.id,
        onClick: interactive ? () => onOpen(slot) : undefined, onMouseEnter: interactive ? () => onOpen(slot) : undefined
      }, h(CiPips, { section: { ...section, fixed: [slot] } })),
      h('span', { className: 'hermes-ci-labels', title: slot.labels.join(', ') }, slot.labels.join(', '))),
    h('span', { className: 'hermes-ci-count' }, `${slot.state === 'busy' ? 1 : 0}/1`))
}

function CiOfflineNote({ section, repairClient, route, onOpen }) {
  const interactive = !!(repairClient && route)
  return h('div', { className: 'hermes-ci-note' },
    section.offline.length ? `${section.offline.length} offline: ` : null,
    ...section.offline.flatMap((slot, i) => [i ? ', ' : null, h(interactive ? 'button' : 'span', {
      key: slot.id || slot.runner, className: 'hermes-ci-offline-name', type: interactive ? 'button' : undefined,
      title: `${slot.runner}: offline`, 'data-runner-slot': slot.id,
      onClick: interactive ? () => onOpen(slot) : undefined, onMouseEnter: interactive ? () => onOpen(slot) : undefined
    }, slot.runner)]),
    section.unobserved ? `${section.offline.length ? ' · ' : ''}${section.unobserved} not observed (no fresh reading)` : null)
}

export function CiPanel({ view, now = Date.now(), repairClient }) {
  const [openSlot, setOpenSlot] = useState(null)
  const model = ciModel(view.ci, now)
  const slotKey = (s) => s.id || s.runner
  const open = openSlot && model.sections.flatMap((s) => s.fixed).find((s) => slotKey(s) === openSlot)
  const onOpen = (slot) => setOpenSlot(slotKey(slot))
  return h('div', { className: 'hermes-ci-panel' },
    h('div', { className: 'hermes-ci-head' }, h('span', null, 'CI (GitHub Actions)'), h('span', null, `${model.total} running`)),
    h(SectionNote, { section: view.ci }),
    model.stale ? h('div', { className: 'hermes-ci-note', role: 'status' },
      `Last-observed CI display · ${model.minutes == null ? 'age unknown' : `${model.minutes} min old`}; not current capacity.`) : null,
    ...model.sections.map((s) => h('section', { key: s.os, 'data-ci-section': s.os },
      h('div', { className: 'hermes-ci-section-head' }, h('b', null, s.name), h(CiPips, { section: s }),
        h('span', null, `${s.busy}/${s.online.length} self-hosted busy${s.running ? ` · ${s.running} elastic running` : ''}`)),
      ...s.online.toSorted((a, b) => (a.runner || '').localeCompare(b.runner || '')).map((slot) => h(CiFixedRow, { key: slot.id || slot.runner, slot, section: s, repairClient, route: view.ci.route, onOpen })),
      ...s.elastic.map((p) => h('div', { key: p.id, className: 'hermes-ci-row', 'data-ci-provider': p.id },
        h('span', { className: 'hermes-ci-name' }, p.name, ' ', h('span', { className: 'hermes-ci-labels' }, 'elastic')),
        h('span', { className: 'hermes-ci-row-detail' },
          p.busy ? h('span', { className: 'hermes-ci-elastic-strip', 'aria-hidden': true }, ...Array.from({ length: p.busy }, (_, i) => h('i', { key: i }))) : null,
          h('span', { className: 'hermes-ci-labels' }, Object.entries(p.sizes).map(([size, count]) => `${count} × ${size}`).join(' · ') || (p.busy ? `${p.busy} running` : 'idle'))),
        h('span', { className: 'hermes-ci-count' }, p.busy))),
      h(CiOfflineNote, { section: s, repairClient, route: view.ci.route, onOpen }),
      s.readinessWarnings.length ? h('div', { className: 'hermes-ci-note' }, s.readinessWarnings.join(' · ')) : null)),
    open && repairClient && view.ci.route ? h('div', null,
      h('button', { type: 'button', onClick: () => setOpenSlot(null), 'aria-label': 'Close CI runner actions' }, 'Close actions'),
      h(SlotRepair, { key: slotKey(open), slot: open.authoritativeSlot || open, repairClient, route: view.ci.route, sourceError: view.ci.error })) : null,
    h('div', { className: 'hermes-ci-note' }, `${model.hidden} finished elastic registrations and ${view.ci.hidden_deregistered || 0} deregistered runners hidden`),
    h('div', { className: 'hermes-fleet-foot' }, `GitHub observations ${model.age == null ? 'age unknown' : ago(model.age)} · received ${ago(now - view.at)}`))
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

// ── worker lifecycle timeline ────────────────────────────────────────────────────────────────────────────
// `gateways.events` (plugins/runners fleet.worker_events: the autoscaler's ledger, last 24 h, `at` in epoch
// seconds). One tick per event on a 24 h axis ending now. Absent (older gateway) or empty: nothing is drawn.
export const TIMELINE_WINDOW_MS = 24 * 3600 * 1000
export const TIMELINE_LANES = 4
// Closest two ticks of one lane may sit, as a share of the axis: ~5 px of the 680 px minimum panel.
export const TIMELINE_GAP = 0.008
export const EVENT_WORDS = { launched: 'launched', ready: 'ready for scheduling', draining: 'draining', removed: 'removed' }
const LANE_PX = 10

// Share of the axis (0 = window start, 1 = now), or null outside the window.
export function timelineX(atMs, now, windowMs = TIMELINE_WINDOW_MS) {
  const x = (atMs - (now - windowMs)) / windowMs
  return Number.isFinite(x) && x >= 0 && x <= 1 ? x : null
}

// Local wall-clock HH:MM.
export function clockTime(ms) {
  const d = new Date(ms)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// "worker-3 · draining · consolidating spare capacity · 03:41"
export function eventTip(e) {
  return [text(e.worker), EVENT_WORDS[e.kind] || text(e.kind), text(e.reason).trim(), clockTime(e.at * 1000)].filter(Boolean).join(' · ')
}

// Events -> ticks `{key, worker, kind, at, x, pos, lane, tip}`, oldest first. `x` is the event's time on the
// axis; `pos` is where the tick is drawn. A worker keeps one lane for the whole window, so its life reads left
// to right. Workers are placed in order of their first event (then name), each in the lane where fewest of its
// ticks land within `gap` of a tick already there (lowest lane on a tie): workers acting at the same moment
// stack instead of covering each other. Ticks still closer than `gap` in one lane (one worker launched and
// ready within minutes, or more busy workers than lanes) are drawn `gap` apart, later ones moved right, so
// every tick keeps its own hover target. Same input, same output.
export function timelineTicks(events, now, { windowMs = TIMELINE_WINDOW_MS, lanes = TIMELINE_LANES, gap = TIMELINE_GAP } = {}) {
  if (!Array.isArray(events)) return []
  const ticks = []
  events.forEach((e, i) => {
    if (!e || typeof e.at !== 'number' || typeof e.worker !== 'string' || !e.worker.trim() || !(e.kind in EVENT_WORDS)) return
    const x = timelineX(e.at * 1000, now, windowMs)
    if (x == null) return
    ticks.push({ key: `${e.at}:${e.worker}:${e.kind}:${i}`, worker: e.worker, kind: e.kind, at: e.at, x, pos: x, lane: 0, tip: eventTip(e), i })
  })
  ticks.sort((a, b) => a.at - b.at || a.i - b.i)
  const byWorker = new Map()
  for (const t of ticks) byWorker.set(t.worker, [...(byWorker.get(t.worker) || []), t])
  const order = [...byWorker.keys()].sort((a, b) => byWorker.get(a)[0].at - byWorker.get(b)[0].at || (a < b ? -1 : a > b ? 1 : 0))
  const placed = Array.from({ length: Math.max(1, lanes) }, () => [])
  for (const worker of order) {
    const mine = byWorker.get(worker)
    const clashes = placed.map((xs) => mine.filter((t) => xs.some((x) => Math.abs(x - t.x) < gap)).length)
    const lane = clashes.indexOf(Math.min(...clashes))
    for (const t of mine) t.lane = lane
    placed[lane].push(...mine.map((t) => t.x))
  }
  for (let lane = 0; lane < placed.length; lane++) {
    const row = ticks.filter((t) => t.lane === lane)
    for (let i = 1; i < row.length; i++) row[i].pos = Math.max(row[i].x, row[i - 1].pos + gap)
    // Pushed past "now": pull the tail back from the right edge instead.
    if (row.length && row[row.length - 1].pos > 1) {
      row[row.length - 1].pos = 1
      for (let i = row.length - 2; i >= 0; i--) row[i].pos = Math.max(0, Math.min(row[i].pos, row[i + 1].pos - gap))
    }
  }
  return ticks.map(({ i, ...t }) => t)
}

// Hour labels at local round hours, every `stepHours`: [{x, label}].
export function timelineAxis(now, windowMs = TIMELINE_WINDOW_MS, stepHours = 6) {
  const start = now - windowMs, d = new Date(start), out = []
  d.setMinutes(0, 0, 0)
  if (d.getTime() < start) d.setHours(d.getHours() + 1)
  for (let guard = 0; d.getHours() % stepHours && guard < 24; guard++) d.setHours(d.getHours() + 1)
  for (let guard = 0; d.getTime() <= now && guard < 48; guard++, d.setHours(d.getHours() + stepHours))
    out.push({ x: timelineX(d.getTime(), now, windowMs), label: clockTime(d.getTime()) })
  return out.filter((a) => a.x != null)
}

const pct = (x) => `${(x * 100).toFixed(3)}%`

export function Timeline({ events, now }) {
  const ticks = timelineTicks(events, now)
  if (!ticks.length) return null
  const lanes = Math.max(...ticks.map((t) => t.lane)) + 1
  const axis = timelineAxis(now)
  return h('div', {
    className: 'hermes-fleet-timeline', 'data-timeline': ticks.length, role: 'group',
    'aria-label': `Worker lifecycle, last 24 hours: ${ticks.length} event${ticks.length === 1 ? '' : 's'}`
  },
    h('div', { className: 'hermes-fleet-tl-track', 'data-lanes': lanes, style: { height: `${lanes * LANE_PX}px` } },
      ...axis.map((a) => h('i', { key: `g${a.x}`, className: 'hermes-fleet-tl-grid', 'aria-hidden': 'true', style: { left: pct(a.x) } })),
      ...ticks.map((t) => h('span', {
        key: t.key, className: `hermes-fleet-tl-tick is-${t.kind}`, role: 'img', title: t.tip, 'aria-label': t.tip,
        'data-kind': t.kind, 'data-worker': t.worker, 'data-lane': t.lane,
        style: { left: pct(t.pos), top: `${t.lane * LANE_PX + 1}px` }
      }))),
    // The label slides from left-aligned at the window start to right-aligned at now, so none is cut off.
    h('div', { className: 'hermes-fleet-tl-axis', 'aria-hidden': 'true' },
      ...axis.map((a) => h('span', { key: `l${a.x}`, style: { left: pct(a.x), transform: `translateX(-${pct(a.x)})` } }, a.label))),
    h('div', { className: 'hermes-fleet-tl-legend', 'data-legend': 'worker-events' },
      ...Object.keys(EVENT_WORDS).map((k) => h('span', { key: k, 'data-kind': k },
        h('i', { className: `hermes-fleet-tl-key is-${k}`, 'aria-hidden': 'true' }), EVENT_WORDS[k]))))
}

// Machines that were shut down on purpose get one line under the grid, not a row each: a row of
// dotted slots and hours-old CPU/RAM numbers for a machine that is off reads as "still there".
function StoppedNote({ machines, now }) {
  if (!machines.length) return null
  return h('div', { className: 'hermes-fleet-note hermes-fleet-stopped', 'data-stopped': machines.length },
    `${machines.length} off: `,
    ...machines.flatMap((m, i) => [i ? ', ' : null, h('span', {
      key: m.name, 'data-machine': m.name,
      title: `stopped: ${m.hold_reason ? text(m.hold_reason) : 'no reason recorded'} (held by ${m.hold_owner ? text(m.hold_owner) : m.hold_by ? text(m.hold_by) : 'unknown'}, ${typeof m.hold_at === 'number' ? ago(now - m.hold_at) : 'time unknown'}); machine is off (${text(m.reason)})`
    }, text(m.name))]))
}

export const SCALER_COMMIT_MS = 200
const SCALER_PATH = '/v1/scaler/limits'

function validLimits(body) {
  if (body?.contract_version !== 1 || body.ok !== true ||
      !['min', 'max', 'active', 'asg_max'].every((k) => Number.isInteger(body[k]) && body[k] >= 0 && body[k] <= 5) ||
      body.min > body.max) throw Error('Invalid scaler response')
  return body
}

// Discover once per connection (limits are machine-wide), then pin every write to the answering route.
// Never retry a POST on another host: a failed response may still have applied the change.
export function createScalerClient(ctx, hostRef = host) {
  const request = async (route, options = {}) => validLimits(await ctx.rest(
    `${SCALER_PATH}?profile=${encodeURIComponent(route.targetProfile)}`,
    { connectionId: route.connectionId, timeoutMs: 60000, ...options }))
  return {
    async read() {
      const routes = new Map()
      for (const route of (await hostRef.profileRoutes()).filter(validRoute)) {
        const prev = routes.get(route.connectionId)
        if (!prev || route.targetProfile === 'default') routes.set(route.connectionId, route)
      }
      const results = await Promise.all([...routes.values()].map(async (route) => {
        try { return { route, limits: await request(route) } }
        catch (e) {
          // Desktop's expected-404 bridge preserves only the message, not status.
          return (e?.status ?? e?.statusCode) === 404 || /^404:/.test(e?.message || '') ? null : { error: e }
        }
      }))
      const supported = results.filter((r) => r?.limits)
      if (supported.length > 1) throw Error('Multiple autoscalers connected; limits not changed')
      if (!supported.length && results.some((r) => r?.error)) throw results.find((r) => r?.error).error
      return supported[0] || null
    },
    async write(source, limits) {
      return { route: source.route, limits: await request(source.route, { method: 'POST', body: limits }) }
    }
  }
}

export function ScalerControls({ client }) {
  const [source, setSource] = useState(null)
  const [draft, setDraft] = useState(null)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const current = useRef(null), pending = useRef(null), busy = useRef(false)
  const timer = useRef(null), revision = useRef(0), generation = useRef(0)
  useEffect(() => {
    generation.current++
    busy.current = false; pending.current = null; current.current = null
    setSaving(false); setDraft(null); setSource(null); setError(null)
    let disposed = false, readSequence = 0
    const load = async () => {
      if (busy.current || pending.current || document.visibilityState === 'hidden') return
      const rev = revision.current, read = ++readSequence
      // Fence both overlapping polls and reads from a replaced effect/client.
      const isCurrent = () => !disposed && read === readSequence && rev === revision.current
      try {
        const next = await client.read()
        if (isCurrent()) { current.current = next; setSource(next); setError(null) }
      } catch (e) {
        if (isCurrent()) { setError(text(e?.message || e)); current.current = null; setSource(null) }
      }
    }
    load()
    const poll = setInterval(load, POLL_OPEN_MS)
    return () => { disposed = true; generation.current++; clearInterval(poll); clearTimeout(timer.current) }
  }, [client])
  const change = (key, value) => {
    if (busy.current || !current.current) return
    clearTimeout(timer.current)
    revision.current++
    const next = { ...(pending.current || current.current.limits), [key]: Number(value) }
    if (key === 'min') next.max = Math.max(next.min, next.max)
    else next.min = Math.min(next.min, next.max)
    pending.current = { min: next.min, max: next.max }
    setDraft(pending.current)
  }
  const commit = () => {
    clearTimeout(timer.current)
    if (busy.current || !pending.current || !current.current) return
    const epoch = generation.current
    const isCurrent = () => epoch === generation.current
    timer.current = setTimeout(async () => {
      const next = pending.current, owner = current.current
      if (!next || !owner || !isCurrent()) return
      if (next.min === owner.limits.min && next.max === owner.limits.max) {
        pending.current = null; setDraft(null); return
      }
      busy.current = true; setSaving(true); setError(null)
      revision.current++
      try {
        const readback = await client.write(owner, next)
        if (isCurrent()) { current.current = readback; setSource(readback) }
      } catch (e) {
        if (isCurrent()) {
          current.current = null; setSource(null)
          setError(`Limits not confirmed: ${text(e?.message || e)}. Refresh before retrying.`)
        }
      } finally {
        if (isCurrent()) {
          busy.current = false; pending.current = null
          setSaving(false); setDraft(null)
        }
      }
    }, SCALER_COMMIT_MS)
  }
  if (!source && !error) return null // older/non-scaler gateways: unsupported, not a broken slider
  const limits = draft || source?.limits
  return h('div', { className: 'hermes-fleet-scaler', 'data-slot': 'runners-scaler' },
    limits ? ['min', 'max'].map((key) => h('label', { key },
      `${key === 'min' ? 'Min' : 'Max'} workers `,
      h('input', { type: 'range', min: 0, max: 5, step: 1, value: limits[key],
        'aria-label': `${key === 'min' ? 'Min' : 'Max'} workers`, disabled: saving,
        onChange: (e) => change(key, e.target.value), onPointerUp: commit, onKeyUp: commit, onBlur: commit }),
      h('output', null, String(limits[key])))) : null,
    source ? h('span', { role: 'status' }, `ACTIVE ${source.limits.active}${saving ? ' · Saving…' : ''}`) : null,
    error ? h('span', { role: 'alert', className: 'hermes-fleet-warning' }, error) : null)
}

export function Panel({ view, now, repairClient, throttle = null, onThrottle, throttleError, scalerClient }) {
  const s = summarize(view, throttle)
  const stopped = view.gateways.machines.filter((m) => m.state === 'stopped')
  return h('div', { className: 'hermes-fleet' },
    h('div', { className: 'hermes-fleet-head' },
      h('span', null, 'Gateway workers'),
      h('span', null, `${s.gBusy} / ${s.gSlots} slots busy`,
        s.gWaiting != null ? h('span', { title: 'Unleased ready cards assigned to gateway-dispatched profiles, counted once across board replicas. Excludes non-dispatched assignees; some cards cannot run on every machine.' }, ` · ${s.gWaiting} waiting`) : null)),
    h(SectionNote, { section: view.gateways }),
    h(ThrottleBar, { throttle, onThrottle, error: throttleError }),
    scalerClient ? h(ScalerControls, { client: scalerClient }) : null,
    h('div', { className: 'hermes-fleet-gw' },
      h(GatewayHead),
      ...view.gateways.machines.filter((m) => m.state !== 'stopped').map((m) => h(GatewayRow, { key: m.name, m, now, limit: limitRow(throttle, m.name) }))),
    h(Timeline, { events: view.gateways.events, now }),
    h(StoppedNote, { machines: stopped, now }),
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

// Track = capacity, fill = scheduled. Green while there is room, red once every slot is taken.
export function meterTone(m) {
  if (!m.slots) return 'none'
  return m.busy >= Math.min(m.slots, m.usable ?? m.slots) ? 'full' : 'ok'
}

// Pure status-chip model; the panel keeps its existing slot rendering.
export function pipGroups(view, throttle = null, now = Date.now()) {
  const totals = summarize(view, throttle)
  const typical = Number.isFinite(view.gateways.launch_ready_s) && view.gateways.launch_ready_s > 0
    ? view.gateways.launch_ready_s : 34 * 60
  return (view.gateways.machines || [])
    .filter((m) => m.state !== 'stopped' && Number.isInteger(m.slots) && m.slots > 0)
    .sort((a, b) => Number(isHub(b)) - Number(isHub(a)) || a.name.localeCompare(b.name))
    .map((m) => {
      const hub = isHub(m), busy = m.running.length, launching = isLaunching(m)
      const limit = slotLimit(m, limitRow(throttle, m.name))
      const throttled = limit?.throttled || 0
      const launchEvent = (view.gateways.events || []).filter((e) => e.worker === m.name && e.kind === 'launched').sort((a, b) => b.at - a.at)[0]
      const age = Math.max(0, now / 1000 - (m.launched_at ?? launchEvent?.at ?? now / 1000))
      const lit = Math.round(Math.min(1, age / typical) * m.slots)
      const full = meterTone(hub ? totals.hub : totals.workers) === 'full'
      const pips = Array.from({ length: m.slots }, (_, i) => {
        if (launching) return i < lit ? 'booted' : 'boot'
        if (i < busy) return m.state === 'draining' ? 'drain' : m.state === 'lost' || full ? 'lost' : 'busy'
        if (m.state === 'draining') return 'gone'
        return i >= m.slots - throttled ? 'thr' : 'free'
      })
      const title = launching
        ? `${m.name}: launching, ${Math.floor(age / 60)} min in (ready in ~${Math.max(0, Math.round((typical - age) / 60))})`
        : m.state === 'draining' ? `${m.name}: draining, ${busy} card${busy === 1 ? '' : 's'} finishing`
          : m.state === 'lost' ? `${m.name}: lost, holding ${busy} cards`
            : `${m.name}: ${busy}/${m.slots} busy${throttled ? `, ${throttled} throttled` : ''}`
      return { name: m.name, hub, state: launching ? 'launching' : m.state, lost: m.state === 'lost', pips, title }
    })
}

export function PipChip({ groups }) {
  return h('span', { className: 'hermes-fleet-chipgroups', 'aria-hidden': true },
    ...[true, false].map((hub) => h('span', { key: String(hub), className: 'hermes-fleet-chippool' },
      ...groups.filter((g) => g.hub === hub).map((g) => h('span', {
        key: g.name, 'data-pip-machine': g.name, title: g.title,
        className: `hermes-fleet-chipgroup${g.hub ? ' is-hub' : ''}${g.lost ? ' is-lost' : ''}${g.state === 'launching' ? ' is-launching' : ''}`
      }, ...g.pips.map((state, i) => h('span', { key: i, className: `hermes-fleet-pip is-${state}` })))))))
}

// The React Query cache keeps the last view across refetches (no flicker, no spinner);
// one poll runs at a time and polling stops while the window is hidden.
// The gateway stream (ctx.onEvent; older hosts: host.onEvent). Returns a disposer, or null without one.
export function onPush(ctx, type, fn) {
  const subscribe = typeof ctx?.onEvent === 'function' ? ctx.onEvent : typeof host?.onEvent === 'function' ? host.onEvent : null
  return subscribe ? subscribe(type, fn) : null
}

export function Runners({ ctx }) {
  const [open, setOpen] = useState(false)
  const visible = useVisible()
  const transport = useMemo(() => transportFor(ctx), [ctx])
  const client = useQueryClient()
  const throttleLive = useRef(-Infinity)
  const repairClient = useMemo(() => createRepairClient(ctx), [ctx])
  const scalerClient = useMemo(() => createScalerClient(ctx), [ctx])
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
  useEffect(() => {
    const offFleet = onPush(ctx, FLEET_EVENT, (ev) => {
      if (transport.applyPush(ev?.payload)) client.setQueryData(['runners', 'fleet'], transport.view())
    })
    const offThrottle = onPush(ctx, SNAPSHOT_EVENT, (ev) => {
      const p = ev?.payload
      const cur = client.getQueryData(['runners', 'throttle'])
      if (!p?.feed || !cur || cur.feed !== p.feed || !p.throttle || typeof p.throttle.percent !== 'number') return
      throttleLive.current = Date.now()
      client.setQueryData(['runners', 'throttle'], p.throttle)
    })
    return () => { offFleet?.(); offThrottle?.() }
  }, [ctx, transport, client])
  const now = useNow(visible)
  const view = query.data
  // The token throttle and per-gateway slot limits (throughput plugin). Absent or failing: nothing is crossed out.
  const [throttleError, setThrottleError] = useState(null)
  const tq = useQuery({
    queryKey: ['runners', 'throttle'],
    queryFn: async () => { try { return await bridge(THROTTLE_PATH) } catch { return null } },
    // Pushed once a minute while the gateway sees our reads: then only the safety read (which also renews interest).
    refetchInterval: visible ? (now - throttleLive.current < PUSH_TRUST_MS ? SAFETY_MS : POLL_OPEN_MS) : false,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    retry: false,
    staleTime: 5000
  })
  const throttle = tq.data && typeof tq.data === 'object' && 'percent' in tq.data ? tq.data : null
  const onThrottle = async (capPerMin) => {
    try {
      await bridge(THROTTLE_PATH, { method: 'PUT', body: { cap_per_min: capPerMin } })
      setThrottleError(null)
    } catch (e) {
      setThrottleError(`throttle not set: ${e?.message || e}`)
    }
    tq.refetch()
  }
  const s = view ? summarize(view, throttle) : null
  const groups = view ? pipGroups(view, throttle, now) : []
  const launching = groups.filter((g) => g.state === 'launching').map((g) => `${g.name} launching`)
  const label = s
    ? `hub ${s.hub.busy}/${s.hub.slots} · workers ${s.workers.busy}/${s.workers.slots}${launching.length ? ` · ${launching.join(' · ')}` : ''}`
    : 'Runners …'
  const title = s ? groups.map((g) => g.title).join(' · ') : 'Loading runners'
  return h(Popover, { open, onOpenChange: setOpen },
    h(PopoverTrigger, { asChild: true },
      h('button', { type: 'button', className: BAR, 'data-slot': 'runners-chip', 'aria-label': `Show runners: ${label}`, title },
        s && (s.tone === 'bad' || s.tone === 'warn') ? h(StatusDot, { tone: s.tone }) : null,
        s ? h(PipChip, { groups })
          : h('span', null, label))),
    h(PopoverContent, {
      side: 'top', align: 'end', sticky: 'always', 'aria-label': 'Runners',
      className: 'hermes-fleet-popover',
      style: { width: 'max-content', minWidth: 'min(680px, calc(100vw - 24px))', maxWidth: 'calc(100vw - 24px)', maxHeight: '70vh', overflowY: 'auto', overflowX: 'auto', scrollbarGutter: 'stable' }
    }, view ? h(Panel, { view, now, repairClient, scalerClient }) : h('div', { className: 'hermes-fleet-note' }, query.error ? `Unavailable: ${text(query.error.message)}` : 'Loading…'))
  )
}

export function CI({ ctx }) {
  const [open, setOpen] = useState(false)
  const visible = useVisible()
  const now = useNow(visible)
  const transport = useMemo(() => transportFor(ctx), [ctx])
  const repairClient = useMemo(() => createRepairClient(ctx), [ctx])
  // Runners owns the shared cache's polling interval. CI observes it and refreshes on open,
  // rather than adding a second observer timer (deduplication only covers in-flight reads).
  const query = useQuery({ queryKey: ['runners', 'fleet'], queryFn: () => transport.poll(),
    refetchInterval: false, refetchIntervalInBackground: false,
    refetchOnWindowFocus: false, retry: false, staleTime: 5000 })
  useEffect(() => {
    const show = () => setOpen(true)
    window.addEventListener('hermes-show-ci', show)
    return () => window.removeEventListener('hermes-show-ci', show)
  }, [])
  useEffect(() => { if (open && visible) query.refetch() }, [open])
  const view = query.data
  const model = ciModel(view?.ci || { state: 'unknown', machines: [] }, now)
  return h(Popover, { open, onOpenChange: setOpen },
    h(PopoverTrigger, { asChild: true }, h('button', { type: 'button', className: BAR,
      'data-slot': 'ci-chip', 'aria-label': `Show CI: ${model.title}`, title: model.title }, h(CiChip, { model }))),
    h(PopoverContent, { side: 'top', align: 'end', sticky: 'always', 'aria-label': 'CI (GitHub Actions)',
      className: 'hermes-ci-popover', style: { width: '620px', maxWidth: 'calc(100vw - 24px)', maxHeight: '70vh', overflowY: 'auto' } },
    view ? h(CiPanel, { view, now, repairClient }) : h('div', { className: 'hermes-fleet-note' }, query.error ? `Unavailable: ${text(query.error.message)}` : 'Loading…')))
}

export const CSS = `
.hermes-fleet-scaler{display:flex;align-items:center;gap:16px;flex-wrap:wrap;font-size:11px;color:var(--ui-text-secondary);margin:6px 0 10px}
.hermes-fleet-scaler label{display:flex;align-items:center;gap:6px;white-space:nowrap}
.hermes-fleet-scaler input{width:110px;accent-color:var(--ui-text-secondary)}
.hermes-fleet-scaler output{min-width:1ch;font-variant-numeric:tabular-nums}
.hermes-ci-chip{display:inline-flex;align-items:center;gap:6px;color:var(--ui-text-tertiary,#8b8b94);font-size:10px;line-height:1;white-space:nowrap}
.hermes-ci-chip.is-stale{opacity:.45}
.hermes-ci-label{font-size:10px;font-weight:600;color:#a9a9b2;letter-spacing:.03em;margin-right:1px}
.hermes-ci-dot{width:6px;height:6px;border-radius:50%;background:#d08a1e}
.hermes-ci-dot.is-bad{background:#d64545}
.hermes-ci-section{display:inline-flex;align-items:center;gap:3px}
.hermes-ci-grid{display:inline-grid;grid-auto-flow:column;grid-template-rows:repeat(2,4px);grid-auto-columns:4px;gap:1px;flex:none}
.hermes-ci-pip{width:4px;height:4px;border-radius:50%;background:rgba(255,255,255,.14)}
.hermes-ci-pip.is-busy{background:#2ea043}
.hermes-ci-pip.is-full,.hermes-ci-pip.is-busy-warn{background:#d64545}
.hermes-ci-pip.is-offline{background:transparent;box-shadow:inset 0 0 0 1px rgba(255,255,255,.34)}
.hermes-ci-pip.is-warn{background:transparent;box-shadow:inset 0 0 0 1px #d64545}
.hermes-ci-pip.is-warn.is-busy-warn{background:#d64545}
.hermes-ci-number{font-variant-numeric:tabular-nums;color:#2ea043;font-size:10px}
.hermes-ci-divider{width:1px;height:9px;background:rgba(255,255,255,.16)}
.hermes-ci-panel{font-size:12px;padding:12px 14px;color:var(--ui-text-primary,#e6e6ea)}
.hermes-ci-head{display:flex;justify-content:space-between;font-weight:600;margin-bottom:8px}
.hermes-ci-head>span:last-child{font-weight:400;color:var(--ui-text-tertiary,#8b8b94)}
.hermes-ci-panel section{margin-top:10px}
.hermes-ci-section-head{display:flex;align-items:center;gap:8px;padding:4px 0;border-bottom:1px solid rgba(255,255,255,.08);margin-bottom:4px}
.hermes-ci-section-head b{font-size:12px;width:44px}
.hermes-ci-section-head>.hermes-ci-grid{zoom:1.5}
.hermes-ci-section-head>span:last-child{margin-left:auto;color:var(--ui-text-tertiary,#8b8b94)}
.hermes-ci-row{display:grid;grid-template-columns:180px minmax(0,1fr) 44px;align-items:center;gap:8px;padding:3px 0}
.hermes-ci-name,.hermes-ci-labels{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hermes-ci-row-detail{display:flex;gap:6px;align-items:center;min-width:0}
.hermes-ci-slot-action{display:inline-flex;flex:none;align-items:center;justify-content:center;width:12px;height:12px;padding:0;border:0;background:none;color:inherit}
button.hermes-ci-slot-action{cursor:pointer}
.hermes-ci-slot-action .hermes-ci-grid{zoom:1.5}
.hermes-ci-slot-action:focus-visible{outline:1px solid #d08a1e;outline-offset:2px}
.hermes-ci-labels{color:#6f6f78;font-size:11px}
.hermes-ci-count{text-align:right;font-variant-numeric:tabular-nums;color:var(--ui-text-tertiary,#8b8b94)}
.hermes-ci-elastic-strip{display:inline-grid;grid-auto-flow:column;grid-template-rows:repeat(3,4px);grid-auto-columns:4px;gap:1px;flex:none;max-width:45%;overflow:hidden}
.hermes-ci-elastic-strip i{display:block;width:4px;height:4px;border-radius:1px;background:#2ea043}
.hermes-ci-offline-name{font:inherit;color:inherit;padding:0;border:0;background:none}
button.hermes-ci-offline-name{cursor:pointer}
button.hermes-ci-offline-name:hover{text-decoration:underline}
.hermes-ci-note{color:#7b7b84;font-size:11px;padding:3px 0;overflow-wrap:anywhere}
.hermes-ci-panel>.hermes-ci-note{margin-top:10px}
[data-slot="runners-chip"]{white-space:nowrap}
.hermes-fleet-chipgroups{display:inline-flex;align-items:center;gap:15px}
.hermes-fleet-chippool{display:inline-flex;align-items:center;gap:4px}
.hermes-fleet-chippool:empty{display:none}
.hermes-fleet-chipgroup{display:inline-grid;grid-auto-flow:column;grid-template-rows:repeat(2,4px);grid-auto-columns:4px;gap:1px;padding:1px;border-radius:2px}
.hermes-fleet-chipgroup.is-hub{box-shadow:0 0 0 1px rgba(255,255,255,.32)}
.hermes-fleet-chipgroup.is-lost{box-shadow:0 0 0 1px #d64545}
.hermes-fleet-pip{width:4px;height:4px;border-radius:1px;background:rgba(255,255,255,.14)}
.hermes-fleet-pip.is-busy{background:#2ea043}
.hermes-fleet-pip.is-lost{background:#d64545}
.hermes-fleet-pip.is-thr{background:repeating-linear-gradient(45deg,transparent 0 1px,#d08a1e 1px 2.2px)}
.hermes-fleet-pip.is-drain{background:#388bfd}
.hermes-fleet-pip.is-gone{background:transparent;box-shadow:inset 0 0 0 1px rgba(56,139,253,.35)}
.hermes-fleet-pip.is-boot{background:transparent;box-shadow:inset 0 0 0 1px #d4a72c}
.hermes-fleet-pip.is-booted{background:#d4a72c}
@keyframes hermes-fleet-launch-pulse{50%{opacity:.35}}
.hermes-fleet-chipgroup.is-launching{animation:hermes-fleet-launch-pulse 1.4s ease-in-out infinite}
@media (prefers-reduced-motion:reduce){.hermes-fleet-chipgroup.is-launching{animation:none}}
.hermes-fleet{display:flex;flex-direction:column;gap:3px;font-size:12px;padding:4px}
.hermes-fleet>:not(.hermes-fleet-gw){contain:inline-size}
.hermes-fleet-head{display:flex;justify-content:space-between;margin-top:8px;font-weight:600;text-transform:uppercase;font-size:11px;color:var(--ui-text-secondary)}
.hermes-fleet-head:first-child{margin-top:0}
.hermes-fleet-row{display:grid;grid-template-columns:150px minmax(0,1fr) 44px;column-gap:8px;align-items:center;min-height:20px}
.hermes-fleet-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hermes-fleet-slots{display:flex;flex-wrap:wrap;align-items:center;gap:2px}
.hermes-fleet-waiting{margin-left:6px;font-size:11px;white-space:nowrap;font-variant-numeric:tabular-nums;color:var(--ui-text-secondary)}
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
.hermes-fleet-group-head{display:grid;grid-template-columns:12px 10px minmax(0,1fr) auto 44px;column-gap:6px;align-items:center;width:100%;min-height:22px;padding:0;border:0;background:transparent;color:inherit;font:inherit;text-align:left;cursor:pointer}
.hermes-fleet-group-head:hover{background:var(--chrome-action-hover)}
.hermes-fleet-group-name{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hermes-fleet-group-meta{font-size:11px;color:var(--ui-text-tertiary);white-space:nowrap}
.hermes-fleet-chev{color:var(--ui-text-tertiary);font-size:10px}
.hermes-fleet-group>.hermes-fleet-row{padding-left:18px}
.hermes-fleet-gw{display:grid;grid-template-columns:repeat(8,max-content) minmax(max-content,1fr) max-content;row-gap:3px}
.hermes-fleet-gw>.hermes-fleet-row,.hermes-fleet-cols{display:grid;grid-column:1/-1;grid-template-columns:subgrid;column-gap:16px;align-items:center;white-space:nowrap}
.hermes-fleet-gw>.hermes-fleet-row>*{grid-row:1}
.hermes-fleet-cols{font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:var(--ui-text-tertiary)}
.hermes-fleet-cols>[data-col=waiting],.hermes-fleet-cols>[data-col=ineligible],.hermes-fleet-cols>[data-col=count],.hermes-fleet-cols>[data-col=tok]{text-align:right}
.hermes-fleet-gw .hermes-fleet-name{grid-column:1;max-width:150px;display:flex;align-items:center;gap:6px}
.hermes-fleet-machine-name{min-width:0;overflow:hidden;text-overflow:ellipsis}
.hermes-fleet-drain-label{flex-shrink:0;font-size:10px;opacity:.75}
.hermes-fleet-gw .hermes-fleet-slots{grid-column:2;flex-wrap:nowrap}
.hermes-fleet-gw .hermes-fleet-cell{font-size:11px;font-variant-numeric:tabular-nums;color:var(--ui-text-secondary)}
.hermes-fleet-gw .hermes-fleet-waiting{grid-column:3;margin:0;text-align:right}
.hermes-fleet-gw .hermes-fleet-ineligible{grid-column:4;margin:0;text-align:right}
.hermes-fleet-gw .hermes-fleet-meter{display:flex;gap:6px}
.hermes-fleet-gw .hermes-fleet-meter[data-meter=cpu]{grid-column:5}
.hermes-fleet-gw .hermes-fleet-meter[data-meter=ram]{grid-column:6}
.hermes-fleet-gw .hermes-fleet-meter[data-meter=disk]{grid-column:7}
.hermes-fleet-gw .hermes-fleet-meter.is-stale,.hermes-fleet-gw .hermes-fleet-meter.is-none{color:var(--ui-text-tertiary)}
.hermes-fleet-gw .hermes-fleet-num{flex:1;text-align:right}
.hermes-fleet-gw .hermes-fleet-info{grid-column:10;margin-left:-8px;position:relative;box-sizing:border-box;width:13px;height:13px;border:1px solid var(--ui-text-tertiary);border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font:italic 600 9px/1 Georgia,serif;color:var(--ui-text-secondary);cursor:help}
.hermes-fleet-gw .hermes-fleet-info.is-empty{visibility:hidden}
.hermes-fleet-gw .hermes-fleet-info:hover::after,.hermes-fleet-gw .hermes-fleet-info:focus-visible::after{content:attr(data-tip);position:absolute;right:-2px;top:calc(100% + 5px);z-index:5;padding:3px 7px;border:1px solid var(--ui-stroke-secondary);border-radius:5px;background:var(--ui-bg,#1b1b1f);color:var(--ui-text-primary,inherit);font:normal 400 11px/1.4 system-ui,sans-serif;white-space:normal;width:max-content;max-width:min(480px,calc(100vw - 32px));overflow-wrap:anywhere;box-shadow:0 2px 8px rgba(0,0,0,.35);pointer-events:none}
.hermes-fleet-gw .hermes-fleet-info:hover,.hermes-fleet-gw .hermes-fleet-info:focus-visible{border-color:var(--ui-text-secondary);color:inherit;outline:none}
.hermes-fleet-gw .hermes-fleet-count{grid-column:9}
.hermes-fleet-gw .hermes-fleet-tok{grid-column:8;margin:0;text-align:right}
.hermes-fleet-gw .hermes-fleet-tok.is-none{color:var(--ui-text-tertiary)}
.hermes-fleet-note,.hermes-fleet-foot{font-size:11px;color:var(--ui-text-tertiary)}
.hermes-fleet-foot{margin-top:8px}
.hermes-fleet-pop{grid-column:1/-1;display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:2px 0 4px;padding:6px;border:1px solid var(--ui-stroke-secondary);border-radius:6px;background:var(--ui-bg)}
.hermes-fleet-pop-title{font-weight:600}
.hermes-fleet-pop button{font-size:11px}
.hermes-fleet-pop button:disabled{opacity:.55}
.hermes-fleet-slot{display:inline-block;width:10px;height:10px;border-radius:2px;border:1px solid var(--ui-text-tertiary);padding:0;background:transparent}
button.hermes-fleet-slot{cursor:pointer}
.hermes-fleet-slot.is-throttled{position:relative;border-color:#d08a1e;background-image:linear-gradient(45deg,transparent calc(50% - .6px),#d08a1e calc(50% - .6px),#d08a1e calc(50% + .6px),transparent calc(50% + .6px)),linear-gradient(-45deg,transparent calc(50% - .6px),#d08a1e calc(50% - .6px),#d08a1e calc(50% + .6px),transparent calc(50% + .6px))}
.hermes-fleet-slot.is-busy.is-throttled{background-color:var(--ui-accent);border-color:var(--ui-accent);background-image:linear-gradient(45deg,transparent calc(50% - .6px),var(--ui-bg,#fff) calc(50% - .6px),var(--ui-bg,#fff) calc(50% + .6px),transparent calc(50% + .6px)),linear-gradient(-45deg,transparent calc(50% - .6px),var(--ui-bg,#fff) calc(50% - .6px),var(--ui-bg,#fff) calc(50% + .6px),transparent calc(50% + .6px))}
.hermes-fleet-throttle{display:flex;align-items:center;gap:8px;font-size:11px;color:var(--ui-text-secondary);margin:2px 0 4px}
.hermes-fleet-throttle input[type=range]{width:120px;flex-shrink:0}
.hermes-fleet-chipbar{position:relative}
.hermes-fleet-chipcut{position:absolute;right:0;top:0;height:100%;background:repeating-linear-gradient(45deg,transparent 0 2px,#d08a1e 2px 3px)}
.hermes-fleet-slot.is-busy{background:var(--ui-accent);border-color:var(--ui-accent)}
.hermes-fleet-slot.is-offline,.hermes-fleet-slot.is-unknown{border-style:dotted;opacity:.6}
.hermes-fleet-slot.is-draining{background:repeating-linear-gradient(45deg,transparent 0 2px,var(--ui-text-tertiary) 2px 3px)}
.hermes-fleet-slot.is-lost{background:#d64545;border-color:#d64545}
.hermes-fleet-row.is-asleep .hermes-fleet-name,.hermes-fleet-row.is-unknown .hermes-fleet-name{color:var(--ui-text-tertiary)}
.hermes-fleet-timeline{margin-top:6px;font-size:10px;color:var(--ui-text-tertiary)}
.hermes-fleet-tl-track{position:relative;margin:0 2px;border-bottom:1px solid var(--ui-stroke-secondary)}
.hermes-fleet-tl-grid{position:absolute;top:0;bottom:0;border-left:1px dotted var(--ui-stroke-secondary)}
.hermes-fleet-tl-tick{position:absolute;width:4px;height:8px;margin-left:-2px;border-radius:1px;cursor:help}
.hermes-fleet-tl-tick:hover{z-index:1;outline:1px solid var(--ui-text-primary,currentColor);outline-offset:1px}
.hermes-fleet-timeline .is-launched{background:#d4a72c}
.hermes-fleet-timeline .is-ready{background:#2ea043}
.hermes-fleet-timeline .is-draining{background:#388bfd}
.hermes-fleet-timeline .is-removed{background:#d64545}
.hermes-fleet-tl-axis{position:relative;height:13px;margin:1px 2px 0;font-variant-numeric:tabular-nums}
.hermes-fleet-tl-axis>span{position:absolute;top:0;white-space:nowrap}
.hermes-fleet-tl-legend{display:flex;justify-content:flex-end;flex-wrap:wrap;gap:2px 10px}
.hermes-fleet-tl-legend>span{display:inline-flex;align-items:center;gap:4px;white-space:nowrap}
.hermes-fleet-tl-key{display:inline-block;width:4px;height:8px;border-radius:1px}
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
    ctx.register({ id: 'ci-chip', area: STATUSBAR_AREAS.right, order: 7, render: () => h(CI, { ctx }) })
    ctx.register({ id: 'show-ci', area: PALETTE_AREA, data: {
      id: 'show-ci', label: 'Show CI', keywords: ['ci', 'github', 'actions', 'builds'],
      run: () => window.dispatchEvent(new window.Event('hermes-show-ci'))
    } })
    ctx.register({
      id: 'show-runners',
      area: PALETTE_AREA,
      data: {
        id: 'show-runners', label: 'Show runners', keywords: ['runners', 'workers', 'slots', 'gateways'],
        run: () => window.dispatchEvent(new window.Event('hermes-show-runners'))
      }
    })
  }
}

// Native node-first surface. No credentials or ambient action routing.
import {
  host,
  useValue,
  useQuery,
  useQueryClient,
  STATUSBAR_AREAS,
  PALETTE_AREA,
  Popover,
  PopoverTrigger,
  PopoverContent,
  StatusDot,
  ContextMenu,
  ContextMenuTrigger,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator
} from '@hermes/plugin-sdk'
import { createElement as h, useState, useEffect, useLayoutEffect, useRef } from 'react'

const BAR =
  'inline-flex h-full items-center gap-1.5 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
const ID_PATTERN = /^[a-z]{2,12}_[0-9a-f]{16,64}$/
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const FILTERS = ['All', 'Needs attention', 'Running (Busy)', 'Idle', 'Held/maintenance', 'Failed', 'Unknown/stale', 'Checks passed + idle (Ready)']
const STATES = {
  not_configured: 'Not configured',
  owner_disconnected: 'Owner gateway disconnected',
  plugin_unavailable: 'Plugin unavailable',
  owner_conflict: 'Ownership conflict',
  partial: 'Partial permissions / coverage',
  provider_unavailable: 'Provider outage',
  configuration_error: 'Configuration error',
  loading: 'Loading inventory'
}
const text = (value) => String(value ?? '').slice(0, 1024)
const routeKey = (r) => JSON.stringify([r.connectionId, r.mode, r.profile, r.targetProfile])
export const ownerQueryKey = (o) => ['runners', 'owner', o.gateway_id, o.profile]
const ownerKey = (o) => JSON.stringify(ownerQueryKey(o))
export const pollInterval = (open, visible) => (visible ? (open ? 15000 : 60000) : false)
function validRoute(r) {
  return (
    r &&
    ['connectionId', 'profile', 'targetProfile'].every(
      (k) => typeof r[k] === 'string' && r[k].trim()
    ) &&
    ['local', 'remote'].includes(r.mode)
  )
}
function failure(error) {
  if (error?.safe) return error.message
  const status = error?.status ?? error?.statusCode
  if (status === 404 || /\b404\b/.test(error?.message || '')) return 'Plugin unavailable'
  if (status === 401 || status === 403 || /\b40[13]\b/.test(error?.message || ''))
    return 'Partial permissions'
  return 'Owner gateway disconnected'
}
function safeError(message) {
  return Object.assign(new Error(message), { safe: true })
}
function verifyEnvelope(value, route, expected, discovery = false) {
  const o = value?.meta?.owner
  if (
    value?.meta?.contract_version !== 1 ||
    !UUID.test(o?.gateway_id || '') ||
    o.profile !== route.targetProfile ||
    (expected &&
      (ownerKey(o) !== ownerKey(expected) ||
        (discovery
          ? o.owner_epoch < expected.owner_epoch
          : o.owner_epoch !== expected.owner_epoch)))
  )
    throw safeError('Owner identity mismatch')
  if (value.error)
    throw safeError(
      {
        not_found: 'Plugin unavailable',
        forbidden: 'Partial permissions',
        configuration_error: 'Configuration error',
        dependency_unavailable: 'Provider outage'
      }[value.error.code] || 'Source unavailable'
    )
  if (!value.data) throw safeError('Invalid inventory response')
  return value
}

// Each request captures a complete route before awaits and revalidates it afterwards.
// Registry removal never changes the request into an ambient/focused REST call.
export function createTransport(ctx, hostRef = host) {
  const sources = new Map(),
    pending = new Map()
  async function routes() {
    const list = await hostRef.profileRoutes()
    if (!Array.isArray(list) || list.some((r) => !validRoute(r)))
      throw safeError('Invalid owner route registry')
    return [...new Map(list.map((r) => [routeKey(r), { ...r }])).values()]
  }
  async function request(input, path, options = {}, expected) {
    if (!validRoute(input)) throw safeError('Invalid owner route')
    const route = { ...input },
      key = routeKey(route)
    if (!(await routes()).some((r) => routeKey(r) === key))
      throw safeError('Owner gateway disconnected: route removed')
    const response = await ctx.rest(
      `/v1${path}${path.includes('?') ? '&' : '?'}profile=${encodeURIComponent(route.targetProfile)}`,
      { ...options, connectionId: route.connectionId, timeoutMs: 10000 }
    )
    if (!(await routes()).some((r) => routeKey(r) === key))
      throw safeError('Owner gateway disconnected: route changed')
    if (path === '/enrollment') {
      if (response?.contract_version !== 1 || response.profile !== route.targetProfile ||
          typeof response.enrolled !== 'boolean' || !Array.isArray(response.capabilities) ||
          (response.enrolled && !response.capabilities.includes('inventory')))
        throw safeError('Invalid enrollment response')
      return response
    }
    return verifyEnvelope(response, route, expected, path === '/inventory' && !options.method)
  }
  async function inventory(queryClient) {
    let list
    try {
      list = await routes()
    } catch (e) {
      for (const [key, s] of sources) sources.set(key, { ...s, error: failure(e) })
      return sources.size ? [...sources.values()] : [{ error: failure(e) }]
    }
    const keys = new Set(list.map(routeKey))
    for (const [key, s] of sources)
      if (!keys.has(key)) sources.set(key, { ...s, error: 'Owner gateway disconnected' })
    let next = 0
    await Promise.all(
      Array.from({ length: Math.min(4, list.length) }, async () => {
        while (next < list.length) {
          const route = list[next++],
            key = routeKey(route),
            prior = sources.get(key)
          let enrollment, legacy = false
          try {
            try {
              enrollment = await request(route, '/enrollment')
            } catch (e) {
              // An older mounted router may still serve a valid owner inventory.
              // Only 404 permits a read-only probe; auth/config failures never do.
              if (e?.status !== 404) throw e
              legacy = true
            }
            if (!legacy && !enrollment.enrolled) {
              sources.set(key, { route, enrolled: false })
              continue
            }
            const fetch = () => request(route, '/inventory', {}, prior?.snapshot?.meta.owner)
            // Bootstrap is route-keyed until the server establishes a stable UUID.
            const queryKey = prior?.snapshot
              ? ownerQueryKey(prior.snapshot.meta.owner)
              : ['runners', 'bootstrap', key]
            const snapshot = queryClient
              ? await queryClient.fetchQuery({
                  queryKey,
                  queryFn: fetch,
                  staleTime: 0,
                  retry: false
                })
              : await fetch()
            if (
              !Array.isArray(snapshot.data.runners) ||
              !Array.isArray(snapshot.data.scopes) ||
              !Array.isArray(snapshot.data.hosts)
            )
              throw safeError('Invalid inventory response')
            queryClient?.setQueryData(ownerQueryKey(snapshot.meta.owner), snapshot)
            const features = { repair_sessions: enrollment?.features?.repair_sessions === true }
            let repairs = []
            if (features.repair_sessions) {
              const response = await request(route, '/repairs', {}, snapshot.meta.owner)
              if (!Array.isArray(response.data)) throw safeError('Invalid repair list')
              repairs = response.data
            }
            sources.set(key, { route, snapshot, features, repairs, ...(legacy ? { error: 'Deployment pending: enrollment unavailable; last-known inventory' } : {}) })
          } catch (e) {
            sources.set(key, { ...prior, route, ...(enrollment?.enrolled === true ? { enrolled: true } : {}), error: failure(e) })
          }
        }
      })
    )
    return [...sources.values()]
  }
  async function refresh(source, ids = []) {
    if (!source?.snapshot || source.error) throw safeError('Owner gateway disconnected')
    if (ids.some((id) => !ID_PATTERN.test(id))) throw safeError('Invalid runner ID')
    const key = `${ownerKey(source.snapshot.meta.owner)}:${[...ids].sort().join(',')}`
    if (pending.has(key)) return pending.get(key)
    const promise = request(
      source.route,
      '/refresh',
      {
        method: 'POST',
        body: {
          target_ids: ids,
          idempotency_key: `refresh_${crypto.randomUUID().replaceAll('-', '')}`
        }
      },
      source.snapshot.meta.owner
    )
    pending.set(key, promise)
    try {
      return await promise
    } finally {
      pending.delete(key)
    }
  }
  // Retain ambiguous attempts for this transport's lifetime. A retry queries
  // status, never sends a prompt or schedules the first turn again.
  const starts = new Set(), repairPending = new Map(), repairKeys = new Map()
  async function assertRoute(route) {
    if (!(await routes()).some(r => routeKey(r) === routeKey(route)))
      throw safeError('Owner gateway disconnected: route removed')
  }
  async function openRepair(route, owner, repair) {
    if (!repair?.owner || ownerKey(repair.owner) !== ownerKey(owner) ||
        repair.owner.owner_epoch !== owner.owner_epoch ||
        !ID_PATTERN.test(repair.session?.start_request_id || ''))
      throw safeError('Invalid repair owner or start request')
    if (typeof hostRef.requestProfile !== 'function' || typeof hostRef.openSession !== 'function')
      throw safeError('Repair sessions unsupported: update Hermes Desktop')
    const params = { profile: route.targetProfile, namespace: 'runners', start_request_id: repair.session.start_request_id }
    async function status() {
      const response = await request(route, `/repairs/${encodeURIComponent(repair.id)}`, {}, owner)
      const current = response.data.repair
      if (current?.owner?.gateway_id !== owner.gateway_id || current.owner.profile !== owner.profile)
        throw safeError('Repair session owner identity mismatch')
      return { gateway_id: current.owner.gateway_id, profile: current.owner.profile,
        stored_session_id: current.session.stored_session_id, resolved_session_id: current.session.lineage_tip_id,
        exists: !!current.session.stored_session_id, reconciliation: current.session.state }
    }
    const key = JSON.stringify([ownerKey(owner), owner.owner_epoch, params.start_request_id])
    await assertRoute(route)
    let result
    if (starts.has(key)) {
      try {
        result = await status()
      } catch {
        throw safeError('Repair outcome unknown; status reconciliation unavailable. No prompt resent.')
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
          throw safeError('Repair outcome unknown; status reconciliation unavailable. No prompt resent.')
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
    if (result?.gateway_id !== owner.gateway_id || result.profile !== owner.profile)
      throw safeError('Repair session owner identity mismatch')
    const id = result.resolved_session_id || result.stored_session_id
    if (!result.exists || !/^[A-Za-z0-9_-]{1,128}$/.test(id || ''))
      throw safeError('Repair session outcome unknown; no session available to open')
    await hostRef.openSession(id, { route: { ...route } })
    return repair
  }
  async function repair(row) {
    if (!row?.actionable || !row.source?.snapshot || row.source.error)
      throw safeError('Owner gateway disconnected or ownership conflict')
    if (!ID_PATTERN.test(row.id)) throw safeError('Invalid runner ID')
    // Copy both routing and authority before the first await, not from the
    // selected chat or a mutable source row after the network responds.
    const route = { ...row.source.route }, owner = { ...row.source.snapshot.meta.owner }, entity = row.id
    const key = JSON.stringify([ownerKey(owner), owner.owner_epoch, entity])
    const related = new Set([entity, ...row.source.snapshot.data.runners
      .filter(r => row.host_id && r.host_id === row.host_id).map(r => r.id)])
    const mappedHost = row.source.snapshot.data.hosts.find(h => h.id === row.host_id)
    if (ID_PATTERN.test(mappedHost?.resource_id || '')) related.add(mappedHost.resource_id)
    if (repairPending.has(key)) return repairPending.get(key)
    const run = async () => {
      const detail = await request(route, `/runners/${encodeURIComponent(entity)}?history_limit=50`, {}, owner)
      const existing = (detail.data.repairs || []).find(r =>
        related.has(r.entity_id) && !['closed', 'failed'].includes(r.state))
      if (existing) {
        starts.add(JSON.stringify([ownerKey(owner), owner.owner_epoch, existing.session?.start_request_id]))
        return openRepair(route, owner, existing)
      }
      const incident = (detail.data.incidents || []).find(i => i.entity_id === entity && !i.resolved_at && ID_PATTERN.test(i.id))
      if (!incident) throw safeError('No current incident identity; refresh runner evidence before investigating')
      const previous = (detail.data.repairs || []).find(r => related.has(r.entity_id))
      const attempt = `${key}:${incident.id}:${previous?.id || 'first'}`
      if (!repairKeys.has(attempt)) repairKeys.set(attempt, `repair_${crypto.randomUUID().replaceAll('-', '')}`)
      const response = await request(route, '/repairs', { method: 'POST', body: {
        entity_id: entity, incident_id: incident.id,
        idempotency_key: repairKeys.get(attempt), mode: row.issue ? 'repair' : 'diagnose', expected_owner: owner
      } }, owner)
      // Backend route hints are not authority. The captured, verified registry
      // route reaches this owner even when the runner host itself is dead.
      if (!related.has(response.data.repair?.entity_id))
        throw safeError('Invalid repair entity')
      return openRepair(route, owner, response.data.repair)
    }
    const promise = run()
    repairPending.set(key, promise)
    try { return await promise } finally { repairPending.delete(key) }
  }
  async function maintenance(row, { state, reason, expires_at }) {
    if (!row?.actionable || row.source?.error) throw safeError('Owner gateway disconnected or ownership conflict')
    const entity = row.host?.resource_id
    if (!ID_PATTERN.test(entity || '') || !['active', 'inactive'].includes(state) ||
        typeof reason !== 'string' || !reason.trim() || reason.length > 512 ||
        (state === 'active' && !(Date.parse(expires_at) > Date.now())))
      throw safeError('Maintenance requires a mapped host, reason and future expiry')
    const owner = { ...row.source.snapshot.meta.owner }
    const route = { ...row.source.route }
    const version = await request(route, '/maintenance', {}, owner)
    if (!Number.isSafeInteger(version.data?.revision) || version.data.revision < 1)
      throw safeError('Maintenance revision unavailable; refresh before retrying')
    return request(route, '/maintenance', { method: 'POST', body: {
      entity_id: entity, state, reason: reason.trim(), expires_at: state === 'active' ? expires_at : null,
      expected_config_revision: version.data.revision, expected_owner: owner
    } }, owner)
  }
  return { inventory, request, refresh, repair, maintenance }
}

function fresh(meta, now) {
  return (
    meta?.freshness === 'fresh' &&
    Date.parse(meta.expires_at) > now &&
    Date.parse(meta.observed_at) <= now &&
    now - Date.parse(meta.observed_at) <= 120000
  )
}
export function ago(stamp, now = Date.now()) {
  const t = Date.parse(stamp)
  if (!Number.isFinite(t)) return 'not observed'
  const seconds = Math.max(0, Math.floor((now - t) / 1000))
  return seconds < 60 ? `${seconds}s ago` : `${Math.floor(seconds / 60)}m ago`
}
const HOLD_LABELS = {
  drained: 'Drained (intentional)',
  maintenance: 'Maintenance',
  disk_pressure: 'Withheld: disk pressure',
  root_headroom: 'Waiting for root headroom',
  label_withdrawn: 'Label withdrawn by supervisor (historical)'
}
const OPERATIONAL_SOURCES = ['systemd', 'maintenance_marker', 'admission_journal', 'runner_journal', 'supervisor_log']
function operationalTime(value) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    ? Date.parse(value) : NaN
}
function operationalEvidence(items, stale, now) {
  if (!Array.isArray(items)) return []
  return items.filter(e => e && Object.hasOwn(HOLD_LABELS, e.kind)).map(e => {
    const historical = e.kind === 'label_withdrawn'
    const current = !stale && OPERATIONAL_SOURCES.includes(e.source) &&
      e.freshness === 'fresh' && operationalTime(e.observed_at) <= now &&
      operationalTime(e.expires_at) > now &&
      (e.recorded_at == null || operationalTime(e.recorded_at) <= now)
    return {
      ...e, label: historical && !Number.isFinite(operationalTime(e.recorded_at))
        ? 'Supervisor label withdrawal' : HOLD_LABELS[e.kind],
      timestamp: historical ? e.recorded_at : e.observed_at,
      held: !historical && current && e.state === 'held',
      status: historical ? (Number.isFinite(operationalTime(e.recorded_at)) && operationalTime(e.recorded_at) <= now
        ? 'Historical — not a current hold' : 'Not observed')
        : e.state === 'unknown' || ['unknown', 'unavailable'].includes(e.freshness) ? 'Not observed'
          : !current ? 'Last-known / stale'
            : e.state === 'held' ? 'Held' : e.state === 'clear' ? 'Clear' : 'Not observed'
    }
  })
}
const SOURCE_LABELS = {
  systemd: 'systemd', maintenance_marker: 'maintenance marker', admission_journal: 'admission journal',
  runner_journal: 'runner journal', supervisor_log: 'supervisor log'
}
const CHECK_KEY = /^(?:disk|service|tool:[A-Za-z0-9_.+-]{1,64}|operational:[a-z_]{1,32}:[a-z_]{1,32})$/
export function checkLabel(key) {
  if (key === 'disk') return 'Disk'
  if (key === 'service') return 'Service'
  if (key.startsWith('tool:')) return `Tool ${key.slice(5)}`
  const [, kind, src] = key.split(':')
  return `${HOLD_LABELS[kind] || kind} (${SOURCE_LABELS[src] || src})`
}
// Validates the owner's optional per-check readiness causes. Anything that is
// malformed, not fresh on the client clock, or internally inconsistent is
// discarded so the aggregate failure stays conservative.
function readinessCauses(e, now) {
  if (!e || typeof e !== 'object') return null
  const lists = ['required', 'passed', 'failed', 'warned', 'hold_explained_failures']
  if (!lists.every(k => Array.isArray(e[k]) && e[k].length <= 256 && e[k].every(v => typeof v === 'string' && CHECK_KEY.test(v)))) return null
  if (e.freshness !== 'fresh' || !(operationalTime(e.observed_at) <= now) || !(operationalTime(e.expires_at) > now)) return null
  const required = new Set(e.required), failed = new Set(e.failed)
  if (![...e.passed, ...e.failed, ...e.warned].every(k => required.has(k))) return null
  if (!e.hold_explained_failures.every(k => failed.has(k))) return null
  const settled = new Set([...e.passed, ...e.failed, ...e.warned])
  return {
    failed: [...failed], warned: [...new Set(e.warned)], explained: [...new Set(e.hold_explained_failures)],
    notObserved: [...required].filter(k => !settled.has(k))
  }
}
function provenanceFresh(p, now, maxAge = Infinity) {
  return p?.state === 'fresh' && operationalTime(p.observed_at) <= now &&
    operationalTime(p.expires_at) > now && now - operationalTime(p.observed_at) <= maxAge
}
// Pure client classification. Raw activity is retained, never used as eligibility.
export function rowState(r, source, hostRow, now) {
  const meta = source.snapshot.meta
  const provenance = meta.provenance?.find(p => p.source_id === r.id)
  const coverage = meta.coverage?.find(c => c.scope_id === r.scope_id)
  const invalidIdentity = !ID_PATTERN.test(r.id || '') || !/^[1-9][0-9]{0,19}$/.test(r.provider_runner_id || '') ||
    !UUID.test(meta.owner?.gateway_id || '') || !source.snapshot.data.scopes.some(s => s.id === r.scope_id && s.provider === r.provider && s.endpoint === r.endpoint)
  const sourceStale = !!source.error || !['ready', 'partial'].includes(source.snapshot.data.state) || !fresh(meta, now)
  const stale = sourceStale || !provenanceFresh(provenance, now, 120000) || r.freshness !== 'fresh' ||
    coverage?.state !== 'complete' || coverage?.pagination_complete !== true
  // Hold age is independent of provider age. A fresh hold can describe a stale
  // listener, but cannot make that listener's activity current.
  const operational = operationalEvidence(r.operational_evidence, sourceStale || invalidIdentity, now)
  const holds = [...new Set(operational.filter(e => e.held).map(e => e.label))]
  const holdReason = operational.filter(e => e.held).map(e => `${e.label} (${SOURCE_LABELS[e.source] || e.source})`).join(' · ')
  const unresolvedHold = operational.some(e => e.kind !== 'label_withdrawn' && !e.held && e.status !== 'Clear')
  const expiredHold = operational.some(e => e.kind !== 'label_withdrawn' && e.state === 'held' && operationalTime(e.expires_at) <= now)
  const checkProvenance = meta.provenance?.find(p => hostRow?.resource_id && p.source_id === hostRow.resource_id)
  const checks = {
    state: !sourceStale && provenanceFresh(checkProvenance, now) && ['pass', 'warn', 'fail'].includes(r.readiness) ? r.readiness : 'unknown',
    observedAt: checkProvenance?.observed_at || null, expiresAt: checkProvenance?.expires_at || null,
    evidenceIds: checkProvenance?.observation_ids || [],
    causes: false, failed: [], explained: [], unexplained: [], warned: [], notObserved: []
  }
  const causes = checks.state === 'unknown' || invalidIdentity ? null : readinessCauses(r.readiness_evidence, now)
  if (causes && (checks.state !== 'fail' || causes.failed.length)) {
    // An explanation is trusted only when this client also sees the matching
    // hold as current; service is explained only by a current systemd drain.
    const current = new Set(operational.filter(e => e.held).map(e => `operational:${e.kind}:${e.source}`))
    const explained = causes.explained.filter(k => k === 'service' ? current.has('operational:drained:systemd') : current.has(k))
    Object.assign(checks, {
      causes: true, failed: causes.failed, explained, warned: causes.warned, notObserved: causes.notObserved,
      unexplained: causes.failed.filter(k => !explained.includes(k))
    })
  }
  const holdOnly = checks.state === 'fail' && checks.causes && checks.unexplained.length === 0 && holds.length > 0
  let bucket = 'unknown', bucketLabel = 'Unknown', reason = 'Activity unknown'
  const conflicting = r.registration === 'offline' && r.activity === 'busy'
  if (invalidIdentity) {
    bucketLabel = 'Identity unknown'; reason = 'Invalid or incomplete registration identity'
  } else if (stale) {
    bucketLabel = 'Stale'; reason = 'Last-known observations; current activity unverified'
  } else if (conflicting) {
    bucketLabel = 'Conflicting observations'; reason = 'Provider reports offline and busy; current activity unverified'
  } else if (checks.state === 'fail' && !holdOnly) {
    // Never suppress an independent failure just because a hold exists. Without
    // usable per-check causes the aggregate stays a failure.
    bucket = 'failed'; bucketLabel = 'Failed'
    reason = checks.causes && checks.unexplained.length
      ? `Failed: ${checks.unexplained.map(checkLabel).join(', ')}`
      : 'Required checks failed; individual check causes not reported'
  } else if (r.activity === 'busy' && r.registration === 'online') {
    bucket = 'running'; bucketLabel = 'Running'
    reason = holds.length ? 'Running · draining / no new jobs' : 'Running — job not identified'
  } else if (holds.length) {
    bucket = 'held'; bucketLabel = 'Held'; reason = `Intentional hold; provider: ${r.registration}`
  } else if (checks.state === 'warn') {
    bucket = 'held'; bucketLabel = 'Degraded'; reason = 'Required check warning — not an intentional hold'
  } else if (unresolvedHold) {
    bucketLabel = expiredHold ? 'Hold expired · awaiting evidence' : 'Hold unconfirmed · awaiting evidence'
    reason = bucketLabel
  } else if (r.registration !== 'online') {
    bucketLabel = ({ offline: 'Offline observation', missing: 'Missing' })[r.registration] || 'Registration unknown'
    reason = r.registration === 'offline' ? 'Provider reports offline; failure not confirmed' : bucketLabel
  } else if (r.activity === 'idle') {
    bucket = 'idle'; bucketLabel = 'Idle'
    reason = checks.state === 'pass' ? 'Provider idle · checks passed; not a free slot' : 'Provider idle · checks not reported'
  } else {
    bucketLabel = 'Activity unknown'
  }
  if (holdReason) reason += ` · ${holdReason}`
  if (checks.warned.length && bucketLabel !== 'Degraded') reason += ` · Required check warning: ${checks.warned.map(checkLabel).join(', ')}`
  else if (checks.warned.length) reason = `Required check warning: ${checks.warned.map(checkLabel).join(', ')} — not an intentional hold`
  else if (bucket === 'running' && checks.state === 'warn') reason += ' · Required check warning'
  // Host maintenance is not a runner hold, but like the owner API it withholds
  // the verified-ready claim while it is active.
  const hostMaintenance = hostRow?.maintenance?.state === 'active' && Date.parse(hostRow.maintenance.expires_at) > now
  if (bucket === 'idle' && hostMaintenance) reason += ' · Host maintenance; not counted ready'
  const ready = bucket === 'idle' && checks.state === 'pass' && !hostMaintenance
  return {
    operational, holds, stale, checks, bucket, bucketLabel, health: bucketLabel, reason,
    lastKnownActivity: ['busy', 'idle'].includes(r.activity) ? r.activity : null,
    evidence: { state: stale ? 'unknown' : 'fresh', observedAt: provenance?.observed_at || null, expiresAt: provenance?.expires_at || null },
    maintenance: operational.some(e => e.held && e.kind === 'maintenance'),
    issue: bucket === 'failed' || bucket === 'unknown' || checks.state === 'warn' || checks.warned.length > 0,
    ready, inspectable: !source.error && !invalidIdentity,
    actionable: !stale && !invalidIdentity && !conflicting,
    tone: bucket === 'failed' ? 'bad' : bucket === 'held' ? 'warn' : ready ? 'good' : 'muted'
  }
}
export function projectFleet(sources = [], now = Date.now()) {
  const rows = new Map(),
    messages = [],
    coverage = []
  for (const entry of sources) {
    const source = {
      ...entry,
      error:
        entry.error ||
        (['owner_disconnected', 'plugin_unavailable', 'owner_conflict'].includes(
          entry.snapshot?.data.state
        )
          ? STATES[entry.snapshot.data.state]
          : null)
    }
    const s = source.snapshot,
      label = source.route
        ? `${source.route.connectionId} / ${source.route.targetProfile}`
        : 'Sources'
    if (source.enrolled === false) messages.push(`${label}: Not enrolled`)
    if (source.error) messages.push(`${label}: ${source.error}`)
    if (!s) continue
    if (STATES[s.data.state]) messages.push(`${label}: ${STATES[s.data.state]}`)
    if (!fresh(s.meta, now))
      messages.push(
        `${label}: Stale or unobserved inventory · checked ${ago(s.meta.observed_at, now)}`
      )
    for (const c of s.meta.coverage || [])
      coverage.push({
        ...c,
        label,
        scope: s.data.scopes.find((x) => x.id === c.scope_id)?.name || c.scope_id
      })
    for (const e of s.meta.source_errors || []) {
      const remaining = typeof e.retry_after_seconds === 'number'
        ? Math.ceil((Date.parse(e.observed_at) + e.retry_after_seconds * 1000 - now) / 1000)
        : 0
      messages.push(`${label}: ${text(e.source_id)} · ${text(e.code)}${Number.isFinite(remaining) && remaining > 0 ? ` · retry in ${remaining}s` : ''}`)
    }
    for (const r of s.data.runners) {
      const scope = s.data.scopes.find((x) => x.id === r.scope_id),
        hostRow = s.data.hosts.find((x) => x.id === r.host_id)
      // Missing scope identity is never enough evidence to merge remote handles.
      const key = JSON.stringify([
        r.provider,
        r.endpoint,
        scope?.kind || ownerKey(s.meta.owner),
        scope?.name || r.scope_id,
        r.provider_runner_id
      ])
      const origin = { ...source, runnerId: r.id }
      const candidate = {
        ...r,
        ...rowState(r, source, hostRow, now),
        repair: (source.repairs || []).some(repair => !['closed', 'failed'].includes(repair.state) &&
          (repair.entity_id === r.id || (hostRow && (repair.entity_id === hostRow.resource_id ||
            s.data.runners.some(sibling => sibling.id === repair.entity_id && sibling.host_id === r.host_id))))) ? 'active' : 'none',
        key,
        host: hostRow,
        scope,
        sources: [origin],
        source: origin,
        conflict: false,

        observed_at: s.meta.provenance?.find((p) => p.source_id === r.id)?.observed_at || null,
        hostKey: hostRow ? JSON.stringify([ownerKey(s.meta.owner), hostRow.id]) : 'unmapped'
      }
      if (rows.has(key)) {
        const previous = rows.get(key),
          attribution = [...previous.sources, origin]
        // Prefer reachable, newest observations, then the conservative state and
        // stable route key. Discovery order must never decide readiness.
        const score = (x) => [
          Number(!x.source.error),
          Date.parse(x.observed_at) || 0,
          Number(x.issue),
          Number(x.activity === 'busy')
        ]
        const a = score(candidate),
          b = score(previous)
        let order = 0
        for (let i = 0; i < a.length && !order; i++) order = a[i] - b[i]
        const row =
          order > 0 ||
          (!order && routeKey(candidate.source.route) < routeKey(previous.source.route))
            ? candidate
            : previous
        row.sources = attribution
        row.conflict = new Set(attribution.map((x) => ownerKey(x.snapshot.meta.owner))).size > 1
        row.actionable = row.actionable && !row.conflict
        if (row.conflict) {
          row.inspectable = false
          row.holds = []
          row.maintenance = false
          row.operational = row.operational.map(e => ({ ...e, held: false, status: 'Owner conflict — unverified' }))
          row.bucket = 'unknown'
          row.bucketLabel = 'Ownership conflict'
          row.health = 'Ownership conflict'
          row.reason = 'Multiple owners claim this provider registration'
          row.tone = 'warn'
          row.ready = false
          row.issue = true
        }
        rows.set(key, row)
      } else rows.set(key, candidate)
    }
  }
  const list = selectRows([...rows.values()], '', 'All'),
    groups = groupRows(list)
  const counts = {
    registrations: list.length,
    online: list.filter((r) => r.registration === 'online').length,
    // Canonical API parity: last-known provider activity, not the display bucket.
    busy: list.filter((r) => r.activity === 'busy').length,
    ready: list.filter((r) => r.ready).length,
    unverified: list.filter((r) => r.registration === 'online' && !r.ready).length,
    issues: list.filter((r) => r.issue).length + groups.filter((g) => g.issue).length
  }
  const complete =
    sources.length > 0 &&
    sources.every((s) => s.snapshot && !s.error && fresh(s.snapshot.meta, now)) &&
    list.every(r => !r.stale && !r.conflict) &&
    coverage.length > 0 &&
    coverage.every((c) => c.state === 'complete' && c.pagination_complete) &&
    !messages.length
  if (!sources.length) messages.push('No connected owner gateways')
  if (!list.length && complete) messages.push('No registrations in fully read scopes')
  return {
    rows: list,
    groups,
    counts,
    messages: [...new Set(messages)],
    coverage,
    complete,
    tone: list.some((r) => r.tone === 'bad')
      ? 'bad'
      : counts.issues
        ? 'warn'
        : !complete ||
            list.some(
              (r) => r.stale || r.conflict || ['Unknown', 'Readiness unverified'].includes(r.health)
            )
          ? 'muted'
          : list.length && list.every(r => r.ready)
            ? 'good'
            : 'muted'
  }
}
export function bucketCounts(rows) {
  const counts = { running: 0, idle: 0, held: 0, failed: 0, unknown: 0 }
  for (const r of rows) counts[Object.hasOwn(counts, r.bucket) ? r.bucket : 'unknown']++
  return counts
}
function cohortSummary(rows, now = Date.now()) {
  const counts = bucketCounts(rows)
  const priority = r => r.bucket === 'failed' ? 0 : r.holds.length || r.bucket === 'held' || r.checks?.state === 'warn' ? 1 : r.bucket === 'unknown' ? 2 : 3
  const blocking = [...rows].filter(r => priority(r) < 3).sort((a, b) => priority(a) - priority(b) || a.key.localeCompare(b.key))[0]
  const stamps = rows.flatMap(r => [r.observed_at, ...(r.checks.state !== 'unknown' ? [r.checks.observedAt] : []), ...r.operational.filter(e => e.kind !== 'label_withdrawn').map(e => e.observed_at)])
  const known = stamps.filter(s => Number.isFinite(operationalTime(s)))
  const mixed = known.length > 0 && (known.length !== stamps.length || new Set(known).size > 1)
  const oldest = known.sort((a, b) => Date.parse(a) - Date.parse(b))[0]
  return { counts, registrationCount: rows.length, reason: blocking?.reason || null,
    age: !rows.length ? null : mixed ? 'Mixed ages' : oldest ? ago(oldest, now) : 'Not observed' }
}
export function projectNodes(sources = [], projectedRows = [], now = Date.now()) {
  const nodes = new Map()
  // Select a usable owner response before recency, with stable route tie-breaking.
  // Envelope time selects the response only; it is never node observation age.
  const ordered = sources.filter(s => s.snapshot).map(entry => ({
    ...entry,
    error: entry.error || (['owner_disconnected', 'plugin_unavailable', 'owner_conflict'].includes(entry.snapshot.data.state) ? STATES[entry.snapshot.data.state] : null)
  })).sort((a, b) =>
    Number(!b.error) - Number(!a.error) ||
    (Date.parse(b.snapshot?.meta.observed_at) || 0) - (Date.parse(a.snapshot?.meta.observed_at) || 0) ||
    routeKey(a.route).localeCompare(routeKey(b.route)))
  for (const source of ordered) {
    const snapshot = source.snapshot
    if (!snapshot) continue
    for (const host of snapshot.data.hosts) {
      const key = JSON.stringify([ownerKey(snapshot.meta.owner), host.id])
      if (nodes.has(key)) continue
      const probe = snapshot.meta.provenance?.find(p => host.resource_id && p.source_id === host.resource_id)
      const observedAt = Number.isFinite(Date.parse(probe?.observed_at)) ? probe.observed_at : null
      const freshness = { observedAt, state: source.error ? 'stale'
        : probe?.state === 'unavailable' ? 'unavailable'
          : !observedAt || operationalTime(probe.observed_at) > now ? 'unknown'
            : probe?.state === 'stale' || Date.parse(probe?.expires_at) <= now ? 'stale'
              : provenanceFresh(probe, now) && fresh(snapshot.meta, now) ? 'fresh' : 'unknown' }
      const runners = projectedRows.filter(r => r.hostKey === key)
      const summary = cohortSummary(runners, now)
      nodes.set(key, {
        key, host, owner: snapshot.meta.owner, source, runners,
        ...summary,
        capacity: {
          enforced: host.capacity?.enforced === true && Number.isInteger(host.capacity?.declared_max_jobs) && host.capacity.declared_max_jobs > 0 && host.capacity.declared_max_jobs <= 1024,
          limit: host.capacity?.enforced === true && Number.isInteger(host.capacity?.declared_max_jobs) && host.capacity.declared_max_jobs > 0 && host.capacity.declared_max_jobs <= 1024 ? host.capacity.declared_max_jobs : null,
          active: Number.isInteger(host.capacity?.observed_active_jobs) && host.capacity.observed_active_jobs >= 0 ? host.capacity.observed_active_jobs : null
        },
        maintenance: {
          active: host.maintenance?.state === 'active' && Date.parse(host.maintenance.expires_at) > now,
          reason: host.maintenance?.reason || null,
          expiresAt: host.maintenance?.expires_at || null
        },
        monitoring: source.error ? 'unknown' : host.management || 'unknown',
        health: {
          state: 'unknown',
          reason: source.error ? `Owner unavailable · ${source.error} · last-known node` : host.management === 'reachable'
            ? 'Reachable · health unverified'
            : host.management === 'unreachable'
              ? 'Management probe unavailable — not proof of a node outage'
              : 'Monitoring unavailable · health unverified'
        },
        activity: {
          busy: runners.length ? summary.counts.running : null,
          idle: runners.length ? summary.counts.idle : null,
          unknown: summary.counts.unknown
        },
        freshness
      })
    }
  }
  return { nodes: [...nodes.values()], unassigned: projectedRows.filter(r => !nodes.has(r.hostKey)) }
}
function highlight(value, query) {
  const label = text(value), lower = label.toLowerCase()
  const terms = query.toLowerCase().trim().split(/\s+/).filter(Boolean)
  const marked = Array(label.length).fill(false)
  for (const term of terms) {
    let at = lower.indexOf(term)
    while (at >= 0) {
      marked.fill(true, at, at + term.length)
      at = lower.indexOf(term, at + term.length)
    }
  }
  const parts = []
  for (let start = 0; start < label.length;) {
    let end = start + 1
    while (end < label.length && marked[end] === marked[start]) end++
    parts.push(marked[start] ? h('mark', { key: start }, label.slice(start, end)) : label.slice(start, end))
    start = end
  }
  return parts
}
export function selectNodes(projection, search = '', filter = 'All', now = Date.now()) {
  const terms = search.toLowerCase().trim().split(/\s+/).filter(Boolean)
  const groups = projection.nodes.flatMap(n => {
    const hostText = [n.host.display_name, n.host.os, n.host.arch].join(' ').toLowerCase()
    const hostMatches = terms.every(t => hostText.includes(t))
    const rows = selectRows(n.runners, hostMatches ? '' : search, filter)
    const nodeMatchesFilter = filter === 'All' ||
      (filter === 'Needs attention' && (n.source.error || n.monitoring !== 'reachable' || n.freshness.state !== 'fresh')) ||
      (['Maintenance', 'Held/maintenance'].includes(filter) && n.maintenance.active)
    return rows.length || (hostMatches && nodeMatchesFilter)
      ? [{ ...n, ...cohortSummary(rows, now), name: n.host.display_name, rows }] : []
  })
  const unassigned = selectRows(projection.unassigned, search, filter)
  if (unassigned.length) groups.push({ key: 'unmapped', name: 'Unassigned runners', rows: unassigned, ...cohortSummary(unassigned, now) })
  return { groups, rows: groups.flatMap(g => g.rows) }
}
export function selectRows(rows, search = '', filter = 'All') {
  const terms = search.toLowerCase().trim().split(/\s+/).filter(Boolean)
  return rows
    .filter((r) =>
      terms.every((t) =>
        [
          r.display_name,
          r.host?.display_name,
          r.scope?.name,
          r.observed_os,
          r.host?.arch,
          ...(r.labels || [])
        ]
          .join(' ')
          .toLowerCase()
          .includes(t)
      )
    )
    .filter(
      (r) =>
        filter === 'All' ||
        (filter === 'Needs attention' &&
          (r.issue ||
            r.stale ||
            r.conflict ||
            r.host?.management === 'unreachable' ||
            r.health === 'Unknown' ||
            r.health === 'Readiness unverified')) ||
        (['Busy', 'Running (Busy)'].includes(filter) && r.bucket === 'running') ||
        (filter === 'Idle' && r.bucket === 'idle') ||
        (filter === 'Failed' && r.bucket === 'failed') ||
        (filter === 'Unknown/stale' && r.bucket === 'unknown') ||
        (['Ready', 'Checks passed + idle (Ready)'].includes(filter) && r.ready) ||
        (filter === 'Held/maintenance' && (r.bucket === 'held' || r.holds.length > 0)) ||
        (filter === 'Maintenance' && r.maintenance)
    )
    .sort(
      (a, b) =>
        Number(b.issue || b.stale || b.conflict) - Number(a.issue || a.stale || a.conflict) ||
        (a.host?.display_name || 'Host not mapped').localeCompare(
          b.host?.display_name || 'Host not mapped'
        ) ||
        a.display_name.localeCompare(b.display_name) ||
        a.key.localeCompare(b.key)
    )
}
export function groupRows(rows) {
  const groups = new Map()
  for (const row of rows) {
    if (!groups.has(row.hostKey)) {
      const c = row.host?.capacity
      groups.set(row.hostKey, {
        key: row.hostKey,
        name: row.host?.display_name || 'Host not mapped',
        capacity:
          c?.enforced && c.declared_max_jobs != null
            ? `Enforced limit ${c.declared_max_jobs} · active ${c.observed_active_jobs ?? 'unknown'}`
            : `Capacity unknown${c?.declared_max_jobs != null ? ` · declared ${c.declared_max_jobs} (advisory)` : ''}`,
        issue:
          row.host?.management === 'unreachable'
            ? 'Host probe unavailable — not proof of a host outage'
            : null,
        rows: []
      })
    }
    groups.get(row.hostKey).rows.push(row)
  }
  return [...groups.values()]
}
export function chipLabel(v) {
  return `Runners · ${v.counts.online} online · ${v.counts.busy} busy${v.counts.issues ? ` · ${v.counts.issues} issues` : ''}${!v.complete ? ' · ?' : ''}`
}
export function chipTooltip(v) {
  return `${chipLabel(v)}. Counts are registrations, not available build slots. ${v.counts.ready} verified-ready; ${v.counts.unverified} readiness-unverified. ${v.complete ? 'Complete source coverage.' : 'Incomplete or stale source coverage.'}`
}
export function settingsURL(row) {
  const s = row.scope
  if (row.endpoint !== 'https://api.github.com' || !s) return null
  const parts = s.name.split('/')
  if (parts.some((p) => !/^[\w.-]+$/.test(p)) || !['repository', 'organization'].includes(s.kind))
    return null
  return s.kind === 'repository' && parts.length === 2
    ? `https://github.com/${parts.join('/')}/settings/actions/runners`
    : s.kind === 'organization' && parts.length === 1
      ? `https://github.com/organizations/${parts[0]}/settings/actions/runners`
      : null
}
export function jobURL(row, job) {
  if (
    !job?.owner ||
    !settingsURL(row) ||
    row.scope.kind !== 'repository' ||
    !/^\d+$/.test(job?.provider_run_id || '') ||
    !/^\d+$/.test(job?.provider_job_id || '') ||
    job.assigned_runner_id !== row.id ||
    ownerKey(job.owner) !== ownerKey(row.source.snapshot.meta.owner)
  )
    return null
  return `https://github.com/${row.scope.name}/actions/runs/${job.provider_run_id}/job/${job.provider_job_id}`
}
export function observedJobURL(row, job, now = Date.now()) {
  if (!job || row.stale || !row.actionable || !settingsURL(row) ||
      job.runner_id !== row.provider_runner_id ||
      !/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(job.repository || '') ||
      !/^\d+$/.test(job.run_id || '') || !/^\d+$/.test(job.job_id || '') ||
      !(Date.parse(job.observed_at) <= now) || !(Date.parse(job.expires_at) > now)) return null
  const scopeMatches = row.scope.kind === 'repository' ? job.repository === row.scope.name
    : row.scope.kind === 'organization' && job.repository.split('/')[0] === row.scope.name
  if (!scopeMatches) return null
  const url = `https://github.com/${job.repository}/actions/runs/${job.run_id}/job/${job.job_id}`
  return job.url === url ? url : null
}
export function diagnosticSummary(row) {
  return `${text(row.display_name)}\n${text(row.scope?.name)} · ${row.provider}\n${row.health} · ${row.activity}\n${row.reason}\nObserved ${row.observed_at || 'unknown'}\nOwner ${row.source.snapshot.meta.owner.gateway_id} / ${row.source.snapshot.meta.owner.profile}`
}

export const CSS = `
[data-slot=runners-chip]{flex-shrink:0;white-space:nowrap}
.hermes-runners {font-size:12px;line-height:1.45;color:var(--foreground);display:flex;flex-direction:column;gap:8px;min-width:0}
.hermes-runners button,.hermes-runners input,.hermes-runners select{font:inherit;color:inherit}
.hermes-runners button{cursor:pointer}.hermes-runners button:disabled{cursor:default;opacity:.5}
.hermes-runners :is(button,input,select):focus-visible{outline:2px solid var(--ui-accent);outline-offset:2px}
.hermes-runners-head,.hermes-runners-summary,.hermes-runners-filters{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.hermes-runners-head strong{margin-right:auto}.hermes-runners-summary,.hermes-runners-muted{font-size:11px;color:var(--ui-text-secondary)}
.hermes-runners input,.hermes-runners select{background:var(--ui-bg-elevated);border:1px solid var(--ui-stroke-secondary);border-radius:4px;padding:4px 6px;min-width:0;max-width:100%}
.hermes-runners-search{flex:1;width:100%;min-width:0!important}.hermes-runners-filters{flex-wrap:nowrap}.hermes-runners-filters select{width:170px;flex-shrink:1}
.hermes-runners-bar{display:flex;gap:2px;height:8px;margin:5px 0;width:100%}
.hermes-runners-segment{min-width:6px;border-radius:2px;box-sizing:border-box}
.hermes-runners-running{background:var(--ui-accent,#3778c2)}
.hermes-runners-idle{border:1px solid var(--ui-text-secondary);background:transparent}
.hermes-runners-held{background:repeating-linear-gradient(135deg,transparent 0 3px,var(--warning,#b87918) 3px 5px);border:1px solid var(--warning,#b87918)}
.hermes-runners-failed{background:var(--destructive,#c74949)}
.hermes-runners-unknown{border:1px dashed var(--ui-text-secondary);background:repeating-linear-gradient(90deg,transparent 0 4px,var(--ui-text-secondary) 4px 6px)}
.hermes-runners-key{display:flex;flex-wrap:wrap;gap:4px 10px;font-size:11px;color:var(--ui-text-secondary)}
.hermes-runners-unassigned{border:1px solid var(--ui-stroke-secondary);border-radius:5px;padding:5px}
.hermes-runners-blocking{display:block;font-size:11px;margin-top:4px;color:var(--ui-text-secondary)}
.hermes-runners-state-icon{font-size:11px;color:var(--ui-text-secondary)}
.hermes-runners-state-icon[data-bucket=running]{color:var(--ui-accent,#3778c2)}
.hermes-runners-state-icon[data-bucket=held]{color:var(--warning,#b87918)}
.hermes-runners-state-icon[data-bucket=failed]{color:var(--destructive,#c74949)}
.hermes-runners-filters button[aria-pressed=true],.hermes-runners button:hover{background:var(--chrome-action-hover)}
.hermes-runners-scroll{min-height:0;overflow:auto;overscroll-behavior:contain}
.hermes-runners-group{border-top:1px solid var(--ui-stroke-secondary);padding-top:6px;margin-top:6px}
.hermes-runners-host{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,auto);align-items:start;text-align:left;width:100%;gap:8px;padding:4px}
.hermes-runners-host>span{min-width:0;overflow-wrap:anywhere}.hermes-runners-host small{max-width:160px;text-align:right;overflow-wrap:anywhere}.hermes-runners-rowwrap{display:flex;align-items:start;gap:2px}
.hermes-runners-row{flex:1;min-width:0;display:grid;grid-template-columns:8px minmax(0,1fr) auto;gap:6px;text-align:left;padding:6px;border-radius:4px}
.hermes-runners-row span{min-width:0;overflow-wrap:anywhere}.hermes-runners-name{display:block;font-weight:500}.hermes-runners-meta{display:block;font-size:11px;color:var(--ui-text-secondary)}
.hermes-runners-overflow{padding:6px;flex:none}.hermes-runners-detail{margin:2px 8px 8px 20px;font-size:11px;overflow-wrap:anywhere}
.hermes-runners-detail dl{display:grid;grid-template-columns:110px minmax(0,1fr);gap:3px 8px}.hermes-runners-detail dd{margin:0}
.hermes-runners-note{padding:4px;overflow-wrap:anywhere;font-size:11px}.hermes-runners-narrow{display:none}
.hermes-runners>details{max-height:140px;overflow:auto;flex-shrink:0}
@media(max-width:1000px){.hermes-runners-full{display:none}.hermes-runners-narrow{display:inline}}
@media(max-width:520px){.hermes-runners-row{grid-template-columns:8px minmax(0,1fr)}.hermes-runners-row>span:last-child{grid-column:2}.hermes-runners-host small{margin-left:0}}
@media(prefers-reduced-motion:reduce){[data-runners-panel],[data-runners-menu]{animation:none!important;transition:none!important}}
`

function useVisible() {
  const [visible, set] = useState(document.visibilityState !== 'hidden')
  useEffect(() => {
    const update = () => set(document.visibilityState !== 'hidden')
    document.addEventListener('visibilitychange', update)
    return () => document.removeEventListener('visibilitychange', update)
  }, [])
  return visible
}
const BUCKET_LABELS = { running: 'running', idle: 'idle', held: 'held/degraded', failed: 'failed', unknown: 'unknown' }
const BUCKET_ICONS = { running: '▶', idle: '○', held: 'Ⅱ', failed: '×', unknown: '?' }
function RegistrationBar({ counts, total }) {
  if (!total) return null
  return h('span', { style: { display: 'block' } },
    h('span', { className: 'hermes-runners-bar', 'data-registration-bar': true, role: 'img',
      'aria-label': `${total} registrations: ${Object.entries(counts).map(([k, n]) => `${n} ${BUCKET_LABELS[k]}`).join(', ')}` },
      ...Object.entries(counts).filter(([, n]) => n).map(([k, n]) => h('span', {
        key: k, 'aria-hidden': true, className: `hermes-runners-segment hermes-runners-${k}`, style: { flex: `${n} 1 0%` }
      }))),
    h('span', { className: 'hermes-runners-key', 'aria-hidden': true },
      ...Object.entries(counts).map(([k, n]) => h('span', { key: k }, `${BUCKET_ICONS[k]} ${n} ${BUCKET_LABELS[k]}`))))
}
function Details({ row, transport, visible, onData }) {
  const q = useQuery({
    queryKey: [
      ...ownerQueryKey(row.source.snapshot.meta.owner),
      'detail',
      row.source.snapshot.meta.owner.owner_epoch,
      row.id
    ],
    queryFn: () =>
      transport.request(
        row.source.route,
        `/runners/${encodeURIComponent(row.id)}?history_limit=50`,
        {},
        row.source.snapshot.meta.owner
      ),
    enabled: visible && row.inspectable && ID_PATTERN.test(row.id),
    retry: false,
    staleTime: 15000
  })
  const d = q.data?.data
  useEffect(() => onData?.(d), [d, onData])
  return h(
    'div',
    { className: 'hermes-runners-detail' },
    h('strong', null, 'Source observations'),
    q.isPending ? h('p', null, row.inspectable ? 'Loading details…' : 'Owner evidence unavailable; showing last-known facts') : null,
    q.error ? h('p', { role: 'alert' }, failure(q.error)) : null,
    h(
      'dl',
      null,
      h('dt', null, 'Labels'),
      h('dd', null, (row.labels || []).map(text).join(', ') || 'None observed'),
      h('dt', null, 'Provider registration'),
      h('dd', null, text(row.registration)),
      h('dt', null, 'Runner lifecycle'),
      h('dd', null, text(row.lifecycle) || 'Not observed'),
      h('dt', null, 'Provider activity'),
      h('dd', null, `${row.stale || row.bucket === 'unknown' ? 'Last known: ' : ''}${row.lastKnownActivity || 'unknown'} · observed ${row.evidence.observedAt || 'Not observed'} · expires ${row.evidence.expiresAt || 'Not observed'}`),
      h('dt', null, 'Required checks'),
      h('dd', null, `${row.checks.state} · observed ${row.checks.observedAt || 'Not observed'} · expires ${row.checks.expiresAt || 'Not observed'}`,
        row.checks.causes ? [
          ['Failed (independent)', row.checks.unexplained], ['Failed (explained by current hold)', row.checks.explained],
          ['Warning', row.checks.warned], ['Not observed', row.checks.notObserved]
        ].filter(([, keys]) => keys.length).map(([label, keys]) => h('div', { key: label }, `${label}: ${keys.map(checkLabel).join(', ')}`))
          : h('div', null, 'Individual check causes: Not reported')),
      h('dt', null, 'OS / architecture'),
      h('dd', null, `${text(row.observed_os)} / ${text(row.host?.arch) || 'Not reported'}`),
      h('dt', null, 'Operational evidence'),
      h('dd', null, row.operational.length ? row.operational.map((e, i) =>
        h('div', { key: i },
          `${e.label} · ${e.status} · source ${text(e.source)} · state ${text(e.state)} · freshness ${text(e.freshness)}`,
          h('div', null, `Observed: ${text(e.observed_at) || 'Not observed'} · Expires: ${text(e.expires_at) || 'Not observed'} · Recorded: ${text(e.recorded_at) || 'Not observed'}`)
        )) : 'Not observed'),
      h('dt', null, 'Host service refs'),
      h('dd', null, row.host?.service_refs?.map(text).join(', ') || 'Not observed'),
      h('dt', null, 'Runner version'),
      h(
        'dd',
        null,
        text(d?.observations?.find((o) => o.dimension === 'runner_version')?.value) ||
          'Not observed'
      ),
      h('dt', null, 'Sources'),
      h(
        'dd',
        null,
        row.sources.map((s) => `${s.route.connectionId} / ${s.route.targetProfile}`).join(', ')
      )
    ),
    d
      ? h(
          'div',
          null,
          ...(d.observations || []).map((o) =>
            h(
              'p',
              { key: o.id },
              `${text(o.dimension)}: ${text(o.evidence_summary)} · ${ago(o.collected_at)} · ${Date.parse(o.expires_at) > Date.now() ? 'current' : 'stale'}`
            )
          ),
          h('strong', null, 'Recent transitions'),
          ...(d.incidents || []).flatMap((i) =>
            (i.transitions || []).map((t, n) =>
              h(
                'p',
                { key: `${i.id}-${n}` },
                `${text(t.from_state)} → ${text(t.to_state)} · ${ago(t.at)}`
              )
            )
          ),
          h('p', null, `Recent jobs: ${(d.jobs || []).length}`),
          !d.jobs?.length && !d.observed_jobs?.length ? h('p', null,
            ({ not_configured: 'Job details not configured', forbidden: 'Job details permission denied', unavailable: 'Job collector unavailable',
              configured: row.activity === 'busy' ? 'Current job not identified' : 'No attributed jobs observed' })[d.job_monitoring?.state] || 'Job attribution unavailable') : null,
          ...(d.jobs || []).map((j) =>
            h(
              'p',
              { key: j.id },
              `${text(j.provider_run_id)} · ${text(j.status)} · ${text(j.conclusion)}`
            )
          ),
          h('p', null, `Provider job observations: ${(d.observed_jobs || []).length}`),
          ...(d.observed_jobs || []).map(j => h('p', { key: `${j.repository}/${j.job_id}` },
            `${text(j.repository)} · ${text(j.job_id)} · ${text(j.status)} · ${text(j.conclusion)} · ${ago(j.observed_at)} · ${Date.parse(j.expires_at) > Date.now() ? 'current' : 'stale'}`)),
          h('p', null, `Repair history: ${(d.repairs || []).length}`),
          ...(d.repairs || []).map((r) =>
            h('p', { key: r.id }, `${text(r.state)} · ${text(r.mode)}`)
          ),
          !d.observations?.length ? h('p', null, 'No source observations recorded') : null
        )
      : null
  )
}
function RunnerRow({ row, expanded, onExpand, transport, onError, visible, features, os, search = '' }) {
  const ref = useRef(null)
  const [detail, setDetail] = useState(null)
  const [repairBusy, setRepairBusy] = useState(false)
  const [openedRepair, setOpenedRepair] = useState(null)
  const [maintenanceForm, setMaintenanceForm] = useState(false)
  const [maintenanceReason, setMaintenanceReason] = useState('')
  const [maintenanceExpiry, setMaintenanceExpiry] = useState('')
  const [maintenanceBusy, setMaintenanceBusy] = useState(false)
  const queryClient = useQueryClient()
  const repairState = openedRepair ? 'active' : row.repair
  // A subsequent collector snapshot owns lifecycle state, including closure.
  useEffect(() => setOpenedRepair(null), [row.source.snapshot])
  const job = detail?.jobs?.find((j) => j.status === 'running' && j.assigned_runner_id === row.id)
  const observedJob = detail?.observed_jobs?.find(j => j.status === 'in_progress' && observedJobURL(row, j))
  const currentJob = row.bucket === 'running' && row.actionable && !row.stale
    ? (observedJob ? observedJobURL(row, observedJob) : job ? jobURL(row, job) : null) : null
  const currentJobId = observedJob?.job_id || job?.provider_job_id
  const displayReason = currentJob
    ? row.reason.replace('Running — job not identified', `Running — job ${currentJobId}`)
    : row.reason
  function openMenu(e) {
    e.preventDefault()
    e.stopPropagation()
    const el = ref.current,
      r = el.getBoundingClientRect()
    el.dispatchEvent(
      new MouseEvent('contextmenu', {
        bubbles: true,
        clientX: r.right - 10,
        clientY: r.top + 10,
        button: 2
      })
    )
  }
  const repairLabel =
    repairState === 'active'
      ? 'Open repair session'
      : row.issue
        ? 'Repair in new session'
        : 'Investigate in new session'
  return h(
    ContextMenu,
    {},
    h(
      ContextMenuTrigger,
      { asChild: true },
      h(
        'div',
        {
          onKeyDown: (e) => {
            if (e.key === 'F10' && e.shiftKey) openMenu(e)
          }
        },
        h(
          'div',
          { className: 'hermes-runners-rowwrap' },
          h(
            'button',
            {
              ref,
              type: 'button',
              'data-runner-row': row.key,
              className: 'hermes-runners-row',
              'aria-label': `${row.display_name} · ${row.bucketLabel} · ${displayReason}`,
              'aria-expanded': expanded,
              onClick: () => onExpand(row.key),
              onKeyDown: (e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  onExpand(row.key)
                }
              }
            },
            h('span', { className: 'hermes-runners-state-icon', 'data-bucket': row.bucket, 'aria-hidden': true }, BUCKET_ICONS[row.bucket]),
            h(
              'span',
              null,
              h('span', { className: 'hermes-runners-name' }, ...highlight(row.display_name, search)),
              h(
                'span',
                { className: 'hermes-runners-meta' },
                ...highlight(`${text(row.scope?.name)} · ${row.provider} · ${(row.labels || []).filter((label, i) => i < 3 || search.trim().toLowerCase().split(/\s+/).filter(Boolean).some(term => text(label).toLowerCase().includes(term))).map(text).join(', ')}`, search),
                search.trim() && search.toLowerCase().trim().split(/\s+/).some(t => `${row.observed_os} ${row.host?.arch || ''}`.toLowerCase().includes(t))
                  ? h('span', null, ' · ', ...highlight(`${row.observed_os} ${row.host?.arch || ''}`, search)) : null
              ),
              h(
                'span',
                { className: 'hermes-runners-meta' },
                displayReason
              )
            ),
            h(
              'span',
              null,
              row.bucketLabel,
              h('span', { className: 'hermes-runners-meta' }, ago(row.observed_at)),
              !['none', 'closed'].includes(repairState)
                ? h('span', { className: 'hermes-runners-meta' }, `Repair ${repairState}`)
                : null
            )
          ),
          h(
            'button',
            {
              type: 'button',
              className: 'hermes-runners-overflow',
              'aria-label': `Actions for ${row.display_name}`,
              'aria-haspopup': 'menu',
              onClick: openMenu
            },
            '⋯'
          )
        ),
        currentJob
          ? h(
              'button',
              {
                type: 'button',
                className: 'hermes-runners-muted',
                onClick: () => os?.openExternal?.(currentJob)
              },
              `Open current job ${currentJobId}`
            )
          : null,
        expanded ? h(Details, { row, transport, visible, onData: setDetail }) : null,
        maintenanceForm ? h('form', {
          className: 'hermes-runners-detail',
          onSubmit: async e => {
            e.preventDefault()
            if (maintenanceBusy) return
            setMaintenanceBusy(true)
            onError('')
            try {
              await transport.maintenance(row, {
                state: row.maintenance ? 'inactive' : 'active', reason: maintenanceReason,
                expires_at: row.maintenance ? null : new Date(maintenanceExpiry).toISOString()
              })
              await queryClient.invalidateQueries({ queryKey: ['runners'] })
              setMaintenanceForm(false)
            } catch (error) { onError(error instanceof RangeError ? 'Choose a future maintenance expiry' : failure(error)) }
            finally { setMaintenanceBusy(false) }
          }
        },
          h('p', null, 'Maintenance is a label only; it does not drain jobs or authorize restarts.'),
          h('input', { 'aria-label': 'Maintenance reason', required: true, maxLength: 512, value: maintenanceReason,
            onChange: e => setMaintenanceReason(e.target.value) }),
          !row.maintenance ? h('input', { 'aria-label': 'Maintenance expiry', type: 'datetime-local', required: true,
            value: maintenanceExpiry, onChange: e => setMaintenanceExpiry(e.target.value) }) : null,
          h('button', { type: 'submit', disabled: maintenanceBusy }, row.maintenance ? 'End maintenance' : 'Save maintenance'),
          h('button', { type: 'button', onClick: () => setMaintenanceForm(false) }, 'Cancel')
        ) : null
      )
    ),
    h(
      ContextMenuContent,
      {
        'data-runners-menu': true,
        onCloseAutoFocus: (e) => {
          e.preventDefault()
          ref.current?.focus()
        },
        onEscapeKeyDown: (e) => e.stopPropagation()
      },
      features.repair_sessions
        ? h(ContextMenuItem, {
            disabled: !row.actionable || repairBusy,
            onSelect: async () => {
              setRepairBusy(true)
              onError('')
              try { setOpenedRepair(await transport.repair(row)) }
              catch (e) { onError(failure(e)) }
              finally { setRepairBusy(false) }
            }
          }, repairBusy ? 'Opening repair session…' : repairLabel)
        : null,
      h(
        ContextMenuItem,
        {
          disabled: !row.actionable,
          onSelect: () => transport.refresh(row.source, [row.id]).catch((e) => onError(failure(e)))
        },
        'Refresh this runner'
      ),
      h(
        ContextMenuItem,
        {
          disabled: !settingsURL(row),
          onSelect: () => os?.openExternal?.(settingsURL(row))
        },
        'Open GitHub runner settings'
      ),
      h(
        ContextMenuItem,
        { disabled: !currentJob, onSelect: () => os?.openExternal?.(currentJob) },
        currentJob ? 'Open current job' : 'Current job not identified'
      ),
      h(ContextMenuSeparator),
      h(
        ContextMenuItem,
        {
          onSelect: async () => {
            try {
              if (!(await os?.writeClipboard?.(diagnosticSummary(row))))
                onError('Could not copy diagnostic summary')
            } catch {
              onError('Could not copy diagnostic summary')
            }
          }
        },
        'Copy diagnostic summary'
      ),
      h(
        ContextMenuItem,
        { disabled: !features.repair_sessions || !row.actionable || !ID_PATTERN.test(row.host?.resource_id || ''),
          onSelect: () => setMaintenanceForm(true) },
        features.repair_sessions ? (row.maintenance ? 'End maintenance…' : 'Mark maintenance…') : 'Maintenance controls unavailable — does not drain jobs'
      )
    )
  )
}

export function Runners({ ctx, features = null }) {
  const [open, setOpen] = useState(false),
    [search, setSearch] = useState(''),
    [filter, setFilter] = useState('All'),
    [scope, setScope] = useState('all'),
    [expanded, setExpanded] = useState(null),
    [expandedNodes, setExpandedNodes] = useState(new Set()),
    [help, setHelp] = useState(false),
    [actionError, setActionError] = useState('')
  const visible = useVisible(),
    client = useQueryClient(),
    transportRef = useRef(null),
    trigger = useRef(null),
    panel = useRef(null),
    searchRef = useRef(null),
    searchHandoff = useRef(false)
  useLayoutEffect(() => {
    if (!searchHandoff.current) return
    searchHandoff.current = false
    searchRef.current?.focus()
    searchRef.current?.setSelectionRange(search.length, search.length)
  }, [search])
  if (!transportRef.current) transportRef.current = createTransport(ctx)
  const transport = transportRef.current
  const gateways = useValue(host.state.gateways || host.state.connectionId),
    profile = useValue(host.state.profile)
  const directoryKey = ['runners', 'directory', JSON.stringify(gateways), profile]
  const q = useQuery({
    queryKey: directoryKey,
    queryFn: () => transport.inventory(client),
    enabled: visible,
    refetchInterval: pollInterval(open, visible),
    refetchIntervalInBackground: false,
    retry: false,
    staleTime: 1000
  })
  const last = useRef([])
  if (q.data) last.current = q.data
  const sources =
    q.data ||
    last.current.map((s) => ({ ...s, error: 'Owner routes refreshing — last-known observations' }))
  const selected =
    scope === 'all' ? sources : sources.filter((s) => s.route && routeKey(s.route) === scope)
  const view = projectFleet(selected),
    nodes = projectNodes(selected, view.rows),
    { groups, rows } = selectNodes(nodes, search, filter)
  // Prune against the complete inventory, not the filtered/scope-visible cohort.
  const inventory = projectFleet(sources)
  const inventoryNodes = projectNodes(sources, inventory.rows)
  const nodeIdentityKey = JSON.stringify([
    ...inventoryNodes.nodes.map(n => n.key),
    ...(inventoryNodes.unassigned.length ? ['unmapped'] : [])
  ].sort())
  useEffect(() => {
    const present = new Set(JSON.parse(nodeIdentityKey))
    setExpandedNodes(old => {
      const next = new Set([...old].filter(key => present.has(key)))
      return next.size === old.size ? old : next
    })
  }, [nodeIdentityKey])
  const [jumpFocus, setJumpFocus] = useState(null)
  const { groups: paletteGroups } = selectNodes(inventoryNodes, '', 'All')
  // Observations refresh frequently; only contribution identity, labels or routing
  // changes should replace entries while the native palette is open.
  const paletteKey = JSON.stringify(paletteGroups.map(group => [
    group.key, group.name, group.source?.route, group.host?.os, group.host?.arch,
    group.rows.map(row => [row.key, row.display_name, row.source.route,
      row.observed_os, row.host?.arch, row.scope?.name, settingsURL(row)])
  ]))
  useEffect(() => {
    if (!ctx.register) return
    const allGroups = paletteGroups
    const disposers = []
    for (const group of allGroups) {
      const jump = (row = null) => {
        setScope(row ? routeKey(row.source.route) : group.source ? routeKey(group.source.route) : 'all')
        setSearch('')
        setFilter('All')
        setExpandedNodes(old => new Set([...old, group.key]))
        setExpanded(row?.key || null)
        setOpen(true)
        setJumpFocus({ key: row?.key || group.key, kind: row ? 'runner' : 'node' })
      }
      if (group.host) disposers.push(ctx.register({
        id: `node:${group.key}`, area: PALETTE_AREA,
        data: { id: `runners.node:${group.key}`, label: `Show node: ${group.name}`,
          keywords: ['runners', 'host', text(group.host.os), text(group.host.arch)], run: () => jump() }
      }))
      for (const row of group.rows) {
        disposers.push(ctx.register({
          id: `runner:${row.key}`, area: PALETTE_AREA,
          data: { id: `runners.runner:${row.key}`, label: `Show runner: ${row.display_name}`,
            keywords: ['runners', group.name, text(row.observed_os), text(row.host?.arch)], run: () => jump(row) }
        }))
        const url = settingsURL(row)
        if (url) disposers.push(ctx.register({
          id: `settings:${row.key}`, area: PALETTE_AREA,
          data: { id: `runners.settings:${row.key}`, label: `Runner settings: ${row.display_name}`,
            keywords: ['configuration', 'preferences', 'github', text(row.scope?.name)],
            run: () => ctx.os?.openExternal?.(url) }
        }))
      }
    }
    return () => disposers.forEach(dispose => dispose?.())
  }, [ctx, paletteKey])
  useEffect(() => {
    if (!open || !jumpFocus) return
    const frame = requestAnimationFrame(() => {
      panel.current?.querySelectorAll(`[data-${jumpFocus.kind}-row]`).forEach(el => {
        if (el.getAttribute(`data-${jumpFocus.kind}-row`) === jumpFocus.key) el.focus()
      })
      setJumpFocus(null)
    })
    return () => cancelAnimationFrame(frame)
  }, [open, jumpFocus])
  const refreshing = useRef(false)
  async function refresh() {
    if (refreshing.current) return
    refreshing.current = true
    setActionError('')
    try {
      await Promise.all(
        selected.filter((s) => s.snapshot && !s.error).map((s) => transport.refresh(s))
      )
    } catch (e) {
      setActionError(failure(e))
    } finally {
      refreshing.current = false
    }
  }
  const openRefreshed = useRef(false)
  useEffect(() => {
    const show = () => setOpen(true)
    window.addEventListener('hermes-show-runners', show)
    return () => window.removeEventListener('hermes-show-runners', show)
  }, [])
  useEffect(() => {
    if (!open) {
      openRefreshed.current = false
      return
    }
    if (q.data && !openRefreshed.current) {
      openRefreshed.current = true
      refresh()
    }
  }, [open, q.data])
  function onExpand(key) {
    setExpanded((old) => (old === key ? null : key))
  }
  function escape(e) {
    e.preventDefault()
    e.stopPropagation()
    if (help) { setHelp(false); return }
    if (expanded) {
      const key = expanded
      setExpanded(null)
      panel.current?.querySelectorAll('[data-runner-row]').forEach(el => {
        if (el.getAttribute('data-runner-row') === key) el.focus()
      })
    } else if (search) {
      setSearch('')
      searchRef.current?.focus()
    } else {
      const activeNode = document.activeElement?.closest('.hermes-runners-group')?.querySelector('[data-node-row]')
      const node = activeNode?.getAttribute('aria-expanded') === 'true' ? activeNode :
        panel.current?.querySelector('[data-node-row][aria-expanded="true"]')
      if (node) {
        setExpandedNodes(old => {
          const next = new Set(old)
          next.delete(node.getAttribute('data-node-row'))
          return next
        })
        node.focus()
      } else {
        setOpen(false)
        trigger.current?.focus()
      }
    }
  }
  function keyboard(e) {
    if (e.defaultPrevented || e.isComposing || e.nativeEvent?.isComposing || e.keyCode === 229 ||
        e.ctrlKey || e.metaKey || e.altKey || e.target.isContentEditable || e.target.closest?.('[role=menu]')) return
    const node = e.target.closest('.hermes-runners-group')?.querySelector('[data-node-row]')
    if (node && ['ArrowRight', 'ArrowLeft'].includes(e.key) && !['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) {
      e.preventDefault()
      const key = node.getAttribute('data-node-row')
      setExpandedNodes(old => {
        const next = new Set(old)
        e.key === 'ArrowRight' ? next.add(key) : next.delete(key)
        return next
      })
      if (e.key === 'ArrowLeft') { setExpanded(null); node.focus() }
    }
    if (e.key === 'Enter' && e.target.hasAttribute('data-node-row')) {
      e.preventDefault()
      e.target.click()
    }
    if (e.key.length === 1 && e.key !== '/' && e.key !== ' ' && e.key !== '?' &&
        !e.ctrlKey && !e.metaKey && !e.altKey && !e.isComposing &&
        !e.target.isContentEditable && !['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) {
      e.preventDefault()
      const next = search + e.key
      searchHandoff.current = true
      setSearch(next)
    }
    if (e.key === '?' && !e.ctrlKey && !e.metaKey && !e.altKey && !e.isComposing &&
        !e.target.isContentEditable && !['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) {
      e.preventDefault()
      setHelp(old => !old)
    }
    if (e.key === '/' && !['INPUT', 'TEXTAREA'].includes(e.target.tagName)) {
      e.preventDefault()
      searchRef.current?.focus()
    }
    if (['ArrowDown', 'ArrowUp'].includes(e.key) && e.target.tagName !== 'SELECT') {
      const buttons = [...panel.current.querySelectorAll('[data-node-row], [data-runner-row]')]
      if (buttons.length) {
        e.preventDefault()
        const index = buttons.indexOf(document.activeElement),
          next =
            e.key === 'ArrowDown' ? Math.min(index + 1, buttons.length - 1) : Math.max(index - 1, 0)
        buttons[next].focus()
      }
    }
    if (e.key === 'Escape') escape(e)
  }
  return h(
    Popover,
    { open, onOpenChange: setOpen },
    h(
      PopoverTrigger,
      { asChild: true },
      h(
        'button',
        {
          ref: trigger,
          type: 'button',
          className: BAR,
          'data-slot': 'runners-chip',
          'aria-label': `Show runners: ${chipLabel(view)}`,
          'aria-description': chipTooltip(view),
          title: chipTooltip(view)
        },
        h(StatusDot, { tone: view.tone }),
        h('span', { className: 'hermes-runners-full' }, chipLabel(view)),
        h(
          'span',
          { className: 'hermes-runners-narrow', 'aria-hidden': true },
          `Runners · ${view.counts.issues} issues${!view.complete ? ' · ?' : ''}`
        )
      )
    ),
    h(
      PopoverContent,
      {
        side: 'top',
        align: 'end',
        // Do not tether the panel to a status item beyond a narrow viewport.
        sticky: 'always',
        'aria-label': 'Runners',
        style: {
          width: 'min(680px, calc(100vw - 24px))',
          maxHeight: '70vh',
          overflow: 'hidden'
        },
        onOpenAutoFocus: (e) => {
          e.preventDefault()
          searchRef.current?.focus()
        },
        onEscapeKeyDown: escape,
        onCloseAutoFocus: (e) => {
          e.preventDefault()
          trigger.current?.focus()
        }
      },
      h(
        'div',
        {
          ref: panel,
          'data-runners-panel': true,
          'data-side': 'top',
          className: 'hermes-runners',
          style: { maxHeight: 'calc(70vh - 32px)' },
          onKeyDown: keyboard
        },
        h(
          'div',
          { className: 'hermes-runners-head' },
          h('strong', null, 'Runners'),
          h(
            'select',
            {
              'aria-label': 'Gateway scope',
              value: scope,
              onChange: (e) => setScope(e.target.value)
            },
            h('option', { value: 'all' }, 'All connected'),
            ...sources
              .filter((s) => s.route)
              .map((s) =>
                h(
                  'option',
                  { key: routeKey(s.route), value: routeKey(s.route) },
                  `${s.route.connectionId} / ${s.route.targetProfile}`
                )
              )
          ),
          h(
            'button',
            {
              type: 'button',
              onClick: () => {
                refresh()
                q.refetch()
              }
            },
            'Refresh'
          )
        ),
        h(
          'div',
          { className: 'hermes-runners-summary' },
          h('span', { 'data-filtered-cohort': true }, `${view.counts.registrations} registrations${search.trim() || filter !== 'All' ? ` · ${rows.length} shown` : ''}`)
        ),
        h(
          'div',
          {
            role: 'status',
            'aria-live': 'polite',
            style: { maxHeight: 100, overflow: 'auto', flexShrink: 0 }
          },
          q.isPending && !sources.length ? 'Loading inventory…' : null,
          ...selected.filter(s => s.enrolled !== false && (
            (s.error && (s.enrolled === true || s.snapshot || /Owner (gateway disconnected|identity mismatch)/.test(s.error))) ||
            ['owner_disconnected', 'owner_conflict'].includes(s.snapshot?.data.state)
          )).map((s, i) =>
            h('p', { key: i, className: 'hermes-runners-note' }, `${['owner_disconnected', 'owner_conflict'].includes(s.snapshot?.data.state) ? 'Owner unavailable · ' : ''}${s.error || STATES[s.snapshot.data.state]} · ${s.route?.connectionId || 'Sources'} / ${s.route?.targetProfile || ''}`))
        ),
        actionError ? h('p', { role: 'alert' }, actionError) : null,
        h('div', { className: 'hermes-runners-filters' }, h('input', {
          ref: searchRef,
          className: 'hermes-runners-search',
          'aria-label': 'Search runners',
          placeholder: 'Search runners…  /',
          value: search,
          onChange: (e) => setSearch(e.target.value)
        }),
        h('select', { 'aria-label': 'Runner state', value: filter, onChange: e => setFilter(e.target.value) },
          ...FILTERS.map(f => h('option', { key: f, value: f }, f)))),
        h(
          'div',
          { className: 'hermes-runners-scroll' },
          !rows.length
            ? h(
                'p',
                { className: 'hermes-runners-note' },
                view.rows.length ? 'No matching registrations' : 'No registrations to display'
              )
            : null,
          ...groups.map((g) =>
            h(
              'section',
              { key: g.key, className: `hermes-runners-group${g.host ? '' : ' hermes-runners-unassigned'}` },
              h(
                'button',
                {
                  type: 'button',
                  className: 'hermes-runners-host',
                  'data-node-row': g.key,
                  'aria-expanded': expandedNodes.has(g.key),
                  onClick: () =>
                    setExpandedNodes((old) => {
                      const n = new Set(old)
                      n.has(g.key) ? n.delete(g.key) : n.add(g.key)
                      return n
                    })
                },
                h('span', null,
                  h('b', null, `${expandedNodes.has(g.key) ? '▾' : '▸'} `, ...highlight(g.name, search)),
                  g.host && search.trim() && search.toLowerCase().trim().split(/\s+/).some(t => `${g.host.os} ${g.host.arch}`.toLowerCase().includes(t))
                    ? h('span', { className: 'hermes-runners-meta' }, ...highlight(`${g.host.os} ${g.host.arch}`, search)) : null,
                  h('span', { className: 'hermes-runners-meta' }, g.registrationCount ? `${g.registrationCount} registrations` : g.runners?.length ? 'No matching registrations' : 'No mapped registrations'),
                  h(RegistrationBar, { counts: g.counts, total: g.registrationCount }),
                  h('span', { className: 'hermes-runners-blocking' },
                    g.reason || (g.maintenance?.active ? `Maintenance: ${g.maintenance.reason || 'Reason not reported'}` : g.health?.reason) || 'No physical node mapping',
                    !g.host && g.reason ? ' · No physical node mapping' : '',
                    g.age ? ` · ${g.age}` : '')
                ),
                g.freshness?.observedAt ? h('small', null, `Probe ${g.freshness.state} · ${ago(g.freshness.observedAt)}`) : null
              ),
              expandedNodes.has(g.key) && g.host ? h('div', { 'data-node-evidence': true, className: 'hermes-runners-note' },
                h('div', null, `${text(g.host.os)} / ${text(g.host.arch)} · CPU: not reported · Memory: not reported`),
                h('div', null, `${g.health.reason} · Probe observed: ${g.freshness.observedAt || 'Not observed'} · Source: ${g.source.route.connectionId} / ${g.source.route.targetProfile}`),
                g.capacity.enforced ? `Enforced limit: ${g.capacity.limit} · ` : 'No enforced limit reported · ',
                `Observed active jobs: ${g.capacity.active ?? 'unknown'}`,
                g.maintenance.active ? h('div', null, `Maintenance: ${g.maintenance.reason || 'Reason unavailable'} · expires ${g.maintenance.expiresAt}`) : null
              ) : null,
              expandedNodes.has(g.key)
                ? g.rows.map((row) =>
                    h(RunnerRow, {
                      key: row.key,
                      row,
                      search,
                      expanded: expanded === row.key,
                      onExpand,
                      transport,
                      onError: setActionError,
                      visible,
                      features: features || row.source.features || { repair_sessions: false },
                      os: ctx.os
                    })
                  )
                : null
            )
          )
        ),
        h(
          'details',
          { 'data-runners-diagnostics': true, style: { maxHeight: 100, overflow: 'auto', flexShrink: 0 } },
          h('summary', { className: 'hermes-runners-muted' }, 'Source diagnostics'),
          h('div', { className: 'hermes-runners-muted' },
            `Collector heartbeat ${ago(selected.map(s => s.snapshot?.meta.observed_at).filter(Boolean).sort()[0])} · Coverage ${view.coverage.filter(c => c.state === 'complete').length}/${view.coverage.length} scopes complete · ${view.complete ? 'current' : 'incomplete / stale'}`),
          h('details', null,
            h('summary', { className: 'hermes-runners-muted' }, 'Source coverage and observation ages'),
            ...selected.map((s, i) => h('div', { key: i, className: 'hermes-runners-note' },
              `${s.route?.connectionId || 'Sources'} / ${s.route?.targetProfile || ''} · checked ${ago(s.snapshot?.meta.observed_at)}`)),
            ...view.coverage.map((c, i) => h('div', { key: `coverage-${i}`, className: 'hermes-runners-note' },
              `${c.scope}: ${c.state} · ${c.enumerated_count ?? '?'} / ${c.provider_total_count ?? '?'} · ${text(c.reason_code)}`))
          ),
          ...view.messages.map(m => h('p', { key: m, className: 'hermes-runners-note' }, m))
        ),
        h(
          'div',
          { className: 'hermes-runners-muted' },
          '↑↓ Move · Enter Details · Shift+F10 Actions · Esc Back · ? Help',
          help ? h('div', { 'data-runners-help': true, role: 'note' },
            '↑↓ Move through visible nodes and runners. Enter toggles a node or runner detail. Right: expand node. Left: collapse node. / or type: search. Shift+F10: runner actions. Escape: help, detail, query, expanded node, then panel.') : null
        )
      )
    )
  )
}

export default {
  id: 'runners',
  name: 'Runners',
  description: 'Owner-routed runner registrations, health, coverage and shared-host capacity.',
  register(ctx) {
    const style = document.createElement('style')
    style.textContent = CSS
    document.head.append(style)
    ctx.onDispose(() => style.remove())
    ctx.register({
      id: 'chip',
      area: STATUSBAR_AREAS.right,
      order: 6,
      render: () => h(Runners, { ctx })
    })
    ctx.register({
      id: 'show-runners',
      area: PALETTE_AREA,
      data: {
        id: 'show-runners',
        label: 'Show runners',
        keywords: ['build', 'registrations', 'hosts'],
        run: () => window.dispatchEvent(new window.Event('hermes-show-runners'))
      }
    })
  }
}

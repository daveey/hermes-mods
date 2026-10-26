// Native Observe surface. No credentials, ambient routing or repair mutations.
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
import { createElement as h, useState, useEffect, useRef } from 'react'

const BAR =
  'inline-flex h-full items-center gap-1.5 px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
const ID_PATTERN = /^[a-z]{2,12}_[0-9a-f]{16,64}$/
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const FILTERS = ['All', 'Needs attention', 'Busy', 'Ready', 'Maintenance']
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
          try {
            let enrollment, legacy = false
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
            sources.set(key, { route, snapshot, ...(legacy ? { error: 'Deployment pending: enrollment unavailable; last-known inventory' } : {}) })
          } catch (e) {
            sources.set(key, { ...prior, route, error: failure(e) })
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
  return { inventory, request, refresh }
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
function rowState(r, source, hostRow, now) {
  const provenance = source.snapshot.meta.provenance?.find((p) => p.source_id === r.id)
  const stale =
    (provenance && (provenance.state !== 'fresh' || Date.parse(provenance.expires_at) <= now)) ||
    !!source.error ||
    !['ready', 'partial'].includes(source.snapshot.data.state) ||
    !fresh(source.snapshot.meta, now) ||
    r.freshness !== 'fresh'
  const maintenance =
    hostRow?.maintenance?.state === 'active' && Date.parse(hostRow.maintenance.expires_at) > now
  const issue =
    stale || ['offline', 'missing', 'unknown'].includes(r.registration) ||
    ['fail', 'warn'].includes(r.readiness)
  const health = stale
    ? 'Stale'
    : r.readiness === 'fail'
      ? 'Unavailable'
      : r.readiness === 'warn'
        ? 'Degraded'
        : r.registration === 'offline'
          ? 'Offline observation'
          : r.registration === 'missing'
            ? 'Missing'
            : r.registration === 'unknown'
              ? 'Unknown'
              : r.activity === 'busy'
                ? 'Busy'
                : r.readiness === 'pass' && r.activity === 'idle'
                  ? 'Ready'
                  : 'Readiness unverified'
  // Offline confirmation belongs to the collector; the base contract lacks its
  // consecutive-observation count. Never invent confirmation from one snapshot.
  const reason = stale
    ? 'Last-known observations; readiness not verified'
    : r.registration === 'offline'
      ? 'Provider reports offline; inspect observation history'
      : r.readiness === 'fail'
        ? 'Required readiness check failed'
        : r.readiness === 'warn'
          ? 'Readiness check warning'
          : r.activity === 'busy'
            ? 'Busy — job not identified'
            : health === 'Ready'
              ? 'Fresh online/idle; readiness checks passed'
              : 'Mandatory readiness evidence unavailable'
  return {
    stale,
    maintenance,
    issue,
    health,
    reason,
    ready: health === 'Ready' && !maintenance,
    tone: stale
      ? 'muted'
      : !maintenance && r.readiness === 'fail'
        ? 'bad'
        : issue
          ? 'warn'
          : health === 'Ready'
            ? 'good'
            : 'muted'
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
        key,
        host: hostRow,
        scope,
        sources: [origin],
        source: origin,
        conflict: false,
        actionable: !source.error,
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
        row.actionable = !row.conflict && !row.source.error
        if (row.conflict) {
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
    busy: list.filter((r) => r.activity === 'busy').length,
    ready: list.filter((r) => r.ready).length,
    unverified: list.filter((r) => r.registration === 'online' && !r.ready).length,
    issues: list.filter((r) => r.issue).length + groups.filter((g) => g.issue).length
  }
  const complete =
    sources.length > 0 &&
    sources.every((s) => s.snapshot && !s.error && fresh(s.snapshot.meta, now)) &&
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
          : list.length
            ? 'good'
            : 'muted'
  }
}
export function projectNodes(sources = [], projectedRows = [], now = Date.now()) {
  const nodes = new Map()
  // Select a usable owner response before recency, with stable route tie-breaking.
  // Envelope time selects the response only; it is never node observation age.
  const ordered = sources.filter(s => s.snapshot).sort((a, b) =>
    Number(!b.error) - Number(!a.error) ||
    (Date.parse(b.snapshot?.meta.observed_at) || 0) - (Date.parse(a.snapshot?.meta.observed_at) || 0) ||
    routeKey(a.route).localeCompare(routeKey(b.route)))
  for (const source of ordered) {
    const snapshot = source.snapshot
    if (!snapshot) continue
    for (const host of snapshot.data.hosts) {
      const key = JSON.stringify([ownerKey(snapshot.meta.owner), host.id])
      if (nodes.has(key)) continue
      const runners = projectedRows.filter(r => r.hostKey === key)
      const unknown = runners.filter(r => r.stale || !['busy', 'idle'].includes(r.activity)).length
      nodes.set(key, {
        key, host, owner: snapshot.meta.owner, source, runners,
        registrationCount: runners.length,
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
          busy: !runners.length || unknown ? null : runners.filter(r => r.activity === 'busy').length,
          idle: !runners.length || unknown ? null : runners.filter(r => r.activity === 'idle').length,
          unknown
        },
        freshness: { state: source.error ? 'stale' : 'unknown', observedAt: null }
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
export function selectNodes(projection, search = '', filter = 'All') {
  const terms = search.toLowerCase().trim().split(/\s+/).filter(Boolean)
  const groups = projection.nodes.flatMap(n => {
    const hostText = [n.host.display_name, n.host.os, n.host.arch].join(' ').toLowerCase()
    const hostMatches = terms.every(t => hostText.includes(t))
    const rows = selectRows(n.runners, hostMatches ? '' : search, filter)
    const nodeMatchesFilter = filter === 'All' ||
      (filter === 'Needs attention' && (n.source.error || n.monitoring !== 'reachable' || n.freshness.state !== 'fresh')) ||
      (filter === 'Maintenance' && n.host.maintenance?.state === 'active')
    return rows.length || (hostMatches && nodeMatchesFilter)
      ? [{ ...n, name: n.host.display_name, rows }] : []
  })
  const unassigned = selectRows(projection.unassigned, search, filter)
  if (unassigned.length) groups.push({ key: 'unmapped', name: 'Unassigned runners', rows: unassigned })
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
        (filter === 'Busy' && r.activity === 'busy') ||
        (filter === 'Ready' && r.ready) ||
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
.hermes-runners-search{width:100%}.hermes-runners-filters button{border-radius:4px;padding:3px 7px;font-size:11px}
.hermes-runners-filters button[aria-pressed=true],.hermes-runners button:hover{background:var(--chrome-action-hover)}
.hermes-runners-scroll{min-height:0;overflow:auto;overscroll-behavior:contain}
.hermes-runners-group{border-top:1px solid var(--ui-stroke-secondary);padding-top:6px;margin-top:6px}
.hermes-runners-host{display:flex;flex-wrap:wrap;text-align:left;width:100%;gap:8px;padding:4px}
.hermes-runners-host small{margin-left:auto}.hermes-runners-rowwrap{display:flex;align-items:start;gap:2px}
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
    enabled: visible && row.actionable && ID_PATTERN.test(row.id),
    retry: false,
    staleTime: 15000
  })
  const d = q.data?.data
  useEffect(() => onData?.(d), [d, onData])
  return h(
    'div',
    { className: 'hermes-runners-detail' },
    h('strong', null, 'Source observations'),
    q.isPending ? h('p', null, 'Loading details…') : null,
    q.error ? h('p', { role: 'alert' }, failure(q.error)) : null,
    h(
      'dl',
      null,
      h('dt', null, 'Labels'),
      h('dd', null, (row.labels || []).map(text).join(', ') || 'None observed'),
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
          ...(d.jobs || []).map((j) =>
            h(
              'p',
              { key: j.id },
              `${text(j.provider_run_id)} · ${text(j.status)} · ${text(j.conclusion)}`
            )
          ),
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
  const job = detail?.jobs?.find((j) => j.status === 'running' && j.assigned_runner_id === row.id)
  const currentJob = row.activity === 'busy' && !row.stale && job ? jobURL(row, job) : null
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
    row.repair === 'active'
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
              'aria-label': `${row.display_name} · ${row.health} · ${row.activity}`,
              'aria-expanded': expanded,
              onClick: () => onExpand(row.key),
              onKeyDown: (e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  onExpand(row.key)
                }
              }
            },
            h(StatusDot, { tone: row.tone }),
            h(
              'span',
              null,
              h('span', { className: 'hermes-runners-name' }, ...highlight(row.display_name, search)),
              h(
                'span',
                { className: 'hermes-runners-meta' },
                `${text(row.scope?.name)} · ${row.provider} · ${row.observed_os} ${row.host?.arch || ''} · ${(row.labels || []).slice(0, 3).map(text).join(', ')}`
              ),
              h(
                'span',
                { className: 'hermes-runners-meta' },
                currentJob ? `Busy — job ${job.provider_job_id}` : row.reason
              )
            ),
            h(
              'span',
              null,
              `${row.maintenance ? 'Maintenance · ' : ''}${row.health} · ${row.activity}`,
              h('span', { className: 'hermes-runners-meta' }, ago(row.observed_at)),
              !['none', 'closed'].includes(row.repair)
                ? h('span', { className: 'hermes-runners-meta' }, `Repair ${row.repair}`)
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
              `Open current job ${job.provider_job_id}`
            )
          : null,
        expanded ? h(Details, { row, transport, visible, onData: setDetail }) : null
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
        ? h(ContextMenuItem, { disabled: true }, `${repairLabel} — not available yet`)
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
        { disabled: true },
        'Maintenance controls unavailable — does not drain jobs'
      )
    )
  )
}

export function Runners({ ctx, features = { repair_sessions: false } }) {
  const [open, setOpen] = useState(false),
    [search, setSearch] = useState(''),
    [filter, setFilter] = useState('All'),
    [scope, setScope] = useState('all'),
    [expanded, setExpanded] = useState(null),
    [expandedNodes, setExpandedNodes] = useState(new Set()),
    [actionError, setActionError] = useState('')
  const visible = useVisible(),
    client = useQueryClient(),
    transportRef = useRef(null),
    trigger = useRef(null),
    panel = useRef(null),
    searchRef = useRef(null)
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
    if (e.defaultPrevented || e.target.closest?.('[role=menu]')) return
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
      setSearch(next)
      searchRef.current?.focus()
      requestAnimationFrame(() => searchRef.current?.setSelectionRange(next.length, next.length))
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
          `Scope: ${view.counts.registrations} registrations · ${view.counts.ready} verified-ready · ${view.counts.unverified} readiness-unverified`,
          search.trim() || filter !== 'All' ? h('div', { 'data-filtered-cohort': true }, `Showing ${groups.filter(g => g.host).length} nodes · ${rows.length} registrations`) : null
        ),
        h(
          'div',
          { className: 'hermes-runners-muted' },
          `Collector heartbeat ${ago(
            selected
              .map((s) => s.snapshot?.meta.observed_at)
              .filter(Boolean)
              .sort()[0]
          )} · Coverage ${view.coverage.filter((c) => c.state === 'complete').length}/${view.coverage.length} scopes complete · ${view.complete ? 'current' : 'incomplete / stale'}`
        ),
        h(
          'details',
          null,
          h(
            'summary',
            { className: 'hermes-runners-muted' },
            'Source coverage and observation ages'
          ),
          ...selected.map((s, i) =>
            h(
              'div',
              { key: i, className: 'hermes-runners-note' },
              `${s.route?.connectionId || 'Sources'} / ${s.route?.targetProfile || ''} · checked ${ago(s.snapshot?.meta.observed_at)}`
            )
          ),
          ...view.coverage.map((c, i) =>
            h(
              'div',
              { key: `coverage-${i}`, className: 'hermes-runners-note' },
              `${c.scope}: ${c.state} · ${c.enumerated_count ?? '?'} / ${c.provider_total_count ?? '?'} · ${text(c.reason_code)}`
            )
          )
        ),
        h(
          'div',
          {
            role: 'status',
            'aria-live': 'polite',
            style: { maxHeight: 100, overflow: 'auto', flexShrink: 0 }
          },
          q.isPending && !sources.length ? 'Loading inventory…' : null,
          ...view.messages.map((m) => h('p', { key: m, className: 'hermes-runners-note' }, m))
        ),
        actionError ? h('p', { role: 'alert' }, actionError) : null,
        h('input', {
          ref: searchRef,
          className: 'hermes-runners-search',
          'aria-label': 'Search runners',
          placeholder: 'Search name, host, scope, OS, architecture, labels…  /',
          value: search,
          onChange: (e) => setSearch(e.target.value)
        }),
        h(
          'div',
          {
            className: 'hermes-runners-filters',
            'aria-label': 'Filter runners'
          },
          ...FILTERS.map((f) =>
            h(
              'button',
              {
                key: f,
                type: 'button',
                'aria-pressed': filter === f,
                onClick: () => setFilter(f)
              },
              f
            )
          )
        ),
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
              { key: g.key, className: 'hermes-runners-group' },
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
                  g.host ? h('span', { className: 'hermes-runners-meta' }, `${text(g.host.os)} ${text(g.host.arch)} · ${g.registrationCount ? `${g.registrationCount} registrations` : 'No mapped runners'}`) : null,
                  g.activity ? h('span', { className: 'hermes-runners-meta' }, `${g.activity.busy == null ? 'Activity unknown' : `${g.activity.busy} busy · ${g.activity.idle} idle`} · Not observed`) : null
                ),
                h('small', null, g.health?.reason || `${g.rows.length} · mapping needed`)
              ),
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
                      features,
                      os: ctx.os
                    })
                  )
                : null
            )
          )
        ),
        h(
          'div',
          { className: 'hermes-runners-muted' },
          '↑↓ Move · Enter Details · Shift+F10 Actions · Esc Back'
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

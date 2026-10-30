/**
 * Session Mentions — @-mention, read, and message sessions on ANY connected gateway.
 *
 * 1. Typeahead. In the composer:
 *      @photo        sessions on every connected gateway whose title matches
 *      @photo-gal    "-", "_", "." act as spaces (the @ token can't hold a space)
 *      @session:     the most recent sessions
 *    Sessions on the active gateway insert `@session:<profile>/<id>`; sessions on another
 *    gateway insert `@session:<profile>@<gateway>/<id>`. This Mac's own connection is named by its
 *    device name (backend config `session_mentions.device_name`, else the hostname), never the
 *    ambiguous `this-device` (every Desktop calls its own Mac that).
 * 2. Referencing. The agent resolves cross-gateway links with the `cross_session` tool
 *    (agent plugin ~/.hermes/plugins/session-mentions); this plugin is its courier.
 * 3. Sending. `/tell @session:… <message>` delivers <message> into that session as a new turn.
 * 4. Agent messaging. Courier loop: drains `cross_session` requests from every connected
 *    gateway that has the agent plugin (GET /api/plugins/session-mentions/outbox, with this Mac's
 *    device name + every gateway name it can reach, so a request for one Mac goes to that Mac),
 *    performs list/read/send on the target gateway's own connection (routed RPCs name the target
 *    profile), posts the result back.
 *
 * 5. Kanban tasks. `@task:` / `@<title words>` / `@t_1a2b` complete tasks from every board on
 *    every gateway, inserting `@task:<board>/<id>` (this gateway) or
 *    `@task:<board>@<gateway>/<id>`. The courier also serves the agent's `cross_task` tool
 *    (task_list / task_read / task_comment over each gateway's kanban REST router).
 *
 * Also repairs the core composer's doubled `@session:` prefix (core's @ popover only splits
 * kind from value for file|folder|url|image|tool|git).
 */

import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'

const PLUGIN_ID = 'session-mentions'
const STALE_MS = 60_000
const MAX_MATCHES = 8
const MAX_RECENT = 15
const COURIER_INTERVAL_MS = 3_000
const UNSUPPORTED_BACKOFF_MS = 5 * 60_000
const ERROR_BACKOFF_MS = 30_000
const HOME_BEAT_MS = 5_000 // server treats the home courier as gone after 20s of silence
const HOME_COURIER_ID = 'home-courier'
const FORWARD_PICKUP_MS = 20_000
const FORWARD_POLL_MS = 1_500
// Core's lease refusal (hermes_cli/active_sessions.py): the turn was never admitted.
const OWNER_REFUSAL_RE = /open in another Hermes window|SESSION_NOT_OWNED|not owned by this runtime/i
const HIDDEN_SOURCES = new Set(['cron', 'kanban', 'tool', 'oneshot', 'subagent'])

// ── small utils ──────────────────────────────────────────────────────────────

export const slug = text =>
  String(text || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

export function ago(seconds, now = Date.now()) {
  if (!seconds) return ''
  const s = Math.max(0, Math.round(now / 1000 - seconds))
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`
  return `${Math.floor(s / (86400 * 30))}mo ago`
}

export function rowLabel(row) {
  const title = (row.title || '').trim()
  if (title) return title
  const preview = (row.preview || '').trim().replace(/\s+/g, ' ')
  return preview ? `${preview.slice(0, 60)}${preview.length > 60 ? '…' : ''}` : `Untitled · ${String(row.id).slice(0, 8)}`
}

const tokenize = text =>
  String(text || '')
    .toLowerCase()
    .split(/[\s\-_.:/]+/)
    .filter(Boolean)

/** 0 = no match; higher is better. Every query token must hit the title (word-prefix beats
 *  substring); preview, gateway name, and id are weaker fallbacks. */
export function score(row, query) {
  const q = tokenize(query)
  if (!q.length) return 0
  const title = (row.title || '').toLowerCase()
  const words = tokenize(row.title)
  const preview = (row.preview || '').toLowerCase()
  const gw = tokenize(row._gwLabel)
  let total = 0
  for (const t of q) {
    if (words.some(w => w.startsWith(t))) total += 3
    else if (t.length >= 3 && title.includes(t)) total += 2
    else if (gw.some(w => w.startsWith(t))) total += 1
    else if (t.length >= 3 && preview.includes(t)) total += 1
    else if (String(row.id).toLowerCase().startsWith(t)) total += 1
    else return 0
  }
  if (title.startsWith(q.join(' '))) total += 2
  return total
}

// ── gateways (Desktop connection registry) ───────────────────────────────────

let connCache = { at: 0, rows: [] }

export function gatewayKeys(conns, device = null, registry = {}) {
  // Prefer the slugged label (`nous-1`); fall back to the id when labels collide. This Mac's own
  // connection (kind local, labelled "This device" on EVERY Mac) is keyed by its device name
  // (`macbook`) once known: a `@this-device` ref handed to an agent on a shared gateway names
  // whichever Desktop happens to drain it.
  // `registry` maps connection id -> that gateway's hermes-hub registry name (its /ping `gateway`).
  // Hermes writes cross-gateway links with it (`@task:default@hub/t_..` for the `hub-ec2`
  // connection), so it is an extra alias; findGateway still prefers exact id/key/label matches.
  const name = c => (c.kind === 'local' && device?.device ? device.device : slug(c.label))
  const counts = new Map()
  for (const c of conns) counts.set(name(c), (counts.get(name(c)) || 0) + 1)
  return conns.map(c => {
    const key = name(c) && counts.get(name(c)) === 1 ? name(c) : c.id
    const own = slug(registry?.[c.id])
    if (c.kind === 'local' && device?.device) {
      return { ...c, key, device: device.device, aliases: [...new Set([...(device.aliases || []), ...(own ? [own] : [])])] }
    }
    return own ? { ...c, key, aliases: [own] } : { ...c, key }
  })
}

// Each gateway's registry name, from its own session-mentions /ping. A gateway without the agent
// plugin (or the hub client) reports none; a failed ping keeps the last known name.
const registryCache = new Map() // connection id -> { at, ttl, name }
const REGISTRY_TTL_MS = 10 * STALE_MS
const REGISTRY_RETRY_MS = 5 * STALE_MS

function noteRegistryName(connectionId, ping) {
  if (!ping) return
  registryCache.set(connectionId, { at: Date.now(), ttl: REGISTRY_TTL_MS, name: slug(ping.gateway) })
}

async function registryNames(conns) {
  await Promise.all(
    conns.map(async c => {
      const hit = registryCache.get(c.id)
      if (hit && Date.now() - hit.at < hit.ttl) return
      const ping = await api(c.id, `/api/plugins/${PLUGIN_ID}/ping`, { timeoutMs: 3_000 }).catch(() => null)
      if (ping) noteRegistryName(c.id, ping)
      else registryCache.set(c.id, { at: Date.now(), ttl: REGISTRY_RETRY_MS, name: hit?.name || '' })
    })
  )
  return Object.fromEntries([...registryCache].filter(([, v]) => v.name).map(([id, v]) => [id, v.name]))
}

// This Mac's courier name, from its own backend's /ping (config `session_mentions.device_name`,
// else the hostname). null until known, or on a backend that predates targeting.
let deviceCache = { at: 0, info: null }

async function deviceInfo(conns) {
  if (deviceCache.info && Date.now() - deviceCache.at < 10 * STALE_MS) return deviceCache.info
  const home = conns.find(c => c.kind === 'local')
  if (!home) return null
  const ping = await api(home.id, `/api/plugins/${PLUGIN_ID}/ping`, { timeoutMs: 8_000 }).catch(() => null)
  noteRegistryName(home.id, ping)
  let device = slug(ping?.device)
  let aliases = (ping?.aliases || []).map(slug).filter(Boolean)
  if (!device && ping) {
    // Backend still serving the pre-targeting routes (mounted once per process): the hostname is
    // always one of the device's aliases, so refs minted now keep resolving after its restart.
    const stats = await api(home.id, '/api/system/stats', { timeoutMs: 8_000 }).catch(() => null)
    device = slug(String(stats?.hostname || '').split('.')[0])
  }
  deviceCache = { at: Date.now(), info: device ? { device, aliases: [...new Set([device, ...aliases])] } : deviceCache.info }
  return deviceCache.info
}

/** Every name this Desktop can resolve to a connection (sent as `reach` when draining). */
export function reachNames(conns) {
  const out = new Set()
  for (const c of conns) {
    for (const n of [c.key, c.id, slug(c.label), ...(c.aliases || [])]) {
      const s = slug(n)
      if (s && s !== 'this-device' && s !== 'local') out.add(s)
    }
  }
  return [...out].sort()
}

const FALLBACK_CONNS = [{ id: 'local', label: 'This device', kind: 'local', primary: true }]

async function connections(force = false) {
  if (!force && Date.now() - connCache.at < STALE_MS && connCache.rows.length) return connCache.rows
  try {
    const rows = typeof host.connections === 'function' ? await host.connections() : []
    const list = (Array.isArray(rows) ? rows : [])
      .filter(r => r && r.id)
      .map(r => ({ id: String(r.id), label: String(r.label || r.name || r.id), kind: String(r.kind || ''), primary: !!r.primary }))
    const base = list.length ? list : FALLBACK_CONNS
    const device = await deviceInfo(base).catch(() => null)
    const registry = await registryNames(base).catch(() => ({}))
    connCache = { at: Date.now(), rows: gatewayKeys(base, device, registry) }
  } catch {
    if (!connCache.rows.length) connCache = { at: Date.now(), rows: gatewayKeys(FALLBACK_CONNS) }
  }
  return connCache.rows
}

function activeConnectionId(conns = connCache.rows) {
  let id = null
  try {
    id = host.state.connectionId?.get?.() || null
  } catch {
    /* optional */
  }
  return id || conns.find(c => c.primary)?.id || conns[0]?.id || 'local'
}

export function findGateway(conns, name) {
  const n = String(name || '').trim().toLowerCase()
  if (!n) return null
  return (
    conns.find(c => c.id.toLowerCase() === n || c.key.toLowerCase() === n || slug(c.label) === slug(n)) ||
    conns.find(c => (c.aliases || []).includes(slug(n))) ||
    null
  )
}

// ── session refs ─────────────────────────────────────────────────────────────

/** `@session:<profile>/<id>` relative to `relativeTo` (a connection id); qualified with
 *  `@<gateway key>` when the session lives on another gateway. */
export function sessionRef({ connectionId, profile, sessionId }, relativeTo, conns) {
  // Profile names never contain `@`; a row labelled `default@mac-mini` (a qualified
  // ref that leaked into Desktop routing) must not stack a second `@mac-mini`.
  const p = String(profile || '').split('@')[0].trim() || 'default'
  if (!connectionId || connectionId === relativeTo) return `@session:${p}/${sessionId}`
  const gw = conns.find(c => c.id === connectionId)
  return `@session:${p}@${gw ? gw.key : connectionId}/${sessionId}`
}

/** Parse `@session:<profile>[@<gateway>]/<id>` (optionally backtick/quote fenced, optionally
 *  with a doubled `@session:` prefix), `<gateway>:<id>`, or a bare id. Unqualified refs belong
 *  to `origin`. Returns {connectionId, profile, sessionId} or {error}. */
export function parseRef(raw, origin, conns) {
  let text = String(raw || '').trim()
  text = text.replace(/^@session:/, '').replace(/^[`"']|[`"']$/g, '').replace(/^@session:/, '').trim()
  if (!text) return { error: 'empty session reference' }

  let gateway = null
  let profile
  let sessionId

  const slash = text.indexOf('/')
  if (slash !== -1) {
    let head = text.slice(0, slash)
    sessionId = text.slice(slash + 1).trim()
    const at = head.indexOf('@')
    if (at !== -1) {
      // `default@mac-mini@mac-mini` (stacked qualifier) still names one gateway.
      gateway = head.slice(head.lastIndexOf('@') + 1)
      head = head.slice(0, at)
    }
    profile = head || 'default'
  } else {
    const colon = text.indexOf(':')
    if (colon !== -1) {
      gateway = text.slice(0, colon)
      sessionId = text.slice(colon + 1).trim()
    } else {
      sessionId = text
    }
    profile = undefined
  }

  if (!sessionId) return { error: `no session id in "${raw}"` }
  let connectionId = origin
  if (gateway) {
    const gw = findGateway(conns, gateway)
    if (!gw) return { error: `unknown gateway "${gateway}" — connected: ${conns.map(c => c.key).join(', ')}` }
    connectionId = gw.id
  }
  return { connectionId, profile, sessionId }
}

// ── REST / RPC per gateway ───────────────────────────────────────────────────

function api(connectionId, path, opts = {}) {
  const bridge = typeof window !== 'undefined' ? window.hermesDesktop?.api : null
  if (!bridge) return Promise.reject(new Error('Hermes desktop bridge unavailable'))
  return bridge({ path, method: opts.method, body: opts.body, connectionId: connectionId || 'local', timeoutMs: opts.timeoutMs || 30_000 })
}

const enc = encodeURIComponent

// Per-connection session rows for typeahead/list.
const rowCache = new Map() // connectionId -> {at, rows}
const rowInflight = new Map()
const rowErrors = new Map() // connectionId -> last fetch error message

// ── change notification (core re-asks an open @ popover) ─────────────────────

const listeners = new Set()
let notifyTimer = null

/** Core's `subscribe` hook: called when cached rows or fetch errors change, so a popover
 *  already open on `@the` re-asks instead of keeping what it had on the last keystroke. */
export function subscribeRows(onChange) {
  listeners.add(onChange)
  return () => listeners.delete(onChange)
}

function notifyChanged() {
  if (notifyTimer) return
  notifyTimer = setTimeout(() => {
    notifyTimer = null
    for (const fn of [...listeners]) {
      try {
        fn()
      } catch {
        /* a broken listener must not stop the others */
      }
    }
  }, 30)
}

// ── warm start (rows survive a reload; the first popover isn't empty) ────────

const WARM_KEY = 'hermes.session-mentions.warm.v1'
const WARM_MAX_ROWS = 3_000 // per connection and kind; slim rows are ~200 B
const WARM_SAVE_MS = 1_000
let warmTimer = null

const safeStorage = () => {
  try {
    return globalThis.localStorage || null
  } catch {
    return null
  }
}

/** Only what typeahead needs: score() reads title/preview/_gwLabel/id, sessionItems the rest. */
export function slimSessionRow(r) {
  return {
    id: r.id,
    title: r.title || '',
    preview: String(r.preview || '').slice(0, 120),
    profile: r.profile || '',
    last_active: r.last_active || 0,
    started_at: r.started_at || 0,
    message_count: r.message_count || 0,
    _conn: r._conn,
    _gwLabel: r._gwLabel,
    _gwKey: r._gwKey
  }
}

/** Serialize both caches. Exported for tests. */
export function saveWarm(storage = safeStorage()) {
  if (!storage) return false
  const pack = (cache, slim) => {
    const out = {}
    for (const [id, { rows }] of cache) if (rows?.length) out[id] = rows.slice(0, WARM_MAX_ROWS).map(slim)
    return out
  }
  try {
    storage.setItem(WARM_KEY, JSON.stringify({ v: 1, at: Date.now(), sessions: pack(rowCache, slimSessionRow), tasks: pack(taskCache, r => r) }))
    return true
  } catch {
    return false // quota or private mode: warm start is a convenience
  }
}

/** Seed both caches from storage as already-stale, so the next provide() refreshes them. */
export function loadWarm(storage = safeStorage()) {
  if (!storage) return 0
  let data
  try {
    data = JSON.parse(storage.getItem(WARM_KEY) || 'null')
  } catch {
    return 0
  }
  if (!data || data.v !== 1) return 0
  let n = 0
  for (const [cache, rows] of [[rowCache, data.sessions], [taskCache, data.tasks]]) {
    for (const [id, list] of Object.entries(rows || {})) {
      if (!Array.isArray(list) || cache.has(id)) continue
      cache.set(id, { at: 0, rows: list, warm: true })
      n += list.length
    }
  }
  return n
}

function scheduleWarmSave() {
  if (warmTimer) return
  warmTimer = setTimeout(() => {
    warmTimer = null
    saveWarm()
  }, WARM_SAVE_MS)
}

/** Gateways a popover should mention: still loading with nothing cached, or failing. */
export function fetchStatus(cache, inflight, errors, conns) {
  const label = id => conns.find(c => c.id === id)?.label || id
  const loading = conns.filter(c => inflight.has(c.id) && !cache.get(c.id)?.rows?.length).map(c => c.label)
  const failed = [...errors].map(([id, error]) => ({ label: label(id), error }))
  return { loading, failed }
}

/** Stamp each row with the gateway that OWNS it. Desktop's `hermes:api` answers a
 *  connection-pinned `/api/profiles/sessions?profile=all` with the ALL-gateways
 *  aggregate once that gateway is pooled: every row from a registry gateway carries
 *  `connection_id`, rows from the primary (base) aggregate carry none. Trusting the
 *  requesting connection instead labelled every local session as living on each
 *  remote gateway too (triplicated typeahead, `@session:…@nous-1/<id>` refs that 404). */
export function tagRows(rows, requested, conns = []) {
  const primary = conns.find(c => c.primary)?.id || 'local'
  return (Array.isArray(rows) ? rows : [])
    .filter(r => r && r.id && !HIDDEN_SOURCES.has(String(r.source || '').toLowerCase()) && !r.hidden)
    .map(r => {
      const ownerId = String(r.connection_id || '').trim() || primary
      const owner = conns.find(c => c.id === ownerId) || (ownerId === requested.id ? requested : null) || { id: ownerId, label: ownerId, key: ownerId }
      return { ...r, _conn: owner.id, _gwLabel: owner.label, _gwKey: owner.key || owner.id }
    })
}

/** One row per (gateway, profile, id): the same aggregate comes back from every pinned request. */
export function dedupeRows(rows) {
  const seen = new Set()
  const out = []
  for (const r of rows) {
    const k = `${r._conn}|${r.profile || 'default'}|${r.id}`
    if (seen.has(k)) continue
    seen.add(k)
    out.push(r)
  }
  return out
}

const SESSION_PAGE = 500 // the endpoint's max page size
const MAX_SESSION_ROWS = 5_000

/** Every user-facing session, not just the most recent page. Hidden sources (kanban workers,
 *  oneshots, …) are excluded SERVER-side: they were ~93% of each page, so a 300-row page held
 *  only ~20 real chats and a week-old one could not be @-mentioned at all. */
export async function fetchAllSessionPages(connectionId, request = api) {
  const out = []
  const exclude = [...HIDDEN_SOURCES].join(',')
  for (let offset = 0; offset < MAX_SESSION_ROWS; offset += SESSION_PAGE) {
    let res
    try {
      res = await request(
        connectionId,
        `/api/profiles/sessions?limit=${SESSION_PAGE}&offset=${offset}&order=recent&profile=all&min_messages=1&archived=exclude&exclude_sources=${exclude}`
      )
    } catch (error) {
      if (!out.length) throw error
      break // keep the pages we have; the next refresh retries
    }
    const page = Array.isArray(res?.sessions) ? res.sessions : []
    out.push(...page)
    const total = Number(res?.total)
    if (page.length < SESSION_PAGE || (Number.isFinite(total) && offset + page.length >= total)) break
  }
  return out
}

function fetchRows(conn, force = false) {
  const cached = rowCache.get(conn.id)
  if (!force && cached && Date.now() - cached.at < STALE_MS) return Promise.resolve(cached.rows)
  if (rowInflight.has(conn.id)) return rowInflight.get(conn.id)
  const p = fetchAllSessionPages(conn.id)
    .then(sessions => {
      const rows = tagRows(sessions, conn, connCache.rows)
      rowCache.set(conn.id, { at: Date.now(), rows })
      rowErrors.delete(conn.id)
      notifyChanged()
      scheduleWarmSave()
      return rows
    })
    .catch(error => {
      // Keep stale rows; retry after a short backoff.
      rowErrors.set(conn.id, String(error?.message || error).slice(0, 300))
      const old = rowCache.get(conn.id)
      rowCache.set(conn.id, { at: Date.now() - STALE_MS + 10_000, rows: old?.rows || [] })
      notifyChanged()
      return old?.rows || []
    })
    .finally(() => rowInflight.delete(conn.id))
  rowInflight.set(conn.id, p)
  notifyChanged() // a cold gateway's "Loading…" row appears
  return p
}

async function allRows(force = false) {
  const conns = await connections(force)
  const lists = await Promise.all(conns.map(c => fetchRows(c, force)))
  return dedupeRows(lists.flat())
}

function cachedRows() {
  const out = []
  for (const { rows } of rowCache.values()) out.push(...rows)
  return dedupeRows(out)
}

export const CHANGE_DEBOUNCE_MS = 400
export const CHANGE_REFRESH_MIN_MS = 30_000

/** Delay before the trailing refresh a sessions.changed event arms: the debounce, stretched so
 *  forced refreshes are at least CHANGE_REFRESH_MIN_MS apart. */
export function changeRefreshDelay(now, lastForcedAt, minGap = CHANGE_REFRESH_MIN_MS, debounceMs = CHANGE_DEBOUNCE_MS) {
  return Math.max(debounceMs, lastForcedAt + minGap - now)
}

// Set by register(): refreshes the active connection now if a sessions.changed is pending.
let flushPendingChange = () => {}

function refreshStale() {
  void connections().then(conns => conns.forEach(c => void fetchRows(c)))
}

async function routeFor(connectionId, profile) {
  if (typeof host.profileRoutes !== 'function') throw new Error('this Desktop build cannot route to other gateways')
  const routes = (await host.profileRoutes()) || []
  const onConn = routes.filter(r => r && r.connectionId === connectionId)
  const p = profile || 'default'
  return onConn.find(r => r.targetProfile === p) || null
}

async function readTranscript({ connectionId, profile, sessionId }, limit = 40) {
  const q = `limit=${Math.max(1, Math.min(200, limit))}&order=latest${profile ? `&profile=${enc(profile)}` : ''}`
  const [meta, page] = await Promise.all([
    api(connectionId, `/api/sessions/${enc(sessionId)}${profile ? `?profile=${enc(profile)}` : ''}`).catch(() => null),
    api(connectionId, `/api/sessions/${enc(sessionId)}/messages?${q}`)
  ])
  const messages = (Array.isArray(page?.messages) ? page.messages : [])
    .filter(m => (m.role === 'user' || m.role === 'assistant') && typeof m.content === 'string' && m.content.trim())
    .map(m => ({ id: m.id, role: m.role, at: m.timestamp, content: m.content.length > 4000 ? `${m.content.slice(0, 4000)}…[truncated]` : m.content }))
  return { title: meta?.title || '', profile: page?.profile || profile || 'default', session_id: page?.session_id || sessionId, messages }
}

async function sessionTitle(target) {
  const meta = await api(target.connectionId, `/api/sessions/${enc(target.sessionId)}${target.profile ? `?profile=${enc(target.profile)}` : ''}`).catch(() => null)
  return meta?.title || ''
}

/** Deliver `text` as a new user turn into `target`. Optionally wait for the reply. */
export async function deliver(target, text, waitSeconds = 0) {
  // The primary socket already owns open Desktop chats. A pooled profile route may
  // spawn another serve process over the SAME store: resume there is not attachment.
  const primaryConnection = async () => {
    const id = host.state.connectionId?.get?.()
    if (id) return id
    // Null denotes the registry primary, NOT necessarily this device.
    const rows = typeof host.connections === 'function' ? await host.connections() : []
    const primaries = (Array.isArray(rows) ? rows : []).filter(r => r.primary)
    return primaries.length === 1 ? primaries[0].id : null
  }
  const primaryId = await primaryConnection()
  const primaryProfile = host.state.profile?.get?.()
  const primary = typeof host.request === 'function' && primaryId === target.connectionId && primaryProfile === (target.profile || 'default')
  const route = primary ? null : await routeFor(target.connectionId, target.profile)
  if (!primary && !route) throw new Error(`no exact route to ${target.profile || 'default'} on ${target.connectionId}`)
  const request = async (method, params, timeout) => {
    if (primary) {
      if (await primaryConnection() !== primaryId || host.state.profile?.get?.() !== primaryProfile) throw new Error('Gateway changed during delivery; no automatic retry')
      return host.request(method, { ...params, profile: target.profile || 'default' }, timeout)
    }
    // A cloud gateway serves every profile from one backend: without `profile` the RPC lands in
    // ITS launch (default) profile — session.resume found nothing in default's state.db and the
    // turn ran there (stray root session). Name the backend profile explicitly, like the primary path.
    return host.requestProfile(route, method, { ...params, profile: route.targetProfile || route.profile || target.profile || 'default' }, timeout)
  }
  const release = !primary && typeof host.retainProfile === 'function' ? await host.retainProfile(route) : null
  try {
    const resumed = await request('session.resume', { session_id: target.sessionId, omit_messages: true }, 60_000)
    const live = resumed?.session_id
    if (!live) throw new Error(`could not open session ${target.sessionId} on that gateway`)
    let sub
    try {
      sub = await request('prompt.submit', { session_id: live, text, queued: true }, 60_000)
    } catch (error) {
      const msg = String(error?.message || error)
      // A lease refusal happens before the turn is admitted: definitely not delivered.
      if (OWNER_REFUSAL_RE.test(msg)) {
        return { ok: false, delivered: false, status: 'refused', retry_safe: true, live_session_id: live, error: msg, note: 'Nothing was delivered: another Hermes backend holds that chat open. Safe to retry.' }
      }
      // The server may have accepted the turn before the socket/response failed.
      // Never try another route or submit again on an uncertain acknowledgement.
      return { ok: false, delivered: null, status: 'unknown', retry_safe: false, live_session_id: live, error: String(error?.message || error), note: 'Delivery outcome unknown. Inspect the target transcript; do not resend automatically.' }
    }
    const out = { ok: ['streaming', 'queued'].includes(sub?.status), delivered: sub?.status === 'streaming', queued: sub?.status === 'queued', status: sub?.status || 'unknown', live_session_id: live, user_row_id: sub?.user_row_id || null, retry_safe: false }
    if (!out.ok) return { ...out, error: `Unexpected submit status: ${out.status}; inspect the target before retrying` }
    if (!waitSeconds) return out

    const after = Number(sub?.user_row_id || 0)
    if (!after) return { ...out, reply: null, note: 'Accepted, but this backend supplied no user row for reply correlation. Inspect the target transcript; do not resend.' }
    const deadline = Date.now() + Math.min(600, Math.max(0, waitSeconds)) * 1000
    while (Date.now() < deadline) {
      await sleep(3000)
      const st = await request('session.resume', { session_id: target.sessionId, omit_messages: true }, 30_000).catch(() => null)
      if (!st || st.running) continue
      const tr = await readTranscript(target, 200).catch(() => null)
      const messages = [...(tr?.messages || [])].sort((a, b) => Number(a.id) - Number(b.id))
      // Anchor to OUR user row, not the previous turn's reply or a later user's turn.
      const anchor = messages.findIndex(m => m.role === 'user' && Number(m.id) === after)
      if (anchor < 0) continue
      const subsequent = messages.slice(anchor + 1)
      const nextUser = subsequent.findIndex(m => m.role === 'user')
      const replies = (nextUser < 0 ? subsequent : subsequent.slice(0, nextUser)).filter(m => m.role === 'assistant')
      if (replies.length) return { ...out, delivered: true, queued: false, status: 'replied', reply: replies[replies.length - 1].content }
    }
    return { ...out, reply: null, note: `no correlated reply within ${waitSeconds}s — do not resend automatically` }
  } finally {
    if (typeof release === 'function') release()
  }
}

// ── kanban tasks on every gateway ────────────────────────────────────────────

const TASK_STALE_MS = 60_000
const MAX_TASK_MATCHES = 4 // plain `@query`: tasks share the popover with sessions
const MAX_TASK_SCOPED = 12 // `@task:query`
const LIVE_TASK_STATUSES = new Set(['running', 'ready', 'blocked', 'review', 'triage', 'todo', 'scheduled'])

/** `@task:<board>/<id>` relative to `relativeTo`; `@task:<board>@<gateway>/<id>` elsewhere. */
export function taskRef({ connectionId, board, taskId }, relativeTo, conns) {
  const b = String(board || '').split('@')[0].trim() || 'default'
  if (!connectionId || connectionId === relativeTo) return `@task:${b}/${taskId}`
  const gw = conns.find(c => c.id === connectionId)
  return `@task:${b}@${gw ? gw.key : connectionId}/${taskId}`
}

/** Parse `@task:<board>[@<gateway>]/<id>`, `<gateway>:<id>` or a bare id. Unqualified refs
 *  belong to `origin`; `board` is undefined when the ref doesn't name one. `anyGateway` is set
 *  for a bare id, which may live on any gateway. */
export function parseTaskRef(raw, origin, conns) {
  const unfence = s => s.trim().replace(/^[`"']|[`"']$/g, '').trim()
  const text = unfence(unfence(String(raw || '')).replace(/^@task:/, ''))
  if (!text) return { error: 'empty task reference' }
  let gateway = null
  let board
  let taskId
  const slash = text.lastIndexOf('/')
  if (slash !== -1) {
    let head = text.slice(0, slash)
    taskId = text.slice(slash + 1).trim()
    const at = head.indexOf('@')
    if (at !== -1) {
      gateway = head.slice(head.lastIndexOf('@') + 1)
      head = head.slice(0, at)
    }
    board = head || undefined
  } else {
    const colon = text.indexOf(':')
    if (colon !== -1) {
      gateway = text.slice(0, colon)
      taskId = text.slice(colon + 1).trim()
    } else {
      taskId = text
    }
  }
  if (!taskId) return { error: `no task id in "${raw}"` }
  if (!/^t_[0-9a-z]+$/i.test(taskId)) return { error: `"${taskId}" is not a kanban task id (t_…)` }
  let connectionId = origin
  if (gateway) {
    const gw = findGateway(conns, gateway)
    if (!gw) return { error: `unknown gateway "${gateway}" — connected: ${conns.map(c => c.key).join(', ')}` }
    connectionId = gw.id
  }
  return { connectionId, board, taskId, anyGateway: !gateway && !board }
}

const taskActivity = t => Number(t.completed_at || t.started_at || t.created_at || 0)

/** Slim board rows to what typeahead/list need (a busy board is ~1 MB). */
export function slimTasks(board, columns, conn) {
  const out = []
  for (const col of Array.isArray(columns) ? columns : []) {
    for (const t of Array.isArray(col?.tasks) ? col.tasks : []) {
      if (!t?.id || t.status === 'archived') continue
      out.push({
        id: t.id,
        title: t.title || '',
        status: t.status || col.name || '',
        assignee: t.assignee || '',
        priority: t.priority || 0,
        created_at: t.created_at || 0,
        started_at: t.started_at || 0,
        completed_at: t.completed_at || 0,
        board,
        _conn: conn.id,
        _gwLabel: conn.label,
        _gwKey: conn.key || conn.id
      })
    }
  }
  return out
}

const taskCache = new Map() // connectionId -> {at, rows}
const taskInflight = new Map()
const taskErrors = new Map()

function fetchTasks(conn, force = false) {
  const cached = taskCache.get(conn.id)
  if (!force && cached && Date.now() - cached.at < TASK_STALE_MS) return Promise.resolve(cached.rows)
  if (taskInflight.has(conn.id)) return taskInflight.get(conn.id)
  const p = (async () => {
    const res = await api(conn.id, '/api/plugins/kanban/boards', { timeoutMs: 15_000 })
    const boards = (Array.isArray(res?.boards) ? res.boards : []).filter(b => b?.slug && (b.total == null || b.total > 0))
    const lists = await Promise.all(
      boards.map(b =>
        api(conn.id, `/api/plugins/kanban/board?board=${enc(b.slug)}`, { timeoutMs: 20_000 })
          .then(board => slimTasks(b.slug, board?.columns, conn))
          .catch(() => [])
      )
    )
    return lists.flat()
  })()
    .then(rows => {
      taskCache.set(conn.id, { at: Date.now(), rows })
      taskErrors.delete(conn.id)
      notifyChanged()
      scheduleWarmSave()
      return rows
    })
    .catch(error => {
      taskErrors.set(conn.id, String(error?.message || error).slice(0, 300))
      const old = taskCache.get(conn.id)
      taskCache.set(conn.id, { at: Date.now() - TASK_STALE_MS + 15_000, rows: old?.rows || [] })
      notifyChanged()
      return old?.rows || []
    })
    .finally(() => taskInflight.delete(conn.id))
  taskInflight.set(conn.id, p)
  notifyChanged()
  return p
}

async function allTasks(force = false) {
  const conns = await connections(force)
  return (await Promise.all(conns.map(c => fetchTasks(c, force)))).flat()
}

function cachedTasks() {
  const out = []
  for (const { rows } of taskCache.values()) out.push(...rows)
  return out
}

function refreshStaleTasks() {
  void connections().then(conns => conns.forEach(c => void fetchTasks(c)))
}

/** Title-first match score for a task row; `loose` also matches assignee, board, status, gateway. */
export function taskScore(row, query, { loose = false } = {}) {
  const q = String(query || '').trim()
  if (/^t_[0-9a-f]*$/i.test(q)) return String(row.id).toLowerCase().startsWith(q.toLowerCase()) ? 10 : 0
  return score({ title: row.title, preview: loose ? `${row.assignee} ${row.board} ${row.status}` : '', _gwLabel: loose ? row._gwLabel : '', id: row.id }, q)
}

export function taskItems(rows, rawQuery, { active = 'local', conns = [], now = Date.now(), status = null } = {}) {
  const query = String(rawQuery || '')
  const lower = query.toLowerCase()
  const items = []
  if (lower.length >= 2 && 'task'.startsWith(lower)) {
    items.push({ insert: '@task:', display: '@task:', meta: 'Mention a kanban task (any gateway)', icon: 'simple' })
  }
  const scoped = lower.startsWith('task:')
  const q = (scoped ? query.slice('task:'.length) : query).trim()
  const idQuery = /^t_[0-9a-f]*$/i.test(q)
  if (!scoped && !idQuery && q.length < 3) return items

  const live = t => (LIVE_TASK_STATUSES.has(t.status) ? 1 : 0)
  const recency = (a, b) => live(b) - live(a) || taskActivity(b) - taskActivity(a)
  const picked = !q
    ? [...rows].sort(recency).slice(0, MAX_TASK_SCOPED)
    : rows
        .map(row => ({ row, s: taskScore(row, q, { loose: scoped }) }))
        .filter(x => x.s > 0)
        .sort((a, b) => b.s - a.s || recency(a.row, b.row))
        .slice(0, scoped || idQuery ? MAX_TASK_SCOPED : MAX_TASK_MATCHES)
        .map(x => x.row)

  const multi = new Set(rows.map(r => r._conn)).size > 1 || conns.length > 1
  for (const row of picked) {
    const bits = [`Task · ${row.status}`]
    if (multi) bits.push(row._conn === active ? `${row._gwLabel} (this gateway)` : row._gwLabel)
    if (row.board && row.board !== 'default') bits.push(`board ${row.board}`)
    if (row.assignee) bits.push(`@${row.assignee}`)
    bits.push(ago(taskActivity(row), now))
    items.push({
      insert: taskRef({ connectionId: row._conn, board: row.board, taskId: row.id }, active, conns),
      display: `${row.title || '(untitled)'} · ${row.id}`,
      meta: bits.filter(Boolean).join(' · '),
      icon: 'simple'
    })
  }
  items.push(...statusItems(status, 'tasks', '@task:'))
  return items
}

const clip = (s, n) => {
  const t = String(s ?? '')
  return t.length > n ? `${t.slice(0, n)}…[truncated]` : t
}

/** GET a task on its gateway: the named board first, then every other board there. */
async function fetchTask(connectionId, taskId, board) {
  const is404 = e => /\b404\b|not found/i.test(String(e?.message || e))
  const one = b => api(connectionId, `/api/plugins/kanban/tasks/${enc(taskId)}${b ? `?board=${enc(b)}` : ''}`, { timeoutMs: 15_000 })
  const tried = new Set()
  for (const b of [board, undefined]) {
    if (tried.has(b || '')) continue
    tried.add(b || '')
    try {
      return { data: await one(b), board: b || '' }
    } catch (e) {
      if (!is404(e)) throw e
    }
  }
  // A failed board listing means the other boards were never searched: that is "unknown", not "missing".
  const res = await api(connectionId, '/api/plugins/kanban/boards', { timeoutMs: 15_000 }).catch(e => {
    throw new Error(`board list unavailable after a miss on ${board || 'default'}: ${e?.message || e}`)
  })
  for (const b of (res?.boards || []).map(x => x?.slug).filter(Boolean)) {
    if (tried.has(b)) continue
    try {
      return { data: await one(b), board: b }
    } catch (e) {
      if (!is404(e)) throw e
    }
  }
  return null
}

export function summarizeTask(data, limit = 10) {
  const t = data?.task || {}
  return {
    id: t.id,
    title: t.title,
    status: t.status,
    assignee: t.assignee || null,
    priority: t.priority,
    created_by: t.created_by || null,
    created_at: t.created_at ? new Date(t.created_at * 1000).toISOString() : null,
    started_at: t.started_at ? new Date(t.started_at * 1000).toISOString() : null,
    completed_at: t.completed_at ? new Date(t.completed_at * 1000).toISOString() : null,
    block_kind: t.block_kind || null,
    last_failure_error: t.last_failure_error ? clip(t.last_failure_error, 1500) : null,
    session_id: t.session_id || null,
    workspace: t.workspace_path || null,
    body: clip(t.body, 6000),
    latest_summary: clip(t.latest_summary || t.result, 4000),
    parents: data?.links?.parents || [],
    children: data?.links?.children || [],
    link_tasks: (data?.link_tasks || []).map(x => ({ id: x.id, title: x.title, status: x.status })),
    runs: (data?.runs || []).slice(-5).map(r => ({ id: r.id, profile: r.profile, outcome: r.outcome || r.status, summary: clip(r.summary || r.error, 800) })),
    comments: (data?.comments || []).slice(-limit).map(c => ({ author: c.author, at: c.created_at ? new Date(c.created_at * 1000).toISOString() : null, body: clip(c.body, 2000) })),
    events: (data?.events || []).filter(e => e.kind !== 'heartbeat').slice(-limit).map(e => ({ kind: e.kind, at: e.created_at ? new Date(e.created_at * 1000).toISOString() : null }))
  }
}

/** Resolve a task ref to {target, found:{data, board}}; a bare id is searched on every gateway
 *  (origin first). */
async function resolveTask(rawTarget, origin, conns) {
  const target = parseTaskRef(rawTarget, origin, conns)
  if (target.error) return { error: target.error }
  const order = target.anyGateway ? [origin, ...conns.map(c => c.id).filter(id => id !== origin)] : [target.connectionId]
  const gwName = id => conns.find(c => c.id === id)?.key || id
  // A gateway that answered with an error (503 gateway_not_ready while it drains for a restart, a
  // timeout) was never searched. Reporting that as "not found" made agents conclude a live card was
  // deleted, so name the failure and say it is retryable.
  const unreachable = []
  for (const connectionId of order) {
    let found = null
    try {
      found = await fetchTask(connectionId, target.taskId, target.board)
    } catch (e) {
      unreachable.push(`${gwName(connectionId)}: ${clip(e?.message || e, 300)}`)
      continue
    }
    if (found) return { target: { ...target, connectionId, board: found.board || target.board }, found }
  }
  if (unreachable.length) {
    return { error: `could not read task ${target.taskId}: lookup failed on ${unreachable.join('; ')}. ` +
      'This does not mean the task is missing; the gateway was unavailable (e.g. draining for a restart). Retry in a few minutes.',
    retryable: true }
  }
  return { error: `task ${target.taskId} not found${target.anyGateway ? ' on any connected gateway' : ` on ${gwName(target.connectionId)}`}` }
}

async function performTaskRequest(req, origin, conns) {
  const args = req?.args || {}
  const action = req.action

  if (action === 'task_list') {
    let rows = await allTasks(true)
    if (args.gateway) {
      const gw = findGateway(conns, args.gateway)
      if (!gw) return { ok: false, error: `unknown gateway "${args.gateway}" — connected: ${conns.map(c => c.key).join(', ')}` }
      rows = rows.filter(r => r._conn === gw.id)
    }
    if (args.status) {
      const want = new Set(String(args.status).toLowerCase().split(/[\s,|]+/).filter(Boolean))
      rows = rows.filter(r => want.has(String(r.status).toLowerCase()))
    }
    const limit = Math.max(1, Math.min(100, Number(args.limit) || 20))
    const recency = (a, b) => taskActivity(b) - taskActivity(a)
    const picked = args.query
      ? rows.map(r => ({ r, s: taskScore(r, args.query, { loose: true }) })).filter(x => x.s > 0).sort((a, b) => b.s - a.s || recency(a.r, b.r)).map(x => x.r)
      : [...rows].sort(recency)
    return {
      ok: true,
      gateways: conns.map(c => ({ name: c.key, label: c.label, id: c.id, this_gateway: c.id === origin, ...(taskErrors.has(c.id) ? { error: taskErrors.get(c.id) } : {}) })),
      tasks: picked.slice(0, limit).map(r => ({
        ref: taskRef({ connectionId: r._conn, board: r.board, taskId: r.id }, origin, conns),
        id: r.id,
        title: r.title,
        status: r.status,
        assignee: r.assignee || null,
        gateway: r._gwKey,
        board: r.board,
        last_activity: taskActivity(r) ? new Date(taskActivity(r) * 1000).toISOString() : null
      }))
    }
  }

  const { target, found, error, retryable } = await resolveTask(args.target, origin, conns)
  if (error) return { ok: false, error, ...(retryable ? { retryable: true } : {}) }
  const gw = conns.find(c => c.id === target.connectionId)
  const ref = taskRef(target, origin, conns)

  if (action === 'task_read') {
    return { ok: true, gateway: gw?.key || target.connectionId, board: target.board || 'default', ref, task: summarizeTask(found.data, Number(args.limit) || 10) }
  }

  if (action === 'task_comment') {
    const body = String(args.message || '').trim()
    if (!body) return { ok: false, error: 'message is required for comment' }
    const sender = req.sender || {}
    const author = String(sender.title || sender.session_id || 'cross-gateway agent').slice(0, 80)
    await api(target.connectionId, `/api/plugins/kanban/tasks/${enc(target.taskId)}/comments${target.board ? `?board=${enc(target.board)}` : ''}`, {
      method: 'POST',
      body: { body, author },
      timeoutMs: 15_000
    })
    taskCache.delete(target.connectionId)
    return { ok: true, commented: true, gateway: gw?.key || target.connectionId, ref }
  }

  return { ok: false, error: `unknown action ${action}` }
}

// ── typeahead ────────────────────────────────────────────────────────────────

function focusedIds() {
  const ids = new Set()
  for (const key of ['focusedStoredSessionId', 'focusedSessionId', 'activeSessionId']) {
    try {
      const v = host.state[key]?.get?.()
      if (v) ids.add(String(v))
    } catch {
      /* optional */
    }
  }
  return ids
}

/** Popover rows saying a gateway is still loading (nothing cached yet) or failed. Picking one
 *  inserts the bare scoped prefix, like the `@session:` helper row. */
export function statusItems(status, kind, prefix) {
  const items = []
  if (status?.loading?.length) {
    items.push({ insert: prefix, display: `Loading ${kind} from ${status.loading.join(', ')}…`, meta: 'This list updates when they arrive', icon: kind === 'tasks' ? 'simple' : 'session' })
  }
  for (const { label, error } of status?.failed || []) {
    items.push({ insert: prefix, display: `Couldn't load ${kind} from ${label}`, meta: String(error || 'unknown error').slice(0, 160), icon: kind === 'tasks' ? 'simple' : 'session' })
  }
  return items
}

export function sessionItems(rows, rawQuery, { active = 'local', conns = [], exclude = new Set(), now = Date.now(), status = null } = {}) {
  const query = String(rawQuery || '')
  const lower = query.toLowerCase()
  const items = []

  if (lower.length >= 2 && 'session'.startsWith(lower)) {
    items.push({ insert: '@session:', display: '@session:', meta: 'Mention a session (any gateway)', icon: 'session' })
  }

  const scoped = lower.startsWith('session:')
  const q = scoped ? query.slice('session:'.length) : query
  if (!scoped && q.length < 2) return items

  const multi = new Set(rows.map(r => r._conn)).size > 1 || conns.length > 1
  const candidates = rows.filter(r => r && r.id && !(r._conn === active && exclude.has(r.id)))
  const recency = (a, b) => Number(b.last_active || b.started_at || 0) - Number(a.last_active || a.started_at || 0)

  const picked = !q.trim()
    ? [...candidates].sort(recency).slice(0, MAX_RECENT)
    : candidates
        .map(row => ({ row, s: score(row, q) }))
        .filter(x => x.s > 0)
        .sort((a, b) => b.s - a.s || recency(a.row, b.row))
        .slice(0, MAX_MATCHES)
        .map(x => x.row)

  for (const row of picked) {
    const bits = ['Session']
    if (multi) bits.push(row._conn === active ? `${row._gwLabel} (this gateway)` : row._gwLabel)
    bits.push(ago(row.last_active || row.started_at, now))
    if (row.message_count) bits.push(`${row.message_count} msgs`)
    items.push({
      insert: sessionRef({ connectionId: row._conn, profile: row.profile, sessionId: row.id }, active, conns),
      display: rowLabel(row),
      meta: bits.filter(Boolean).join(' · '),
      icon: 'session'
    })
  }
  items.push(...statusItems(status, 'sessions', '@session:'))
  return items
}

// ── doubled-prefix repair ────────────────────────────────────────────────────

const DOUBLED_RE = /@session:([`"']?)@session:([^\s`"']+)\1/g

export function fixDraftText(text) {
  return typeof text === 'string' ? text.replace(DOUBLED_RE, '@session:$2') : text
}

export function fixChip(chip) {
  const id = chip?.dataset?.refId || ''
  if (chip?.dataset?.refKind !== 'session' || !id.startsWith('@session:')) return false
  const value = id.slice('@session:'.length)
  chip.dataset.refId = value
  chip.dataset.refText = `@session:${value}`
  chip.title = value
  return true
}

function fixAllChips(root = document) {
  for (const chip of root.querySelectorAll?.('span.ref[data-ref-kind="session"]') || []) fixChip(chip)
}

// ── /tell ────────────────────────────────────────────────────────────────────

export const TELL_RE = /^\s*\/tell\s+(@session:(?:`[^`]+`|"[^"]+"|'[^']+'|\S+))\s+([\s\S]*\S[\s\S]*)$/

async function handleTell(text) {
  const m = TELL_RE.exec(text)
  if (!m) return false
  const conns = await connections()
  const target = parseRef(m[1], activeConnectionId(conns), conns)
  if (target.error) {
    host.notifyError?.(new Error(target.error), 'Could not send')
    return 'error'
  }
  if (!target.profile) target.profile = 'default'
  try {
    const title = await sessionTitle(target)
    const result = await deliver(target, m[2].trim(), 0)
    if (!result.ok) throw new Error(`${result.error || 'Send not acknowledged'}. ${result.note || 'Inspect the target before retrying.'}`)
    const gw = conns.find(c => c.id === target.connectionId)
    host.notify?.({ kind: 'success', title: result.queued ? 'Message queued' : 'Message sent', message: `→ "${title || target.sessionId}" on ${gw?.label || target.connectionId}` })
    return 'sent'
  } catch (error) {
    host.notifyError?.(error instanceof Error ? error : new Error(String(error)), 'Could not send')
    return 'error'
  }
}

// ── courier for the cross_session agent tool ─────────────────────────────────

const courierState = new Map() // connectionId -> {until}
let courierBusy = false

export async function performRequest(req, origin, conns) {
  const args = req?.args || {}
  const action = req?.action

  if (String(action || '').startsWith('task_')) return performTaskRequest(req, origin, conns)
  if (action === 'user_terminal') return performUserTerminal(req, origin, conns)

  if (action === 'list') {
    let rows = await allRows(true)
    if (args.gateway) {
      const gw = findGateway(conns, args.gateway)
      if (!gw) return { ok: false, error: `unknown gateway "${args.gateway}" — connected: ${conns.map(c => c.key).join(', ')}` }
      rows = rows.filter(r => r._conn === gw.id)
    }
    const limit = Math.max(1, Math.min(100, Number(args.limit) || 20))
    const recency = (a, b) => Number(b.last_active || 0) - Number(a.last_active || 0)
    const picked = args.query
      ? rows.map(r => ({ r, s: score(r, args.query) })).filter(x => x.s > 0).sort((a, b) => b.s - a.s || recency(a.r, b.r)).map(x => x.r)
      : [...rows].sort(recency)
    return {
      ok: true,
      gateways: conns.map(c => ({ name: c.key, label: c.label, id: c.id, this_gateway: c.id === origin, ...(rowErrors.has(c.id) ? { error: rowErrors.get(c.id) } : {}) })),
      sessions: picked.slice(0, limit).map(r => ({
        ref: sessionRef({ connectionId: r._conn, profile: r.profile, sessionId: r.id }, origin, conns),
        title: rowLabel(r),
        gateway: r._gwKey,
        profile: r.profile || 'default',
        last_active: r.last_active ? new Date(r.last_active * 1000).toISOString() : null,
        message_count: r.message_count || 0
      }))
    }
  }

  const target = parseRef(args.target, origin, conns)
  if (target.error) return { ok: false, error: target.error }
  if (!target.profile) target.profile = 'default'
  const gw = conns.find(c => c.id === target.connectionId)

  if (action === 'read') {
    const tr = await readTranscript(target, Number(args.limit) || 40)
    return { ok: true, gateway: gw?.key || target.connectionId, ref: sessionRef(target, origin, conns), ...tr }
  }

  if (action === 'send') {
    let text = String(args.message || '')
    if (!req.prewrapped) {
      const sender = req.sender || {}
      const senderRef = sender.session_id
        ? sessionRef({ connectionId: origin, profile: sender.profile, sessionId: sender.session_id }, target.connectionId, conns)
        : ''
      const header = `[Message from another Hermes session${sender.title ? ` "${sender.title}"` : ''}${senderRef ? ` (${senderRef})` : ''}]`
      text = `${header}\n\n${text}`
    }
    const wait = Number(req.wait_for_reply) || 0
    // A gateway that runs its own Desktop holds its open chats on that Desktop's backend; this
    // Desktop only reaches it through a separate serve process, which core refuses ("This chat
    // is open in another Hermes window"). Hand the send to that gateway's own courier.
    const home = conns.find(c => c.kind === 'local')
    let forwarded = !req.forwarded && target.connectionId !== (home?.id || 'local') ? await forwardSend(target, text, wait) : null
    // Refused there = never admitted, and the chat may be open in THIS Desktop (its backend on
    // that gateway owns it): the direct route is then the owning one.
    if (forwarded?.status === 'refused') forwarded = null
    const res = forwarded || (await deliver(target, text, wait))
    return { ...res, gateway: gw?.key || target.connectionId, ref: sessionRef(target, origin, conns) }
  }

  return { ok: false, error: `unknown action ${action}` }
}

// ── run_in_user_terminal relayed from a gateway with no screen ───────────────
// The remote tool addresses the request to this Mac (route = its device name), so only this courier
// claims it. The job runs on THIS Mac's own backend (user-terminal plugin routes), which opens the
// confirm window; nothing runs until the owner types y there.

// Surfaces whose user is the owner. The remote tool already refuses the rest; this is the second gate.
const OWNER_SURFACES = new Set(['desktop', 'api_server', 'tui', 'cli', 'local'])
const UT_BASE = '/api/plugins/user-terminal'

export function ownerSurface(platform) {
  const ids = String(platform || '')
    .split(',')
    .map(s => s.trim().toLowerCase())
    .filter(Boolean)
  return ids.length > 0 && ids.every(id => OWNER_SURFACES.has(id))
}

export async function performUserTerminal(req, origin, conns) {
  const home = conns.find(isHome)
  if (!home) return { ok: false, error: 'this Desktop has no local backend to open a terminal on' }
  const sender = req?.sender || {}
  if (!ownerSurface(sender.platform)) {
    return { ok: false, error: `refused: the request came from a ${sender.platform || 'unknown'} session, not the owner's own chat. Ask the owner to request it from their Hermes chat.` }
  }
  const args = req?.args || {}
  // Named by THIS Desktop (the connection it drained), never by the remote agent.
  const gw = conns.find(c => c.id === origin)
  const gateway = gw?.key || origin
  const device = conns.find(isHome)?.device || home.key
  try {
    if (args.job_id) {
      const wait = Math.max(0, Math.min(25, Number(args.wait_s) || 0))
      const res = await api(home.id, `${UT_BASE}/status/${enc(args.job_id)}?gateway=${enc(gateway)}&wait_s=${wait}`, { timeoutMs: (wait + 20) * 1000 })
      return { ...res, device }
    }
    const res = await api(home.id, `${UT_BASE}/start`, {
      method: 'POST',
      body: {
        command: String(args.command || ''),
        reason: String(args.reason || ''),
        cwd: String(args.cwd || ''),
        origin: { gateway, gateway_label: gw?.label || '', profile: sender.profile || 'default', session_id: sender.session_id || '', title: sender.title || '' }
      },
      timeoutMs: 40_000
    })
    return { ...res, device }
  } catch (error) {
    const msg = String(error?.message || error)
    const hint = /404|not found/i.test(msg) ? ' (this Mac\'s backend has no user-terminal routes: enable the user-terminal plugin and update it)' : ''
    return { ok: false, error: `could not reach the user-terminal plugin on ${device}: ${msg}${hint}`, device }
  }
}

const relay = (connectionId, path, opts) => api(connectionId, `/api/plugins/${PLUGIN_ID}${path}`, opts)

/** Hand a send to the target gateway's own Desktop courier. Returns null when that gateway has
 *  no live home courier (or predates forwarding) and nothing was queued there, so the caller
 *  delivers directly. Never returns null once the forwarded request may have been claimed. */
export async function forwardSend(target, text, waitSeconds = 0) {
  const ping = await relay(target.connectionId, '/ping', { timeoutMs: 8_000 }).catch(() => null)
  if (!ping?.forwarding || !ping?.home_courier) return null
  const id = `fwd-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
  const request = {
    id,
    action: 'send',
    args: { target: `@session:${target.profile || 'default'}/${target.sessionId}`, message: text },
    prewrapped: true,
    forwarded: true,
    wait_for_reply: waitSeconds,
    ttl: 600
  }
  const status = () => relay(target.connectionId, `/status/${id}`, { timeoutMs: 10_000 }).catch(() => null)
  const withdraw = async () => !!(await relay(target.connectionId, '/cancel', { method: 'POST', body: { id }, timeoutMs: 10_000 }).catch(() => null))?.ok
  const unknown = error => ({ ok: false, delivered: null, status: 'unknown', retry_safe: false, forwarded: true, error, note: 'Delivery outcome unknown. Inspect the target transcript; do not resend automatically.' })

  let enq = null
  try {
    enq = await relay(target.connectionId, '/enqueue', { method: 'POST', body: { request }, timeoutMs: 15_000 })
  } catch {
    // The POST may have landed. Only fall back once the request is provably not there.
    const st = await status()
    if (!st) return unknown('Lost the forwarding acknowledgement and could not check it')
    if (st.state === 'unknown') return null
    if (st.state === 'pending' && (await withdraw())) return null
    enq = { ok: true }
  }
  if (!enq?.ok) return null

  const started = Date.now()
  const deadline = started + FORWARD_PICKUP_MS + 90_000 + Math.min(600, Math.max(0, waitSeconds)) * 1000
  while (Date.now() < deadline) {
    await sleep(FORWARD_POLL_MS)
    const st = await status()
    if (st?.state === 'done') return { ...(st.result || { ok: false, error: 'empty forwarded result' }), forwarded: true }
    if (st?.state === 'pending' && Date.now() - started > FORWARD_PICKUP_MS && (await withdraw())) return null
  }
  return unknown(`The target gateway's Desktop did not finish the forwarded send in time`)
}

let lastHomeBeat = 0

/** Drain query: home flag, plus (once this Mac's device name is known) who is draining and every
 *  gateway name it can reach, so the gateway hands it only requests it can serve. */
export function outboxPath(conn, conns) {
  const q = []
  if (isHome(conn)) q.push('home=1')
  const me = conns.find(isHome)
  if (me?.device) {
    q.push(`courier=${enc(me.device)}`, `aliases=${enc(me.aliases.join(','))}`, `reach=${enc(reachNames(conns).join(','))}`)
  }
  return `/outbox${q.length ? `?${q.join('&')}` : ''}`
}

/** This Desktop's own machine. Its backend owns the chats open in this Desktop's windows. */
const isHome = conn => conn?.kind === 'local'

export async function courierTick() {
  if (courierBusy) return
  courierBusy = true
  try {
    const conns = await connections()
    const home = conns.find(isHome)
    if (home && Date.now() - lastHomeBeat >= HOME_BEAT_MS) {
      // Tell this gateway its own courier is alive, so other Macs' Desktops (which reach it through
      // a non-owning serve process) leave its outbox to us. Plain /result, so it also works on a
      // backend that predates the home-courier gate.
      lastHomeBeat = Date.now()
      void relay(home.id, '/result', { method: 'POST', body: { id: HOME_COURIER_ID, result: { at: Date.now() / 1000 } }, timeoutMs: 10_000 }).catch(() => {
        lastHomeBeat = 0
      })
    }
    await Promise.all(
      conns.map(async conn => {
        const st = courierState.get(conn.id)
        if (st && Date.now() < st.until) return
        let res
        try {
          res = await relay(conn.id, outboxPath(conn, conns), { timeoutMs: 10_000 })
        } catch (error) {
          const msg = String(error?.message || error)
          courierState.set(conn.id, { until: Date.now() + (/404|not found/i.test(msg) ? UNSUPPORTED_BACKOFF_MS : ERROR_BACKOFF_MS) })
          return
        }
        courierState.delete(conn.id)
        for (const req of Array.isArray(res?.requests) ? res.requests : []) {
          // Don't block the drain on a long send-and-wait.
          void (async () => {
            let result
            try {
              result = await performRequest(req, conn.id, conns)
            } catch (error) {
              result = { ok: false, error: String(error?.message || error) }
            }
            await api(conn.id, `/api/plugins/${PLUGIN_ID}/result`, { method: 'POST', body: { id: req.id, result } }).catch(() => undefined)
          })()
        }
      })
    )
  } finally {
    courierBusy = false
  }
}

// ── plugin ───────────────────────────────────────────────────────────────────

/** setInterval on a dedicated worker; returns a stop function, or null when workers are unavailable. */
export function workerTicker(fn, ms) {
  try {
    if (typeof Worker !== 'function' || typeof Blob !== 'function' || typeof URL?.createObjectURL !== 'function') return null
    const url = URL.createObjectURL(new Blob([`setInterval(() => postMessage(0), ${Number(ms) || 3000})`], { type: 'text/javascript' }))
    const worker = new Worker(url)
    worker.onmessage = () => fn()
    return () => {
      worker.terminate()
      URL.revokeObjectURL(url)
    }
  } catch {
    return null
  }
}

export default {
  id: PLUGIN_ID,
  name: 'Session Mentions',
  register(ctx) {
    rowCache.clear()
    taskCache.clear()
    courierState.clear()
    lastHomeBeat = 0
    connCache = { at: 0, rows: [] }
    deviceCache = { at: 0, info: null }
    loadWarm()
    refreshStale()
    refreshStaleTasks()

    const timers = []
    const every = (fn, ms) => {
      const t = typeof ctx.setInterval === 'function' ? ctx.setInterval(fn, ms) : setInterval(fn, ms)
      timers.push(t)
    }
    // The courier must keep its cadence while the window is hidden and idle: Chromium then
    // throttles main-thread timers to ~1/min, which starved the home heartbeat (other Macs'
    // couriers took over again) and left requests waiting a minute. A dedicated worker's timer
    // is not throttled that way, and its messages are not timers.
    const stopWorker = typeof ctx.onDispose === 'function' ? workerTicker(() => void courierTick(), COURIER_INTERVAL_MS) : null
    if (stopWorker) ctx.onDispose(stopWorker)
    else every(() => void courierTick(), COURIER_INTERVAL_MS)

    // sessions.changed fires every ~1-2 s while any turn streams, and a forced fetch of the
    // active (primary) connection makes Desktop main fan /api/sessions out to EVERY connected
    // gateway, up to 5 pages each: ~1,500 backend list reads per 40 s on a 20-gateway Desktop
    // (David-MBP 2026-10-30). The @ list only needs to be fresh when someone types @, so a change
    // marks rows pending; idle refreshes are capped at one per CHANGE_REFRESH_MIN_MS, and
    // provide() refreshes a pending change at once.
    let debounce = null
    let lastForcedAt = 0
    let changePending = false
    const forceActive = () => {
      if (debounce) clearTimeout(debounce)
      debounce = null
      changePending = false
      lastForcedAt = Date.now()
      const active = activeConnectionId()
      const conn = connCache.rows.find(c => c.id === active)
      if (conn) void fetchRows(conn, true)
    }
    flushPendingChange = () => {
      if (changePending) forceActive()
    }
    const onChanged = () => {
      changePending = true
      if (debounce) return // the armed trailing refresh covers this change too
      debounce = setTimeout(forceActive, changeRefreshDelay(Date.now(), lastForcedAt))
    }
    const subscribe = typeof ctx.onEvent === 'function' ? ctx.onEvent : host.onEvent
    const off = subscribe('sessions.changed', onChanged)

    ctx.onDispose?.(() => {
      if (debounce) clearTimeout(debounce)
      if (typeof off === 'function') off()
      if (typeof ctx.setInterval !== 'function') timers.forEach(t => clearInterval(t))
    })

    ctx.register({
      id: 'session-mention-middleware',
      area: COMPOSER_AREAS.middleware,
      data: {
        handler: async draft => {
          if (!draft || typeof draft.text !== 'string') return draft
          const text = fixDraftText(draft.text)
          if (TELL_RE.test(text)) {
            const outcome = await handleTell(text)
            if (outcome === 'sent') setTimeout(() => void host.composer?.setDraft?.(null, ''), 60)
            return null // consumed: never reaches the agent
          }
          return { ...draft, text }
        }
      }
    })

    if (typeof MutationObserver !== 'undefined' && typeof document !== 'undefined') {
      const observer = new MutationObserver(records => {
        for (const r of records) {
          for (const node of r.addedNodes) {
            if (node.nodeType !== 1) continue
            if (node.matches?.('span.ref[data-ref-kind="session"]')) fixChip(node)
            else fixAllChips(node)
          }
        }
      })
      observer.observe(document.body, { childList: true, subtree: true })
      fixAllChips()
      ctx.onDispose?.(() => observer.disconnect())
    }

    ctx.register({
      id: 'session-mention-completions',
      area: COMPOSER_AREAS.atCompletions,
      data: {
        provide: query => {
          flushPendingChange()
          refreshStale()
          const conns = connCache.rows
          const status = fetchStatus(rowCache, rowInflight, rowErrors, conns)
          return sessionItems(cachedRows(), query, { active: activeConnectionId(conns), conns, exclude: focusedIds(), status })
        },
        subscribe: subscribeRows
      }
    })

    ctx.register({
      id: 'task-mention-completions',
      area: COMPOSER_AREAS.atCompletions,
      data: {
        provide: query => {
          refreshStaleTasks()
          const conns = connCache.rows
          const status = fetchStatus(taskCache, taskInflight, taskErrors, conns)
          return taskItems(cachedTasks(), query, { active: activeConnectionId(conns), conns, status })
        },
        subscribe: subscribeRows
      }
    })
  }
}

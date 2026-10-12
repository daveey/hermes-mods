/**
 * Session Mentions — @-mention, read, and message sessions on ANY connected gateway.
 *
 * 1. Typeahead. In the composer:
 *      @photo        sessions on every connected gateway whose title matches
 *      @photo-gal    "-", "_", "." act as spaces (the @ token can't hold a space)
 *      @session:     the most recent sessions
 *    Sessions on the active gateway insert `@session:<profile>/<id>`; sessions on another
 *    gateway insert `@session:<profile>@<gateway>/<id>`.
 * 2. Referencing. The agent resolves cross-gateway links with the `cross_session` tool
 *    (agent plugin ~/.hermes/plugins/session-mentions); this plugin is its courier.
 * 3. Sending. `/tell @session:… <message>` delivers <message> into that session as a new turn.
 * 4. Agent messaging. Courier loop: drains `cross_session` requests from every connected
 *    gateway that has the agent plugin (GET /api/plugins/session-mentions/outbox), performs
 *    list/read/send on the target gateway's own connection, posts the result back.
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

export function gatewayKeys(conns) {
  // Prefer the slugged label (`nous-1`); fall back to the id when labels collide.
  const counts = new Map()
  for (const c of conns) counts.set(slug(c.label), (counts.get(slug(c.label)) || 0) + 1)
  return conns.map(c => ({ ...c, key: slug(c.label) && counts.get(slug(c.label)) === 1 ? slug(c.label) : c.id }))
}

const FALLBACK_CONNS = [{ id: 'local', label: 'This device', kind: 'local', primary: true }]

async function connections(force = false) {
  if (!force && Date.now() - connCache.at < STALE_MS && connCache.rows.length) return connCache.rows
  try {
    const rows = typeof host.connections === 'function' ? await host.connections() : []
    const list = (Array.isArray(rows) ? rows : [])
      .filter(r => r && r.id)
      .map(r => ({ id: String(r.id), label: String(r.label || r.name || r.id), kind: String(r.kind || ''), primary: !!r.primary }))
    connCache = { at: Date.now(), rows: gatewayKeys(list.length ? list : FALLBACK_CONNS) }
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
  return conns.find(c => c.id.toLowerCase() === n || c.key.toLowerCase() === n || slug(c.label) === slug(n)) || null
}

// ── session refs ─────────────────────────────────────────────────────────────

/** `@session:<profile>/<id>` relative to `relativeTo` (a connection id); qualified with
 *  `@<gateway key>` when the session lives on another gateway. */
export function sessionRef({ connectionId, profile, sessionId }, relativeTo, conns) {
  const p = profile || 'default'
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
      gateway = head.slice(at + 1)
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

function fetchRows(conn, force = false) {
  const cached = rowCache.get(conn.id)
  if (!force && cached && Date.now() - cached.at < STALE_MS) return Promise.resolve(cached.rows)
  if (rowInflight.has(conn.id)) return rowInflight.get(conn.id)
  const p = api(conn.id, '/api/profiles/sessions?limit=300&offset=0&order=recent&profile=all&min_messages=1&archived=exclude&exclude_sources=cron')
    .then(res => {
      const rows = tagRows(res?.sessions, conn, connCache.rows)
      rowCache.set(conn.id, { at: Date.now(), rows })
      rowErrors.delete(conn.id)
      return rows
    })
    .catch(error => {
      // Keep stale rows; retry after a short backoff.
      rowErrors.set(conn.id, String(error?.message || error).slice(0, 300))
      const old = rowCache.get(conn.id)
      rowCache.set(conn.id, { at: Date.now() - STALE_MS + 10_000, rows: old?.rows || [] })
      return old?.rows || []
    })
    .finally(() => rowInflight.delete(conn.id))
  rowInflight.set(conn.id, p)
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
    return host.requestProfile(route, method, params, timeout)
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

export function sessionItems(rows, rawQuery, { active = 'local', conns = [], exclude = new Set(), now = Date.now() } = {}) {
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
    const sender = req.sender || {}
    const senderRef = sender.session_id
      ? sessionRef({ connectionId: origin, profile: sender.profile, sessionId: sender.session_id }, target.connectionId, conns)
      : ''
    const header = `[Message from another Hermes session${sender.title ? ` "${sender.title}"` : ''}${senderRef ? ` (${senderRef})` : ''}]`
    const res = await deliver(target, `${header}\n\n${String(args.message || '')}`, Number(req.wait_for_reply) || 0)
    return { ...res, gateway: gw?.key || target.connectionId, ref: sessionRef(target, origin, conns) }
  }

  return { ok: false, error: `unknown action ${action}` }
}

async function courierTick() {
  if (courierBusy) return
  courierBusy = true
  try {
    const conns = await connections()
    await Promise.all(
      conns.map(async conn => {
        const st = courierState.get(conn.id)
        if (st && Date.now() < st.until) return
        let res
        try {
          res = await api(conn.id, `/api/plugins/${PLUGIN_ID}/outbox`, { timeoutMs: 10_000 })
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

export default {
  id: PLUGIN_ID,
  name: 'Session Mentions',
  register(ctx) {
    rowCache.clear()
    courierState.clear()
    connCache = { at: 0, rows: [] }
    refreshStale()

    const timers = []
    const every = (fn, ms) => {
      const t = typeof ctx.setInterval === 'function' ? ctx.setInterval(fn, ms) : setInterval(fn, ms)
      timers.push(t)
    }
    every(() => void courierTick(), COURIER_INTERVAL_MS)

    let debounce = null
    const onChanged = () => {
      if (debounce) clearTimeout(debounce)
      debounce = setTimeout(() => {
        const active = activeConnectionId()
        const conn = connCache.rows.find(c => c.id === active)
        if (conn) void fetchRows(conn, true)
      }, 400)
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
          refreshStale()
          const conns = connCache.rows
          return sessionItems(cachedRows(), query, { active: activeConnectionId(conns), conns, exclude: focusedIds() })
        }
      }
    })
  }
}

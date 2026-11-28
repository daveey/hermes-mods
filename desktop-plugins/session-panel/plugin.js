/** Session Panel: render the hub's PanelModel v1; no local work-state inference. */
import { COMPOSER_AREAS, host, useValue } from '@hermes/plugin-sdk'
// Namespace: ContextMenu* are absent on older hosts (and in test stubs); rows then get no menu.
import * as sdk from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

// Desktop's runtime loader accepts only @hermes/plugin-sdk and react imports: a sibling module
// (`./x.js`) fails the whole plugin load, so the process viewer lives here, not in its own file.
/** Resolve once per explicit open. Never fall back to the active backend for a named owner. */
export function processRoute(target, bridge, gateways) {
  if (!target?.id || !target.session || !target.gateway || !target.profile) throw Error('Process owner is unavailable.')
  const connectionId = bridge?.connectionForHubGateway?.(target.gateway)
  const gateway = gateways?.find(g => g?.id === connectionId)
  if (!connectionId || !gateway) throw Error('The gateway that holds this process is not connected.')
  return Object.freeze({ connectionId, mode: gateway.kind === 'local' ? 'local' : 'remote',
    profile: target.profile, targetProfile: target.profile })
}

/** Existing owner-scoped RPCs only; no shell, kill, log acknowledgement or ambient request. */
export async function readProcessView(target, route, request) {
  const attached = await request(route, 'session.resume', {session_id: target.session, lazy: true, omit_messages: true}, 30000, {spawnPriority: 'foreground'})
  if (attached?.stored_session_id !== target.session || !attached?.session_id) throw Error('The owning session changed. Refresh the panel before opening this process.')
  const result = await request(route, 'process.list', {session_id: attached.session_id}, 10000)
  if (!Array.isArray(result?.processes)) throw Error('Process status is unavailable.')
  const process = result.processes.find(p => p?.session_id === target.id)
  if (!process) throw Error('This process is no longer in the owning session’s live inventory. Its output may have expired.')
  return {id: target.id, command: String(process.command || target.id), status: String(process.status || 'unknown'),
    output: typeof process.output_tail === 'string' ? process.output_tail : '', exit_code: process.exit_code}
}

/** Read-only, explicit-refresh process viewer. Closing/switching owner cancels late presentation. */
export function ProcessView({target, route, onClose}) {
  const [value, setValue] = useState(null), [error, setError] = useState(null), [retry, setRetry] = useState(0)
  useEffect(() => {
    let active = true
    setValue(null); setError(null)
    void readProcessView(target, route, (...args) => sdk.host.requestProfile(...args))
      .then(v => {if (active) setValue(v)}, e => {if (active) setError(String(e?.message || e))})
    return () => {active = false}
  }, [target, route, retry])
  const {Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription} = sdk
  if (!Dialog || !DialogContent || !DialogTitle) return null
  return jsx(Dialog, {open: true, onOpenChange: open => {if (!open) onClose()}, children:
    jsxs(DialogContent, {'data-process-view': target.id, children: [
      jsx(DialogHeader || 'div', {children: jsx(DialogTitle, {children: value?.command || target.title || target.id})}),
      jsx(DialogDescription || 'p', {children: `${target.gateway} / ${target.profile} · ${target.id}`}),
      target.detail && jsx('p', {'data-process-snapshot': '', children: `Panel observation at open: ${target.detail}`}),
      jsx('div', {role: 'status', children: error || (value ? value.status + (value.exit_code == null ? '' : ` · exit ${value.exit_code}`) : 'Loading process…')}),
      value && jsx('pre', {'data-process-output': '', style: {whiteSpace: 'pre-wrap', overflow: 'auto', maxHeight: '50vh'}, children: value.output || 'No output yet.'}),
      jsx('button', {type: 'button', onClick: () => setRetry(n => n + 1), children: 'Refresh'}),
      jsx('button', {type: 'button', onClick: onClose, children: 'Close'})
    ]})})
}

// Identical in the two standalone disk plugins; matches core lib/wait-eta.ts.
export function formatWaitDuration(seconds) {
  const s = Math.max(0, Number.isFinite(seconds) ? seconds : 0)
  for (const [size, unit] of [[86400, 'd'], [3600, 'h'], [60, 'm']]) {
    if (s >= size) {
      const value = s / size
      return unit === 'm' || value >= 10 ? `${Math.round(value)}${unit}` : `${value.toFixed(1).replace(/\.0$/, '')}${unit}`
    }
  }
  return `${Math.round(s)}s`
}
export function waitReadout(eta, now) {
  if (!eta || !['ci', 'deploy', 'clock'].includes(eta.wait_kind) || !Number.isFinite(eta.waited_since)) return null
  const waitedS = Math.max(0, now - eta.waited_since), waited = formatWaitDuration(waitedS)
  const what = `Waiting on ${{ ci: 'CI', deploy: 'deploy', clock: 'timer' }[eta.wait_kind]}`
  const source = typeof eta.estimate_source === 'string' ? eta.estimate_source.trim() : ''
  if (!Number.isFinite(eta.expected_s)) return { waited, expected: null, left: '—', over: false,
    title: `${what} · ${waited} so far · no estimate${source ? ` (${source})` : ''}` }
  const leftS = eta.expected_s - waitedS, expected = formatWaitDuration(eta.expected_s), over = leftS < 0
  const left = over ? `+${formatWaitDuration(-leftS)}` : formatWaitDuration(leftS)
  return { waited, expected, left, over,
    title: `${what} · waited ${waited} · expected ${expected}${source ? ` (${source})` : ''} · ${over ? `${left.slice(1)} over` : `${left} left`}` }
}
export function renderWaitEta(eta, now, props = {}) {
  const r = waitReadout(eta, now)
  return r && jsxs('span', { ...props, 'data-wait-eta': eta.wait_kind, title: r.title,
    style: { display: 'inline-grid', gridTemplateColumns: '4ch 5ch 5ch', columnGap: 2, alignItems: 'baseline',
      fontSize: 11, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap', flex: 'none', textAlign: 'right', ...props.style },
    children: [jsx('span', { style: { opacity: .75 }, children: r.waited }),
      jsx('span', { style: { color: 'var(--muted-foreground)', textAlign: 'left', paddingLeft: 2 }, children: `/ ${r.expected ?? '?'}` }),
      jsx('span', { 'data-eta-left': '', style: { color: r.over ? '#f0883e' : eta.wait_kind === 'deploy' ? '#a371f7' : '#38bdf8' }, children: r.left })] })
}

const ID = 'session-panel'
const VERSION = 'session-panel-v29-frame'
const str = v => (typeof v === 'string' ? v : v == null ? '' : String(v))
// Emoji-presentation glyphs the Desktop fonts draw as tofu (a Hermes-bug title carried "⛔"); the model strips them
// too, this covers a cached older model.
const EMOJI_RANGES = [
  [0x1F000, 0x1FAFF], [0x231A, 0x231B], [0x23E9, 0x23F3], [0x23F8, 0x23FA],
  [0x25FD, 0x25FE], [0x2614, 0x2615], [0x2648, 0x2653], [0x267F, 0x267F],
  [0x2693, 0x2693], [0x26A1, 0x26A1], [0x26AA, 0x26AB], [0x26BD, 0x26BE],
  [0x26C4, 0x26C5], [0x26CE, 0x26CE], [0x26D4, 0x26D4], [0x26EA, 0x26EA],
  [0x26F2, 0x26F3], [0x26F5, 0x26F5], [0x26FA, 0x26FA], [0x26FD, 0x26FD],
  [0x2705, 0x2705], [0x270A, 0x270B], [0x2728, 0x2728], [0x274C, 0x274C],
  [0x274E, 0x274E], [0x2753, 0x2755], [0x2757, 0x2757], [0x2795, 0x2797],
  [0x27B0, 0x27B0], [0x27BF, 0x27BF], [0x2B1B, 0x2B1C], [0x2B50, 0x2B50],
  [0x2B55, 0x2B55], [0xFE0F, 0xFE0F], [0x200D, 0x200D], [0x20E3, 0x20E3]
]
export const plain = v => Array.from(str(v)).filter(c => !EMOJI_RANGES.some(([lo, hi]) => lo <= c.codePointAt(0) && c.codePointAt(0) <= hi)).join('').replace(/\s+/g, ' ').trim()
let osApi = null // ctx.os, set in register()
const log = (...a) => console.info(`[${ID}]`, ...a)
const clamp = n => ({ display: '-webkit-box', WebkitLineClamp: n, WebkitBoxOrient: 'vertical', overflow: 'hidden' })
const pill = color => ({ fontSize: 10, fontWeight: 700, letterSpacing: 0.4, textTransform: 'uppercase', color, border: '1px solid currentColor', borderRadius: 999, padding: '1px 6px', whiteSpace: 'nowrap' })
const askLinkBtn = { background: 'none', border: 0, padding: 0, font: 'inherit', fontSize: 12, color: 'light-dark(#0969da, #58a6ff)', cursor: 'pointer' }

const panelRoute = () => {
  const owner = host.state?.focusedSessionOwner?.get?.()
  return {
    connectionId: owner ? owner.connectionId : host.state?.connectionId?.get?.() || null,
    profile: owner ? (owner.targetProfile || owner.profile || 'default') : (host.state?.profile?.get?.() || 'default'),
    session: host.state?.focusedStoredSessionId?.get?.() || null
  }
}
const routeKey = r => JSON.stringify([r.connectionId || '', r.profile, r.session])

const RECENT_KEY = 'hermes.sessionPanel.recent.v1'
export const WARM_RECENT = 8
// [daveey 2026-11-20] "pre-compute the status panels ... so most switches are instant". Besides the chats you
// focused, the sidebar's chats are kept warm too (one never opened in this window included).
// [daveey 2026-11-22] "clicking on a session takes forever to compute the status panel": 12 sidebar chats were
// warm, every other click waited for the gateway. Now the whole sidebar is, most recently active first, up to
// what one registration carries (200 chats a request; the gateway watches 400 across its Desktops).
export const WARM_SIDEBAR = 180
export const WARM_MAX = 190
export const MODEL_CACHE_MAX = 400
export function readRecent() {
  try { const v = JSON.parse(window.localStorage.getItem(RECENT_KEY) || '[]'); return Array.isArray(v) ? v.filter(r => r?.connectionId && r?.session) : [] } catch { return [] }
}
/** Put a focused chat at the head of the recent list (deduped by route, capped). */
export function noteRecent(route) {
  if (!route?.connectionId || !route?.session) return
  const key = routeKey(route)
  const next = [{ connectionId: route.connectionId, profile: route.profile || 'default', session: route.session }, ...readRecent().filter(r => routeKey(r) !== key)].slice(0, WARM_RECENT)
  try { window.localStorage.setItem(RECENT_KEY, JSON.stringify(next)) } catch {}
}

/** The chats kept warm: recently focused first, then the most recently active sidebar chats (not archived), deduped,
 *  at most WARM_MAX. `rows` = Desktop's sidebar rows (`host.sessions.rows`). */
export function warmTargets(rows = (() => { try { return host?.sessions?.rows?.get?.() || [] } catch { return [] } })()) {
  const out = [], seen = new Set()
  const add = r => { const k = routeKey(r); if (!seen.has(k) && out.length < WARM_MAX) { seen.add(k); out.push(r) } }
  readRecent().forEach(add)
  ;(Array.isArray(rows) ? rows : [])
    .filter(r => r && !r.archived && str(r.id) && str(r.connection_id))
    .sort((a, b) => (Number(b.last_active) || 0) - (Number(a.last_active) || 0))
    .slice(0, WARM_SIDEBAR)
    .forEach(r => add({ connectionId: str(r.connection_id), profile: str(r.profile) || 'default', session: str(r.id) }))
  return out
}


const FEED_PATH = '/api/plugins/session-panel/changes'
export const FEED_EVENT = 'plugin.session-panel.changed'
export const FEED_CATCHUP_MS = 60_000
export const FEED_STALE_MS = 60_000 // three missed 20 s heartbeats
const FEED_UNSUPPORTED_RETRY_MS = 10 * 60_000
// A backend that just started answers 503 (Retry-After: 1) until its feed exists: ask again shortly, a few times.
export const FEED_STARTING = { retryMs: 1_000, tries: 3 }
const feeds = new Map()      // JSON [connectionId, profile] -> { connectionId, profile, feed, epoch, rev, sessions, liveAt, unsupported, at }
const feedFlights = new Map() // same key -> the catch-up in flight
const connKey = (connectionId, profile) => JSON.stringify([connectionId || '', profile || 'default'])
// One desktop.log line per feed state change (registered, no backend half, pushes started), like open-timing:
// `[panel-feed] <connection>/<profile> <state> ...`.
const feedLog = msg => { try { window.hermesDesktop?.logLine?.(`[panel-feed] ${msg}`) } catch {} }
const tasksBridge = () => (typeof window !== 'undefined' ? window.__sessionTasks : null)
const unsupportedFeed = route => feeds.get(connKey(route?.connectionId, route?.profile))?.unsupported === true
/** Only replace the task strip when this exact chat has a renderable model. */
export function covers(route) {
  return !!route?.connectionId && !!route?.session && !unsupportedFeed(route) && !!cachedModel(route)
}
/** True while `route`'s gateway feed pushes (or heartbeats) reach this window. */
export function feedIsLive(route, nowMs = Date.now()) {
  const st = route?.connectionId ? feeds.get(connKey(route.connectionId, route.profile)) : null
  return !!st?.liveAt && !st.unsupported && nowMs - st.liveAt <= FEED_STALE_MS
}
/** True while `route`'s gateway feed is live and its last push vouched for this chat. */
export function feedVouches(route, nowMs = Date.now()) {
  const st = route?.connectionId ? feeds.get(connKey(route.connectionId, route.profile)) : null
  return feedIsLive(route, nowMs) && !!st?.fresh?.includes(route.session)
}
const modelVersions = new Map()
export const resetFeedForTests = () => { feeds.clear(); feedFlights.clear(); feedQueued.clear(); modelCache.clear(); modelVersions.clear(); decisionTimings.clear(); provisionalDecisions.clear() }

/** Cache only the model. Missing/unsupported models invalidate this exact scope; raw cards are never a fallback. */
export function ingestBundle(connectionId, profile, b, version = null) {
  if (!connectionId || !b?.session) return false
  profile = profile || 'default'
  const scope = routeKey({ connectionId, profile, session: b.session })
  const old = modelVersions.get(scope)
  if (version && old?.feed === version.feed && old?.epoch === version.epoch && old.rev > version.rev) return false
  if (version) {
    modelVersions.delete(scope)
    modelVersions.set(scope, { feed: version.feed, epoch: version.epoch, rev: version.rev })
    if (modelVersions.size > MODEL_CACHE_MAX) modelVersions.delete(modelVersions.keys().next().value)
  }
  modelCache.delete(scope)
  if (validModel(b.model)) {
    modelCache.set(scope, b.model)
    if (modelCache.size > MODEL_CACHE_MAX) modelCache.delete(modelCache.keys().next().value)
  }
  // Card chips consume the same final display model; raw tasks provide identity only.
  tasksBridge()?.ingestFeed?.({ connectionId, profile, session: b.session,
    lineage: b.lineage, tasks: b.tasks, model: b.model, trusted: true })
  modelChanged()
  return true
}

/** Heartbeats attest transport freshness only; they do not reinterpret a model. */
function confirmFeed(st, fresh, nowMs = Date.now()) {
  st.liveAt = nowMs
  st.fresh = Array.isArray(fresh) ? fresh : []
  modelChanged()
}

/** The chats to watch, per gateway connection: the focused chat first, then warmTargets(). */
export function feedTargets(rows) {
  const out = new Map()
  const focused = panelRoute()
  for (const r of [focused, ...warmTargets(rows)]) {
    if (!r?.connectionId || !r?.session) continue
    const key = connKey(r.connectionId, r.profile)
    if (!out.has(key)) out.set(key, { connectionId: r.connectionId, profile: r.profile || 'default', sessions: [] })
    const t = out.get(key)
    if (!t.sessions.includes(r.session)) t.sessions.push(r.session)
  }
  return [...out.values()]
}

/** Register `target.sessions` with its gateway's feed and apply what changed since our last revision. One in flight
 *  per connection (a later call waits for it). False when the gateway has no feed or the call failed. */
const feedQueued = new Map() // connection key -> the one catch-up waiting its turn ({ target, opts, p })
export function feedCatchUp(target, opts = {}) {
  const key = connKey(target?.connectionId, target?.profile)
  // One waiting catch-up per connection: later calls merge into it (their chats and flags), so a stalled gateway
  // never builds a queue.
  const q = feedQueued.get(key)
  if (q) {
    q.target.sessions = [...new Set([...(q.target.sessions || []), ...(target?.sessions || [])])]
    q.opts.full = q.opts.full || !!opts.full
    q.opts.reset = q.opts.reset || !!opts.reset
    q.opts.preserveCoverage = q.opts.preserveCoverage || !!opts.preserveCoverage
    return q.p
  }
  const entry = { target: { ...target, sessions: [...(target?.sessions || [])] }, opts: { ...opts } }
  const before = feedFlights.get(key) || Promise.resolve()
  // Chained: each runs after the one before it for this connection has answered (never two at once).
  entry.p = before.catch(() => {}).then(() => {
    if (feedQueued.get(key) === entry) feedQueued.delete(key)
    return feedCatchUpNow(entry.target, entry.opts)
  }).catch(e => { log('feed catch-up failed', key, e?.message || e); return false })
  feedQueued.set(key, entry)
  const tail = entry.p.finally(() => { if (feedFlights.get(key) === tail) feedFlights.delete(key) })
  feedFlights.set(key, tail)
  return entry.p
}

async function feedCatchUpNow(target, { nowMs = Date.now(), reset = false, full = false, preserveCoverage = false, attempt = 0 } = {}) {
  const bridge = typeof window !== 'undefined' ? window.hermesDesktop?.api : null
  if (!bridge || !target?.connectionId || !target.sessions?.length) return false
  const profile = target.profile || 'default'
  const key = connKey(target.connectionId, profile)
  const st = feeds.get(key)
  // Resolve after preceding flights complete: their watched set may have changed while queued.
  if (preserveCoverage) target = { ...target, sessions: [...new Set([...target.sessions, ...(st?.sessions || [])])] }
  if (st?.unsupported && nowMs - st.at < FEED_UNSUPPORTED_RETRY_MS) return false
  const unsupported = why => {
    if (!st?.unsupported) feedLog(`${target.connectionId}/${profile} no-feed ${why}`)
    feeds.set(key, { connectionId: target.connectionId, profile, unsupported: true, reason: why, at: nowMs })
    modelChanged()
    return false
  }
  const sameFeed = !reset && st && !st.unsupported
  let r
  try {
    r = await bridge({ path: FEED_PATH, method: 'POST', connectionId: target.connectionId, profile, timeoutMs: 15_000,
      body: { sessions: target.sessions.slice(0, 200), since: sameFeed && !full ? st.rev || 0 : 0, epoch: sameFeed ? st.epoch || null : null, response_encoding: 'gzip' } })
  } catch (e) {
    // The Desktop bridge rejects a 404 with "404: ...": this gateway has no backend half.
    if (/^404\b/.test(String(e?.message || ''))) return unsupported('404')
    if (/^503\b/.test(String(e?.message || '')) && attempt < FEED_STARTING.tries) {
      await new Promise(done => setTimeout(done, FEED_STARTING.retryMs))
      return feedCatchUpNow(target, { reset, full, preserveCoverage, attempt: attempt + 1 })
    }
    log('feed catch-up failed', target.connectionId, e?.message || e)
    return false
  }
  if (!r || r.profile_unsupported || typeof r.epoch !== 'string') return unsupported(r?.profile_unsupported ? 'profile_unsupported' : 'no epoch')
  const cur = feeds.get(key)
  if (cur && !cur.unsupported && cur.epoch === r.epoch && cur.feed === r.feed && Number(r.rev) < cur.rev) {
    // A push for a later revision landed while this request was out: keep it, drop this older answer's bundles.
    if (feedIsLive(cur, nowMs)) {
      cur.sessions = [...new Set([...(cur.sessions || []), ...target.sessions])]
      return true
    }
    // No pushes reach us and the gateway is behind what we hold: it carried the feed on from an older snapshot (a
    // restart). Waiting for it to pass our revision could take hours: start over with it instead.
    if (!reset) {
      feedLog(`${target.connectionId}/${profile} behind rev=${r.rev} < ${cur.rev}: reset`)
      return feedCatchUpNow(target, { nowMs, reset: true, full, preserveCoverage })
    }
  }
  const next = { connectionId: target.connectionId, profile, feedProfile: str(r.profile) || profile, feed: r.feed, epoch: r.epoch, rev: Number(r.rev) || 0,
    sessions: [...target.sessions], liveAt: cur?.feed === r.feed && cur?.epoch === r.epoch ? cur.liveAt : 0, at: Date.now() }
  feeds.set(key, next)
  if (cur?.unsupported) modelChanged()
  if (cur?.epoch !== r.epoch || cur?.feed !== r.feed) feedLog(`${target.connectionId}/${profile} registered feed=${r.feed} epoch=${r.epoch} rev=${next.rev} watching=${target.sessions.length} bundles=${Object.keys(r.bundles || {}).length}`)
  next.fresh = cur?.feed === r.feed && cur?.epoch === r.epoch ? cur.fresh || [] : []
  for (const b of Object.values(r.bundles || {})) ingestBundle(target.connectionId, profile, b, r)
  return true
}

/** One pushed batch or heartbeat: apply it when it continues our revision chain, else catch up. Exported for tests. */
export function onFeedEvent(ev) {
  const p = ev?.payload
  if (!p || typeof p.feed !== 'string') return
  // Matched on the gateway's own name for its profile (it may call it 'custom' where the Desktop says 'default').
  const st = [...feeds.values()].find(f => !f.unsupported && f.feed === p.feed && (f.feedProfile || 'default') === (p.profile || 'default'))
  if (!st) return // a gateway or profile this window did not register with: the next catch-up covers it
  const target = { connectionId: st.connectionId, profile: st.profile, sessions: st.sessions }
  if (p.epoch !== st.epoch) { void feedCatchUp(target, { reset: true }); return }
  const rev = Number(p.rev)
  const wasLive = feedIsLive(st)
  if (!wasLive) feedLog(`${st.connectionId}/${st.profile} live rev=${rev} ${p.heartbeat ? 'heartbeat' : 'push'} vouched=${(p.fresh || []).length}`)
  if (p.heartbeat) {
    if (rev === st.rev) {
      confirmFeed(st, p.fresh)
      // Pushes just started reaching us: what the catch-up gave before was untrusted; fetch it all again, trusted.
      if (!wasLive) void feedCatchUp(target, { full: true })
    } else if (rev > st.rev) void feedCatchUp(target)
    return
  }
  if (rev <= st.rev) return // already have it (a catch-up answered first)
  if (Number(p.prev) !== st.rev || (Array.isArray(p.sessions) && p.sessions.length)) { void feedCatchUp(target); return }
  st.rev = rev
  for (const b of Object.values(p.bundles || {})) ingestBundle(st.connectionId, st.profile, b, p)
  confirmFeed(st, p.fresh)
  if (!wasLive) void feedCatchUp(target, { full: true })
}

export const PEEK_SLICE = 50
/** Read `target.sessions`' current bundles without touching the connection's shared cursor: the answer is applied
 *  per chat, the revision chain stays where pushes and catch-ups left it. Not queued behind a catch-up in flight
 *  (a click never waits for a sidebar-wide read). True when applied, false when the call failed, null when there is
 *  no registered feed to read against or it changed (the caller catches up instead). */
export async function feedPeek(target, { timeoutMs = 5_000 } = {}) {
  const bridge = typeof window !== 'undefined' ? window.hermesDesktop?.api : null
  if (!bridge || !target?.connectionId || !target.sessions?.length) return false
  const profile = target.profile || 'default'
  const key = connKey(target.connectionId, profile)
  const st = feeds.get(key)
  if (!st || st.unsupported || !st.epoch) return null
  let r
  try {
    r = await bridge({ path: FEED_PATH, method: 'POST', connectionId: target.connectionId, profile, timeoutMs,
      body: { sessions: target.sessions.slice(0, 200), since: 0, epoch: st.epoch, response_encoding: 'gzip' } })
  } catch (e) {
    log('feed peek failed', target.connectionId, e?.message || e)
    return false
  }
  const cur = feeds.get(key)
  if (!r || r.profile_unsupported || typeof r.epoch !== 'string' || r.reset || !cur || cur.unsupported || cur.epoch !== r.epoch || cur.feed !== r.feed) return null
  // Answered from a revision older than one already applied here: it may only fill chats that have no model yet.
  const older = Number(r.rev) < cur.rev
  for (const b of Object.values(r.bundles || {})) {
    if (older && cachedModel({ connectionId: target.connectionId, profile, session: b?.session })) continue
    ingestBundle(target.connectionId, profile, b, r)
  }
  cur.sessions = [...new Set([...(cur.sessions || []), ...target.sessions])]
  return true
}

/** Catch up every gateway once (the safety net, on load, and when the sidebar changes). A chat that joined the
 *  watched set since the last pass is read on its own: the cursor-bound catch-up returns only what changed after
 *  our revision, and its bundle may be older than that. */
export async function feedPass() {
  const done = []
  for (const t of feedTargets()) {
    const key = connKey(t.connectionId, t.profile), before = feeds.get(key)
    if (!(await feedCatchUp(t))) continue
    done.push(t)
    const st = feeds.get(key)
    if (!before || before.unsupported || !st || st.unsupported || before.epoch !== st.epoch || before.feed !== st.feed) continue
    const known = new Set(before.sessions || [])
    const joined = t.sessions.filter(s => !known.has(s) && !cachedModel({ connectionId: t.connectionId, profile: t.profile, session: s }))
    for (let i = 0; i < joined.length; i += PEEK_SLICE) await feedPeek({ ...t, sessions: joined.slice(i, i + PEEK_SLICE) })
  }
  return done
}
export const SIDEBAR_SETTLE_MS = 500
/** Re-register when the sidebar's set of chats changes (a new chat, another gateway's list arriving), so a chat is
 *  warm before its first click instead of at the next minute's pass. Activity re-ordering alone does nothing.
 *  Returns the unsubscribe. */
export function watchSidebar(rowsAtom = host?.sessions?.rows, { pass = feedPass, setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
  if (typeof rowsAtom?.subscribe !== 'function') return () => {}
  const names = () => warmTargets((() => { try { return rowsAtom.get?.() || [] } catch { return [] } })()).map(routeKey).sort().join('\n')
  let last = names(), timer = null
  const off = rowsAtom.subscribe(() => {
    if (timer != null) return
    timer = setTimer(() => {
      timer = null
      const now = names()
      if (now === last) return
      last = now
      if (typeof document === 'undefined' || !document.hidden) void pass()
    }, SIDEBAR_SETTLE_MS)
  })
  return () => { if (timer != null) clearTimer(timer); if (typeof off === 'function') off() }
}
/** A focused chat painted from cache while its feed does not vouch for it (no pushes reach this window, or the
 *  gateway has not confirmed it): read it once in the background. The paint never waits for this. */
export function feedReconcile(route) {
  if (!route?.connectionId || !route?.session || !cachedModel(route) || feedVouches(route)) return null
  return feedPeek({ connectionId: route.connectionId, profile: route.profile || 'default', sessions: [route.session] })
}
export const FEED_FOCUS = { delays: [0, 250, 750], retryMs: [2_000, 5_000, 10_000, 15_000] }
/** Hydrate the focused model, not every warm chat. Registration is not proof of a model:
 *  a failed backend read may return an empty bundle map after successfully recording interest.
 *  Retry that cold path a few times; a peek timeout or missing feed falls through to a full catch-up.
 *  The panel keeps retrying with backoff while focused. Transport serialization still bounds one request
 *  per connection. */
export async function feedFocus(route, { wait = ms => new Promise(r => setTimeout(r, ms)), active = () => true, delays = FEED_FOCUS.delays } = {}) {
  if (!route?.connectionId || !route?.session) return false
  for (const delay of delays) {
    if (delay) await wait(delay)
    if (!active()) return false
    if (cachedModel(route)) return true
    const st = feeds.get(connKey(route.connectionId, route.profile))
    if (st?.unsupported) return false
    // One chat, read beside the shared cursor (never advancing it, never queued behind a sidebar-wide catch-up). A
    // gateway that holds no bundle yet answers empty at once, computes it first and pushes it. With no feed
    // registered yet (or a changed one) this is a full catch-up: the cursor is shared by every watched chat, so
    // its coverage is preserved at execution time. A peek timeout is not proof the chat has no bundle.
    const target = { connectionId: route.connectionId, profile: route.profile || 'default', sessions: [route.session] }
    if ((await feedPeek(target)) !== true) await feedCatchUp(target, { full: true, preserveCoverage: true })
    if (cachedModel(route)) return true
  }
  return false
}


// Hub PanelModel v1 presentation and owner-qualified decisions.
const modelCache = new Map() // exact connection/profile/stored-session scope
const modelSubs = new Set()
const modelChanged = () => modelSubs.forEach(fn => fn())
export const cachedModel = route => modelCache.get(routeKey(route)) || null
const MODEL_SECTIONS = [
  ['needs_you', 'Needs you'], ['stuck', 'Stuck'], ['running', 'In progress'],
  ['waiting', 'Waiting'], ['ready', 'Ready'], ['done', 'Done']
]
const modelSmall = { fontSize: 12, lineHeight: 1.5, overflowWrap: 'anywhere' }

export const validModel = model => model?.version === 1 && !!model.header?.counts && Array.isArray(model.header?.pips) &&
  MODEL_SECTIONS.every(([section]) => Array.isArray(model.sections?.[section]))

// Palette and shapes carried over from the pre-hub panel so the cutover keeps the same look: one coloured box per
// state (header pips and row markers read as one legend), red-edged ask cards, accent-filled recommended option.
// Text colours keep >= 4.5:1 contrast on both themes via light-dark(); ACCENT_FILL carries white text.
export const SECTION_COLOR = { needs_you: '#f85149', stuck: '#f0883e', running: '#3b82f6', waiting: '#d29922', ready: '#3fb950', done: '#8b949e' }
// Light blue: work waiting on CI or a deploy (row `wait_kind`, status reason `linked_work_delivery`). Not amber
// (nothing is wrong) and not grey (something is actively producing the result).
export const DELIVERY_COLOR = '#38bdf8'
// Purple: merged work waiting for a hub deploy (`wait_kind` deploy), so it reads apart from CI/clock/card waits.
export const DEPLOY_COLOR = '#a371f7'
/** Backend-selected producer wait; administrative agent decisions use ordinary blue. Live authority only (ETA). */
export const deliveryWait = item => ['ci', 'deploy', 'clock', 'card'].includes(item?.wait_kind)
/** The one wait colour category a row box and the numbered waiting square share: the live `wait_kind`, else the
 *  `last_wait_kind` a stale/unknown row last showed (colour only: no ETA, no header dot, no live producer claim). */
export const waitCategory = item => item?.wait_kind || item?.last_wait_kind || null
export const waitColor = item => {
  const kind = waitCategory(item)
  return kind === 'agent' ? SECTION_COLOR.running : kind === 'deploy' ? DEPLOY_COLOR
    : ['ci', 'clock', 'card'].includes(kind) ? DELIVERY_COLOR : SECTION_COLOR.waiting
}
const ACCENT_TEXT = 'light-dark(#0969da, #58a6ff)', ALERT_TEXT = 'light-dark(#cf222e, #ff7b72)', ACCENT_FILL = '#0969da'
const muted = { color: 'var(--muted-foreground)', fontSize: 12 }
const box = (section, row) => ({ width: 9, height: 9, borderRadius: 2, boxSizing: 'border-box', flex: 'none',
  ...(row?.attention === 'triage_overdue' ? { background: SECTION_COLOR.needs_you }
    : section === 'waiting' ? { border: `1.5px solid ${waitColor(row)}` } : { background: SECTION_COLOR[section] || SECTION_COLOR.done }) })
// Section -> shared card-square category (Desktop `CardCountSquare`), matching the row boxes' colours.
const SECTION_CATEGORY = { needs_you: 'red', stuck: 'orange', running: 'blue', ready: 'green', done: 'grey' }
export const pipCategory = pip => pip.attention === 'triage_overdue' ? 'red' : pip.section !== 'waiting' ? SECTION_CATEGORY[pip.section] || 'grey'
  : waitCategory(pip) === 'deploy' ? 'violet' : ['agent', 'ci', 'clock', 'card'].includes(waitCategory(pip)) ? 'blue' : 'amber'
// Row-box partitions, the same as the hub's `section_pips`: overdue triage red on any row, waiting rows by wait
// category, everything else the section colour. Fixed order so the hub and client expiry agree.
const WAIT_PARTITION = { agent: 'blue', ci: 'blue', clock: 'blue', card: 'blue', deploy: 'violet' }
const PARTITION_ORDER = ['red', 'blue', 'violet', 'plain']
const rowPartition = (section, row) => row?.attention === 'triage_overdue' ? 'red'
  : section === 'waiting' ? WAIT_PARTITION[waitCategory(row)] || 'plain' : 'plain'
/** One numbered square per row-box colour, each displayed row counted once. `count` is the section total
 *  (the hub's value adjusted for withdrawn rows); any rows it counts beyond those shown read as plain. */
export function sectionPips(section, rows, count = rows.length, label = section) {
  const parts = {}
  for (const row of rows) (parts[rowPartition(section, row)] ||= []).push(row)
  const extra = count - rows.length
  if (extra > 0) (parts.plain ||= []).push(...Array(extra).fill(null))
  return PARTITION_ORDER.filter(part => parts[part]?.length).map(part => {
    const members = parts[part], pip = { section, label, count: members.length }
    if (part === 'red') pip.attention = 'triage_overdue'
    if (part === 'blue' || part === 'violet') {
      const kinds = members.map(waitCategory), kind = kinds.includes('agent') ? 'agent' : kinds[0]
      pip[members.every(row => row.wait_kind) ? 'wait_kind' : 'last_wait_kind'] = kind
    }
    return pip
  })
}
const sectionsPips = (sections, counts, sourcePips = []) => MODEL_SECTIONS.filter(([key]) => counts[key] > 0).flatMap(([section, label]) =>
  sectionPips(section, sections[section], counts[section], sourcePips.find(p => p.section === section)?.label || label))
// Same look as the Desktop's shared numbered card square, for Desktops whose SDK predates `CardCountSquare`.
const SQUARE_BORDER = { red: '#ef4444', orange: '#f97316', green: '#10b981', amber: '#f59e0b', blue: '#38bdf8', grey: '#71717a', violet: '#8b5cf6' }
/** One numbered card square: the count inside an outline in the category's colour. */
export function CountSquare({ category, count }) {
  if (typeof sdk.CardCountSquare === 'function') return jsx(sdk.CardCountSquare, { category, count })
  return jsx('span', { 'data-card-colour': category, style: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
    boxSizing: 'border-box', height: 16, minWidth: 16, padding: '0 2px', borderRadius: 2, flex: 'none', fontSize: 10, lineHeight: 1,
    fontVariantNumeric: 'tabular-nums', color: 'var(--ui-text-secondary, var(--muted-foreground))', fontWeight: 400,
    border: `1px solid ${SQUARE_BORDER[category] || SQUARE_BORDER.grey}` }, children: count })
}
const ghostBtn = { minHeight: 26, padding: '2px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'transparent', color: 'var(--foreground)', font: 'inherit', fontSize: 12, whiteSpace: 'nowrap' }
const PANEL_CSS = `[data-session-panel] [data-choice-letter]:not(:disabled):hover{background:color-mix(in srgb,var(--foreground) 5%,transparent)}
[data-session-panel] [data-choice-letter][data-recommended-row]:not(:disabled):hover{background:color-mix(in srgb,${ACCENT_FILL} 15%,transparent)}
[data-session-panel] [data-choice-letter]:disabled{cursor:default;opacity:.6}
[data-session-panel] [data-line-row]{border-radius:5px}[data-session-panel] [data-line-row]:hover{background:color-mix(in srgb,var(--foreground) 5%,transparent)}
[data-session-panel] summary{cursor:pointer}`
const STATE_LABEL = { decision: 'needs your decision', needs_input: 'needs you', needs_user: 'needs you', working: 'working', done: 'done', stuck: 'stuck', idle: 'idle' }

/** Pure model -> React tree. No parsing, classification, I/O, clock or ambient state. */
/** Rows that reopen a collapsed panel: any ask or stuck item not already seen when it was collapsed. */
export const attentionKeys = model => validModel(model) ? ['needs_you', 'stuck'].flatMap(section => model.sections[section].map(row => str(row.key))) : []
const COLLAPSED_KEY = 'hermes.sessionPanel.collapsed.v2'
/** Collapsed state for one chat: the attention keys present when the user collapsed it, or null (expanded). */
export function readCollapsed(scope) {
  try { const all = JSON.parse(window.localStorage.getItem(COLLAPSED_KEY) || '{}'); return Array.isArray(all[scope]) ? all[scope] : null } catch { return null }
}
export function writeCollapsed(scope, keys) {
  try {
    const all = JSON.parse(window.localStorage.getItem(COLLAPSED_KEY) || '{}')
    if (keys) all[scope] = keys; else delete all[scope]
    // Bounded: keep the 200 most recently written chats.
    const entries = Object.entries(all).slice(-200)
    window.localStorage.setItem(COLLAPSED_KEY, JSON.stringify(Object.fromEntries(entries)))
  } catch {}
}
/** Collapsed only while every current ask/stuck row was already present when the user collapsed the panel. */
export const isCollapsed = (model, stored) => !!stored && attentionKeys(model).every(k => stored.includes(k))

/** Only display text ages locally. Never refresh evidence, classify rows or read the network. */
export function formatClockText(parts, now = Date.now() / 1000) {
  const duration = value => {
    const n = Math.max(0, Math.floor(value)), m = Math.floor(n / 60), h = Math.floor(n / 3600), d = Math.floor(n / 86400)
    return d ? `${d}d${h % 24 ? ` ${h % 24}h` : ''}` : h ? `${h}h${m % 60 ? ` ${m % 60}m` : ''}` : m ? `${m}m` : `${n}s`
  }
  return Array.isArray(parts) ? parts.map(p => {
    if (typeof p === 'string') return p
    if (!p || !Number.isFinite(p.timestamp) || p.timestamp <= 0) return ''
    const age = Math.max(0, Math.floor(now - p.timestamp))
    if (p.format === 'elapsed') return duration(age)
    if (p.format === 'remaining') return duration(p.timestamp - now)
    if (p.format === 'ago') return age < 60 ? 'just now' : age < 3600 ? `${Math.floor(age / 60)} min ago` : age < 86400 ? `${Math.floor(age / 3600)} h ago` : `${Math.floor(age / 86400)} d ago`
    return ''
  }).join('') : ''
}
export function clockRow(row, now = Date.now() / 1000) {
  const result = { ...row }
  for (const key of ['detail', 'nested_detail', 'text']) if (row.clock_text?.[key]) result[key] = formatClockText(row.clock_text[key], now)
  if (row.clock_text?.details) result.details = row.clock_text.details.map(parts => formatClockText(parts, now))
  if (row.decision) result.decision = clockRow(row.decision, now)
  const reason = row.non_running_reason
  if (reason && reason.freshness === 'current' && row.attention !== 'triage_overdue') {
    const at = reason.observed_at, until = reason.valid_until
    if (!Number.isFinite(at) || !Number.isFinite(until) || !Number.isFinite(now) || now < at || now >= Math.min(until, at + 180)) {
      result.non_running_reason = { freshness: 'unknown', disposition: 'unknown', actor: 'unknown',
        observed_at: at, valid_until: until, unknown_reason: 'dispatcher_evidence_stale' }
      result.reason = 'reason-unknown'
      result.detail = 'ready · execution reason unknown'
      result.evidence_state = 'unknown'
      result.details = ['Reason freshness: unknown', 'The dispatcher evidence has expired; inspect fresh authoritative evidence.']
      delete result.clock_text
    }
  }
  return result
}
/** Withdraw expired row presentation, never rewrite canonical status or source clocks. */
function expiryPresentation(model, sections, diagnosticExpired = false, groups = model.groups) {
  const counts = { ...model.header.counts }
  for (const [key] of MODEL_SECTIONS) counts[key] = Math.max(0, (counts[key] || 0) + sections[key].length - model.sections[key].length)
  const delivery = sections.waiting.length > 0 && sections.waiting.every(row => row.wait_kind)
  const waitKind = delivery ? (sections.waiting.some(row => row.wait_kind === 'agent') ? 'agent' : sections.waiting[0].wait_kind) : null
  // Overdue triage is fresh operational red: it survives expiry only on rows or
  // shared-blocker groups that still claim it (a group alone can own the header's red).
  const overdue = [...sections.stuck, ...(Array.isArray(groups) ? groups : [])].some(row => row?.attention === 'triage_overdue')
  // Recompute every square from the effective rows (one per row-box colour); never carry the snapshot's pips.
  const pips = sectionsPips(sections, counts, model.header.pips)
  const withdrawnDerived = diagnosticExpired && model.header.source === 'derived' && !model.header.status &&
    !counts.needs_you && !counts.stuck && !counts.running
  const { attention, ...header } = model.header
  const label = !overdue && attention === 'triage_overdue' ? str(header.label).replace(/^overdue triage · /, '') : header.label
  return { ...model, sections, ...(Array.isArray(groups) ? { groups } : {}), header: { ...header, label, counts, pips, wait_kind: waitKind, wait_eta: null,
    ...(overdue && attention ? { attention } : {}),
    ...(withdrawnDerived ? { state: 'unknown', label: 'execution reason unknown' } : {}) } }
}
/** Expiration can withdraw diagnostic attention, never create execution or human authority. */
export function expireDiagnostics(model, now) {
  let sections = null
  for (const [key, rows] of Object.entries(model.sections || {})) {
    for (const row of rows) {
      if (!row.non_running_reason || row.non_running_reason.freshness !== 'current') continue
      const clocked = clockRow(row, now)
      if (clocked.non_running_reason?.freshness !== 'unknown') continue
      sections ||= Object.fromEntries(Object.entries(model.sections).map(([k, v]) => [k, [...v]]))
      sections[key] = sections[key].filter(v => v !== row)
      sections.ready.push(clocked)
    }
  }
  return sections ? expiryPresentation(model, sections, true) : model
}
/** One source clock for header and row freshness; heartbeats cannot renew computed_at. */
export function modelFreshness(model, { now = Date.now() / 1000, vouched = null } = {}) {
  const f = model?.freshness
  if (!f) return { stale: vouched === false, age: null } // older producers
  const age = Number.isFinite(f.computed_at) ? now - f.computed_at : null
  const ttl = Number.isFinite(f.ttl_s) && f.ttl_s > 0 ? Math.min(60, f.ttl_s) : 60
  return { stale: age === null || age < 0 || age > ttl || vouched === false || (vouched !== true && f.trusted !== true), age }
}
/** Cached native task waits lose attention/action authority with their source snapshot. */
export function expireNativeWaits(model, now, vouched) {
  if (!modelFreshness(model, { now, vouched }).stale) return model
  const sections = Object.fromEntries(Object.entries(model.sections).map(([key, rows]) => [key, []]))
  for (const [key, rows] of Object.entries(model.sections)) for (const row of rows) {
    if (key === 'done' || row.kind !== 'card' || row.non_running_reason) {
      const { attention: _stale, ...kept } = row  // a stale snapshot never claims fresh operational red
      sections[key].push(row.attention === 'triage_overdue' ? kept : row)
      continue
    }
    const { attention: _overdue, last_wait_kind: _category, ...old } = clockRow(row, now)  // stale evidence never claims fresh operational red
    sections.waiting.push({ ...old, reason: 'source-stale', evidence_state: 'unknown',
      // A row the reducer already made historical keeps its one "status unknown · last known:" prefix.
      detail: old.retained_disposition?.text
        ? `${old.retained_disposition.text} · last recorded disposition; current status unknown`
        : /^status unknown\b/.test(str(old.detail)) ? old.detail
          : `status unknown · last known: ${key === 'running' && old.detail ? `running · ${old.detail}` : old.detail || key}`,
      details: ['Task source freshness: stale; read current authoritative task state before acting.'],
      actions: row.actions.filter(a => ['open_card', 'open_session', 'open_url', 'open_process'].includes(a.kind)),
      options: [], decision: old.decision, wait_kind: null, wait_eta: null, clock_text: null,
      // Last-known category is colour only (no ETA, no header dot): a row last seen running keeps its blue box
      // instead of turning amber just because the feed is late.
      ...(key === 'waiting' && waitCategory(row) ? { last_wait_kind: waitCategory(row) }
        : key === 'running' ? { last_wait_kind: 'agent' } : {}) })
  }
  // Shared-blocker groups come from the same stale snapshot: they lose overdue red
  // too and keep only explicitly historical text.
  const groups = Array.isArray(model.groups) ? model.groups.map(group => {
    if (group?.attention !== 'triage_overdue') return group
    const { attention: _overdue, ...old } = group
    return { ...old, section: 'waiting', reason: 'source-stale', evidence_state: 'unknown',
      detail: `status unknown · last known: ${old.detail || 'stuck'}`,
      details: ['Task source freshness: stale; read current authoritative task state before acting.'],
      wait_kind: null, wait_eta: null, clock_text: null }
  }) : model.groups
  return expiryPresentation(model, sections, false, groups)
}
/** The three right-click entries for a row, in order. `action` is null when the row has nothing to open there
 *  (no owning chat, no dependency, or the owning chat is the one already in focus). */
export function rowMenuItems(row, currentSession = null) {
  const actions = Array.isArray(row?.actions) ? row.actions : []
  const cards = actions.filter(a => a?.kind === 'open_card' && a.target?.id)
  const own = cards.find(a => str(a.target.id) === str(row.id)) || null
  // Older producers label the dependency "Open parent card" without a role: any other card target is it.
  const dependency = cards.find(a => a.role === 'dependency') || cards.find(a => a !== own) || null
  const session = actions.find(a => a?.kind === 'open_session' && str(a.target?.session)) || null
  return [
    { key: 'session', label: 'Open session', action: session && str(session.target.session) !== str(currentSession) ? session : null },
    { key: 'card', label: 'Open card', action: own },
    { key: 'dependency', label: 'Open dependency', action: dependency },
    ...actions.filter(a => a?.kind === 'open_process' && a.target?.id).map(action => ({key: 'process', label: 'Open process', action}))
  ]
}
/** Owner-qualified identity of a bound ask row: the same [gateway, profile, request id] key user-steps uses. */
const boundAsk = row => {
  if (!row?.request_id || !Number.isSafeInteger(row.request_revision)) return null
  const a = (row.actions || []).find(x => x?.kind === 'decide' && x.request_id === row.request_id && x.revision === row.request_revision && str(x.owner?.gatewayId))
  return a ? JSON.stringify([a.owner.gatewayId, a.owner.profile || 'default', row.request_id]) : null
}
/** Every bound ask the model carries: what a collapsed header still counts. */
export const modelAsks = model => Object.fromEntries((model?.sections?.needs_you || []).filter(r => r?.kind !== 'blocker')
  .map(r => [boundAsk(r), r.request_revision]).filter(([k]) => k))
// One combined height budget for the session-status frame (rows + decision companion). The header never scrolls.
export const PANEL_MAX_HEIGHT = '55vh'
const frameStyle = { minWidth: 0, width: '100%', color: 'var(--foreground)', maxHeight: PANEL_MAX_HEIGHT, minHeight: 0,
  display: 'flex', flexDirection: 'column', overflow: 'hidden', boxSizing: 'border-box',
  borderBottom: '1px solid color-mix(in srgb, var(--border) 60%, transparent)', userSelect: 'text', WebkitUserSelect: 'text' }
const bodyStyle = { minHeight: 0, minWidth: 0, flex: '1 1 auto', overflowY: 'auto', overflowX: 'hidden', overscrollBehavior: 'contain' }
/** A frame with no model header (loading, unsupported, empty model): still bounded, still scrolls inside. */
export function bodyFrame(...children) {
  return jsx('section', { 'data-session-panel': '', 'data-panel-frame': 'bare', 'aria-label': 'Session status', style: { ...frameStyle, padding: '6px 12px 8px' },
    children: jsxs('div', { 'data-panel-body': '', style: bodyStyle, children }) })
}
const emptyCoverage = state => ({ state, collapsed: false, drawn: {}, counted: {} })
/** No rows to show: the companion stays mounted (it keeps polling for a new ask) inside a frame that paints nothing
 *  (no padding, no border) until the companion has something to draw. */
function companionFrame(companion) {
  const queue = companion?.(emptyCoverage('empty'))
  if (!queue) return null
  const us = typeof window !== 'undefined' ? window.__userSteps : null
  if (typeof us?.queueVisible !== 'function' || us.queueVisible()) return bodyFrame(queue)
  // Not a landmark: an unpainted idle frame must not announce an empty "Session status" region.
  return jsx('div', { 'data-session-panel': '', 'data-panel-frame': 'idle', style: { ...frameStyle, borderBottom: 0, padding: 0 },
    children: jsx('div', { 'data-panel-body': '', style: bodyStyle, children: queue }) })
}
export function renderModel(model, { onChoose, onAction, decisionStatus = () => null, onCheck, collapsed = false, onToggle, now = Date.now() / 1000, vouched = null, currentSession = null, companion = null } = {}) {
  if (!validModel(model)) return companionFrame(companion)
  model = expireNativeWaits(expireDiagnostics(model, now), now, vouched)
  // Spec: hidden when there is neither open work nor finished history.
  if (!MODEL_SECTIONS.some(([section]) => model.sections[section].length)) return companionFrame(companion)
  // Asks this render actually draws with their options (exact owner/request/revision), for the companion.
  const drawn = {}
  const freshness = modelFreshness(model, { now, vouched })
  const status = freshness.stale ? null : model.header.status
  const hasHeader = status || model.header.source === 'derived'
  const state = !freshness.stale && hasHeader ? str(model.header.state) : 'unknown'
  // Vouch exclusion/TTL expiry remove current trust, not the last backend-authored state.
  // This is historical display only: effective state/stripe stay unknown/neutral. Never
  // describe a missing or future source clock as a successful sync (including "0s ago").
  const dated = Number.isFinite(freshness.age) && freshness.age >= 0 && model.freshness?.computed_at > 0
  const lastSeen = freshness.stale && dated && model.header.source && str(model.header.state) && model.header.state !== 'unknown'
  const ageText = dated ? formatClockText([{ timestamp: model.freshness.computed_at, format: 'elapsed' }], now) : null
  const label = freshness.stale
    ? lastSeen ? `${str(model.header.label) || model.header.state} · synced ${ageText} ago` : `status unknown (stale${ageText === null ? '' : ` · ${ageText} old`})`
    : hasHeader ? str(model.header.label) || state : 'unknown'
  const stripe = freshness.stale ? 'none' : model.header.attention === 'triage_overdue' ? 'stuck' : status?.presentation?.stripe || 'attention'
  // Presentation palette only: no local activity/count/kind classification.
  const statusColor = { done: 'var(--color-emerald-500)', stuck: 'var(--color-red-500)', attention: 'var(--color-amber-500)',
    blocked: 'var(--color-zinc-400)', waiting: hasHeader && model.header.wait_kind ? waitColor(model.header) : 'var(--color-zinc-400)', none: 'var(--muted-foreground)' }[stripe]
  const details = row => row.details?.length > 0 && jsxs('details', { 'data-ask-details': '', style: { ...modelSmall, ...muted, flex: 1, minWidth: 0 }, children: [
    jsx('summary', { style: { fontSize: 11.5 }, children: 'Details' }),
    ...row.details.map((text, i) => jsx('div', { style: { whiteSpace: 'pre-wrap', marginTop: 4, fontSize: 11.5 }, children: text }, i))
  ] })
  // A line row's own card opens by double-clicking its state square, so its links skip that action.
  const ownCard = row => row.actions.find(action => action.kind === 'open_card' && str(action.target?.id) === str(row.id))
  // Card/dependency/session opens live in the row's right-click menu (rowMenuItems); only an ask row keeps its own
  // Open card button, and web links (CI, PR) stay buttons.
  const openLinks = (row, ...skip) => row.actions.filter(action => !skip.includes(action) && ((action.kind === 'open_card' && action === ownCard(row)) || (action.kind === 'open_url' && /^https?:\/\//.test(str(action.url))))).map((action, i) => jsx('button', { type: 'button', 'data-open-card': action.kind === 'open_card' ? '' : undefined, 'data-open-url': action.kind === 'open_url' ? '' : undefined,
    style: { ...askLinkBtn, fontSize: 11.5, whiteSpace: 'nowrap', cursor: onAction ? 'pointer' : 'default' }, disabled: !onAction, onClick: () => onAction?.(row, action), children: action.label }, `open${i}`))
  // Right-click menu: Open session / Open card / Open dependency. Absent SDK menu (older host) = no menu.
  const withMenu = (row, element) => {
    const { ContextMenu, ContextMenuTrigger, ContextMenuContent, ContextMenuItem } = sdk
    if (!onAction || !ContextMenu || !ContextMenuTrigger || !ContextMenuContent || !ContextMenuItem) return element
    const items = rowMenuItems(row, currentSession)
    if (!items.some(item => item.action)) return element
    return jsxs(ContextMenu, { children: [
      jsx(ContextMenuTrigger, { asChild: true, children: element }),
      jsx(ContextMenuContent, { 'data-row-menu': row.key, children: items.map(item => jsx(ContextMenuItem, {
        'data-row-menu-item': item.key, disabled: !item.action, onSelect: () => item.action && onAction(row, item.action), children: item.label }, item.key)) })
    ] }, row.key)
  }
  const askRow = (row, hidden = false) => {
    const bound = boundAsk(row)
    if (bound && !hidden) drawn[bound] = row.request_revision
    const status = decisionStatus(row)
    const enabled = option => !!onChoose && !!row.request_id && !!option.choice_id && row.actions.some(a => a.kind === 'decide' && a.owner && a.request_id === row.request_id && a.revision === row.request_revision) && !status?.locked
    const decision = row.options.length > 0
    return withMenu(row, jsxs('article', { 'data-panel-row': row.key, 'data-ask-row': row.source, 'data-ask-binding': bound || undefined,
      style: { border: '1px solid var(--border)', borderLeft: `3px solid ${SECTION_COLOR.needs_you}`, borderRadius: 8, padding: '10px 12px', marginBottom: 8, minWidth: 0, display: 'grid', gap: 8 }, children: [
      jsxs('div', { style: { display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0 }, children: [
        jsx('span', { 'data-ask-kind': '', style: { fontSize: 10.5, fontWeight: 700, letterSpacing: 0.5, textTransform: 'uppercase', color: ALERT_TEXT, whiteSpace: 'nowrap' }, children: decision ? 'Needs your decision' : 'Needs you' }),
        row.id && jsx('span', { 'data-ask-card': '', style: { ...muted, marginLeft: 'auto', fontSize: 10.5, fontFamily: 'var(--font-mono, ui-monospace, monospace)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 }, children: row.source === 'card' ? row.id : 'question' })
      ] }),
      jsx('strong', { 'data-ask-title': '', style: { fontWeight: 600, fontSize: 14, lineHeight: 1.35, overflowWrap: 'anywhere', marginTop: -4 }, children: plain(row.title) || row.id }),
      row.detail && jsx('div', { style: { ...modelSmall, ...muted }, children: row.detail }),
      row.decision?.text && jsx('div', { 'data-decision-status': row.decision.request_id || '', role: 'status', style: { ...muted, overflowWrap: 'anywhere' }, children: row.decision.text }),
      row.situation && jsx('div', { 'data-ask-situation': '', title: row.situation, style: { ...modelSmall, fontSize: 12.5, ...clamp(3) }, children: row.situation }),
      decision && jsx('div', { 'data-ask-options': '', style: { display: 'grid', gap: 6 }, children: row.options.map(option => {
        const rec = !!option.recommended, on = enabled(option)
        return jsxs('button', { type: 'button', 'data-choice-letter': option.letter, ...(rec ? { 'data-recommended-row': '' } : {}), disabled: !on,
          'aria-label': `Choose option ${option.letter}: ${option.name}`, onClick: () => onChoose?.(row, option),
          style: { textAlign: 'left', font: 'inherit', color: 'inherit', border: `1px solid ${rec ? ACCENT_FILL : 'var(--border)'}`, borderRadius: 6, padding: '8px 10px',
            background: rec ? `color-mix(in srgb, ${ACCENT_FILL} 9%, transparent)` : 'transparent', display: 'grid', gridTemplateColumns: '22px minmax(0,1fr) auto', columnGap: 10, alignItems: 'start', cursor: on ? 'pointer' : 'default', minWidth: 0 }, children: [
          jsx('span', { 'data-option-badge': '', 'aria-hidden': true, style: { width: 22, height: 22, borderRadius: 999, display: 'grid', placeItems: 'center', fontSize: 11.5, fontWeight: 700, background: rec ? ACCENT_FILL : 'color-mix(in srgb, var(--foreground) 12%, transparent)', color: rec ? '#fff' : 'var(--foreground)' }, children: option.letter }),
          jsxs('span', { style: { minWidth: 0 }, children: [
            jsxs('span', { style: { display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }, children: [
              jsx('strong', { 'data-option-label': '', style: { fontWeight: 600, fontSize: 13 }, children: option.name }),
              rec && jsx('span', { style: pill(ACCENT_TEXT), children: 'Recommended' })
            ] }),
            jsx('span', { 'data-option-detail': '', title: option.detail, style: { display: 'block', fontSize: 12, lineHeight: 1.45, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', color: 'var(--muted-foreground)' }, children: option.detail }),
            rec && option.reason && jsx('span', { 'data-option-reason': '', style: { display: 'block', fontSize: 12, lineHeight: 1.45, marginTop: 3, color: ACCENT_TEXT }, children: `Why: ${option.reason}` })
          ] }),
          jsx('span', { 'aria-hidden': true, style: { ...ghostBtn, alignSelf: 'center', display: 'inline-flex', alignItems: 'center', ...(rec ? { background: ACCENT_FILL, borderColor: ACCENT_FILL, color: '#fff', fontWeight: 600 } : {}) }, children: `Choose ${option.letter}` })
        ] }, option.letter)
      }) }),
      row.only_you && jsx('div', { 'data-ask-only-you': '', style: { ...modelSmall, ...muted }, children: row.only_you }),
      row.source === 'card' && !row.request_id && decision && jsx('div', { role: 'status', style: { ...muted, fontSize: 11 }, children: 'Answer unavailable until the exact owner ask is mirrored. Open the card for details.' }),
      status?.text && jsx('div', { role: 'status', style: { ...modelSmall, color: status.locked ? 'var(--foreground)' : ALERT_TEXT }, children: status.text }),
      status?.locked && onCheck && jsx('button', { type: 'button', style: { ...ghostBtn, justifySelf: 'start', cursor: 'pointer' }, onClick: () => onCheck(row), children: status.retry ? 'Retry decision' : 'Check status' }),
      (row.details?.length > 0 || row.actions.some(a => a.kind === 'open_card')) && jsxs('div', { style: { display: 'flex', alignItems: 'flex-start', gap: 12, minWidth: 0 }, children: [
        details(row), ...openLinks(row)
      ] })
    ] }, row.key))
  }
  // Every other section: one compact line per row — state box, title, short detail; details/open on the right.
  // In progress keeps the old one-line shape: title, then where · model · how long right-aligned beside it.
  const lineRow = (row, section) => {
    // A finished row is already under "Done": its own `done`/`archived` label says nothing.
    const detail = section === 'done' && /^(done|archived)$/i.test(str(row.detail).trim()) ? '' : str(row.detail)
    // One line per row: title left, detail right. Only Stuck keeps its (orange) reason on a line of its own.
    const inline = (section !== 'stuck' || row.attention === 'triage_overdue') && !!detail
    const compact = section === 'waiting' || section === 'ready'
    const title = plain(row.title) || row.id
    const own = ownCard(row)
    const eta = section === 'waiting' && deliveryWait(row) && renderWaitEta({ ...row.wait_eta, wait_kind: row.wait_kind }, now,
      own ? { 'data-open-own-card': '', onDoubleClick: () => onAction?.(row, own) } : {})
    // The backend names the word of the detail that opens a link (`inline: "CI"`): that word is the link, no button.
    const word = action => {
      const w = str(action.inline).replace(/[^A-Za-z0-9 ]/g, '')
      const m = w && new RegExp(`(^|[^A-Za-z0-9_])(${w})(?![A-Za-z0-9_])`).exec(detail)
      return m ? { index: m.index + m[1].length, text: m[2] } : null
    }
    const linked = row.actions.find(action => action.kind === 'open_url' && /^https?:\/\//.test(str(action.url)) && word(action))
    const hit = linked && word(linked)
    const detailText = hit ? [detail.slice(0, hit.index),
      jsx('button', { type: 'button', 'data-open-url': '', 'data-inline-link': '', title: linked.label, disabled: !onAction, onClick: () => onAction?.(row, linked),
        style: { ...askLinkBtn, fontSize: 'inherit', padding: 0, cursor: onAction ? 'pointer' : 'default' }, children: hit.text }, 'inline'),
      detail.slice(hit.index + hit.text.length)] : detail
    const hover = [title, ...(row.details || [])].join('\n')
    return withMenu(row, jsxs('article', { 'data-panel-row': row.key, 'data-line-row': '',
      style: { display: 'grid', gridTemplateColumns: '12px minmax(0,1fr) auto', columnGap: 8, alignItems: 'start', padding: '3px 4px', fontSize: 13, minWidth: 0 }, children: [
      // The state box always draws (light blue for CI/clock, purple for deploy): the ETA readout sits at the far
      // right of a wide panel, so dropping the box left a delivery wait with no colour at all.
      jsx('span', { 'aria-hidden': true, 'data-row-pip': section, 'data-wait-kind': row.wait_kind || undefined, 'data-wait-category': (section === 'waiting' && waitCategory(row)) || undefined, ...(own ? { 'data-open-own-card': '', title: 'Double-click to open card', onDoubleClick: () => onAction?.(row, own) } : {}),
        style: { ...box(section, row), justifySelf: 'center', marginTop: 5, ...(own && onAction ? { cursor: 'pointer' } : {}) } }),
      jsxs('div', { style: { minWidth: 0 }, children: [
        jsxs('div', { style: { display: 'flex', alignItems: 'baseline', gap: 10, minWidth: 0, flexWrap: 'wrap', rowGap: 1 }, children: [
          jsx('strong', { 'data-ask-title': '', title: hover, style: { flex: 1, minWidth: 0, fontWeight: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: title }),
          inline && jsx('span', { 'data-row-detail': '', 'data-run-meta': '', title: row.details?.join('\n') || detail,
            // Runtime ends in an elapsed clock. An unshrinkable metadata line
            // can extend past the clipped panel even though its DOM text exists.
            // Wrap the entire detail when necessary; never ellipsize its clock.
            style: { ...muted, flex: '0 1 auto', ...(compact ? { maxWidth: '60%', overflow: 'hidden', textOverflow: 'ellipsis' } : { maxWidth: '100%', overflowWrap: 'anywhere' }), whiteSpace: compact ? 'nowrap' : 'normal', ...(section === 'waiting' || row.attention === 'triage_overdue' ? { flex: '0 0 100%', maxWidth: '100%', overflow: 'visible', whiteSpace: 'normal', overflowWrap: 'anywhere' } : {}), fontVariantNumeric: 'tabular-nums', ...(row.quiet ? { color: SECTION_COLOR.stuck } : {}) }, children: detailText })
        ] }),
        !inline && detail && jsx('div', { 'data-row-detail': '', style: { ...muted, overflowWrap: 'anywhere', ...(section === 'stuck' ? { color: SECTION_COLOR.stuck } : {}) }, children: detail }),
        // An answered ask: one status line on its own card ("Done · delivers when this turn ends"); gone once delivered.
        row.decision?.text && jsx('div', { 'data-decision-status': row.decision.request_id || '', role: 'status', style: { ...muted, overflowWrap: 'anywhere' }, children: row.decision.text })
      ] }),
      jsxs('span', { style: { display: 'flex', gap: 8, paddingTop: 2 }, children: [eta, ...openLinks(row, own, hit ? linked : null)] })
    ] }, row.key))
  }
  // The backend owns parent/depth. Keep classification/counts on the original
  // section while drawing each row only once, beneath its published parent.
  const entries = MODEL_SECTIONS.flatMap(([section]) => model.sections[section].map(row => ({ row, section })))
  const nodes = new Map(entries.map(entry => [entry.row.key, entry]))
  for (const row of model.groups || []) nodes.set(row.key, { row, section: row.section || 'waiting' })
  const children = new Map()
  for (const entry of entries) {
    const parent = entry.row.parent
    if (parent && nodes.has(parent)) children.set(parent, [...(children.get(parent) || []), entry])
  }
  // Rows under the closed "done" disclosure are not visibly drawn: they never count as presented asks.
  const drawTree = ({ row: raw, section }, visited = new Set(), hidden = section === 'done') => {
    if (visited.has(raw.key)) return null
    const row = clockRow(raw, now)
    if (raw.parent && nodes.has(raw.parent) && typeof row.nested_detail === 'string') row.detail = row.nested_detail
    const next = new Set([...visited, raw.key])
    return jsxs('div', { 'data-tree-node': row.key, 'data-tree-depth': row.depth || 0,
      'data-tree-parent': row.parent || undefined, children: [
      section === 'needs_you' && row.kind !== 'blocker' ? askRow(row, hidden) : lineRow(row, section),
      children.has(row.key) && jsx('div', { 'data-tree-guide': '', style: {
        // Desktop publishes --dt-border, not the legacy --border alias. Keep
        // geometry independent of color resolution so missing tokens cannot erase guides.
        marginLeft: 9, paddingLeft: 15, borderLeftWidth: 1, borderLeftStyle: 'solid',
        borderLeftColor: 'var(--dt-border, var(--border, #768390))', minWidth: 0
      }, children: children.get(row.key).map(child => drawTree(child, next, hidden)) })
    ] }, row.key)
  }
  const roots = new Map(MODEL_SECTIONS.map(([section]) => [section, []]))
  const placedGroups = new Set()
  for (const entry of entries) {
    if (!entry.row.parent || !nodes.has(entry.row.parent)) roots.get(entry.section).push(entry)
    else {
      const parent = nodes.get(entry.row.parent)
      if (parent.row.kind === 'blocker' && !placedGroups.has(parent.row.key)) {
        roots.get(entry.section).push(parent)
        placedGroups.add(parent.row.key)
      }
    }
  }
  return jsxs('section', { 'data-session-panel': '', 'data-hub-model': model.revision, 'aria-label': 'Session status',
    'data-collapsed': collapsed ? '' : undefined,
    // The frame never scrolls; only its body does, so the header (and its collapse control) stays reachable however
    // many rows and companion decisions the body holds.
    style: { ...frameStyle, padding: collapsed ? '6px 12px 4px' : '6px 12px 8px' }, children: [
    jsx('style', { 'data-panel-css': '', children: PANEL_CSS }),
    jsxs('header', { 'data-panel-header': '', style: { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', fontSize: 12, lineHeight: '18px', minWidth: 0, flexShrink: 0 }, children: [
      // The caret minimizes the panel to this header line; a new ask or stuck item reopens it.
      jsxs(onToggle ? 'button' : 'span', { ...(onToggle ? { type: 'button', 'data-panel-toggle': '', 'aria-expanded': !collapsed,
          'aria-label': collapsed ? 'Expand session status' : 'Collapse session status',
          title: collapsed ? 'Expand' : 'Collapse (reopens when something new needs you or gets stuck)', onClick: onToggle } : {}),
        style: { display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--muted-foreground)', background: 'transparent', border: 0, padding: 0, font: 'inherit', cursor: onToggle ? 'pointer' : 'default' }, children: [
        onToggle && jsx('span', { 'aria-hidden': true, 'data-panel-caret': '', style: { display: 'inline-block', width: 10, fontSize: 10, transform: collapsed ? 'rotate(-90deg)' : 'none', transition: 'transform 120ms' }, children: '\u25be' }),
        jsx('span', { 'aria-hidden': true, 'data-panel-status-stripe': stripe, 'data-panel-status-revision': status?.revision,
          style: { width: 7, height: 7, borderRadius: '50%', background: statusColor } }),
        jsx('span', { children: 'This session' })
      ] }),
      jsx('span', { 'data-session-status': '', title: STATE_LABEL[state] || state, style: { color: statusColor }, children: label }),
      // Header counts are numbered card squares (the Desktop's shared look); the section words are the tooltip.
      ...model.header.pips.map((pip, i) => jsx('span', { 'data-model-pip': pip.section, 'data-pip-category': pipCategory(pip), title: `${pip.count} ${pip.label}`, 'aria-label': `${pip.count} ${pip.label}`,
        style: { display: 'inline-flex' }, children: jsx(CountSquare, { category: pipCategory(pip), count: pip.count }) }, i)),
      // Counts are the hub's values, not row lengths, even if a test deliberately makes them disagree.
      ...MODEL_SECTIONS.map(([section]) => jsx('span', { 'data-model-count': section, hidden: true, children: model.header.counts[section] }, section))
    ] }),
    // Collapsing removes the rows and every ask the header still counts. The body itself stays visible: what the
    // header does not count (delivery Check status / Retry, an ask the model omits, a queue error) is drawn by the
    // companion and must not disappear behind a quiet header.
    jsxs('div', { 'data-panel-body': '', 'data-body-collapsed': collapsed ? '' : undefined, style: bodyStyle, children: [
    ...MODEL_SECTIONS.map(([section, label]) => {
      if (collapsed || !roots.get(section).length) return false
      const heading = jsx('h3', { 'data-section': section.replaceAll('_', '-'), style: { display: 'inline', fontSize: 11, fontWeight: 700, letterSpacing: 0.4, textTransform: 'uppercase', margin: 0, color: section === 'needs_you' ? ALERT_TEXT : section === 'stuck' ? SECTION_COLOR.stuck : 'var(--muted-foreground)' }, children: `${label} · ${model.header.counts[section]}` })
      const rows = roots.get(section).map(entry => drawTree(entry))
      // Finished history stays one click away (closed by default), as before the cutover.
      if (section === 'done') return jsxs('details', { 'data-model-section': section, style: { marginTop: 12 }, children: [
        jsx('summary', { style: { margin: '0 0 6px', listStylePosition: 'inside', color: 'var(--muted-foreground)' }, children: heading }), ...rows
      ] }, section)
      return jsxs('div', { 'data-model-section': section, children: [jsx('div', { style: { margin: '12px 0 6px' }, children: heading }), ...rows] }, section)
    }),
    // Evaluated after the sections above (array order), so `drawn` holds exactly the asks this render drew.
    companion?.({ state: 'presented', collapsed: !!collapsed, drawn: collapsed ? {} : { ...drawn }, counted: modelAsks(model) }) || null
    ] })
  ] })
}

const MODEL_INTENT_PREFIX = 'hermes.sessionPanel.decision.v1:'
const modelDecisions = new Map()
// Presentation-only receipt overlay. Never mutate the canonical cached model or
// infer executor state from a recorded choice. Exact source/owner/revision fences
// keep an unrelated ask (including another gateway's identical id) untouched.
const provisionalDecisions = new Map()
export function decisionPresentation(model, route) {
  if (!model) return model
  const moving = model.sections.needs_you.filter(row => [...provisionalDecisions.values()].some(p =>
    routeKey(p.sourceRoute) === routeKey(route) && p.key === modelDecisionKey(row)))
  if (!moving.length) return model
  const sections = { ...model.sections,
    needs_you: model.sections.needs_you.filter(row => !moving.includes(row)),
    waiting: [...model.sections.waiting, ...moving.map(row => ({ ...row, detail: '', options: [],
      actions: row.actions.filter(a => a.kind !== 'decide'),
      decision: { request_id: row.request_id, text: modelDecisionStatus(row)?.text || 'Decision recorded; updating status…' } }))] }
  const counts = { ...model.header.counts, needs_you: model.header.counts.needs_you - moving.length,
    waiting: model.header.counts.waiting + moving.length }
  const pips = sectionsPips(sections, counts, model.header.pips)
  return { ...model, sections, header: { ...model.header, counts, pips,
    ...(counts.needs_you === 0 ? { label: 'Decision recorded' } : {}) } }
}
const modelDecisionKey = row => JSON.stringify([row.actions.find(a => a.kind === 'decide')?.owner, row.request_id, row.request_revision])
export function modelDecisionStatus(row) {
  const key = modelDecisionKey(row)
  if (modelDecisions.has(key)) return modelDecisions.get(key)
  try {
    const intent = JSON.parse(window.localStorage.getItem(MODEL_INTENT_PREFIX + key) || 'null')
    if (intent?.notStarted) return { locked: true, retry: true, text: 'Decision was not started. Retry the same decision.' }
    if (intent) return { locked: true, text: 'Decision outcome unknown — check status before choosing again.' }
  } catch {}
  return null
}
const sameModelOwner = (a, b) => !!a && !!b && a.gatewayId === b.gatewayId && a.profile === b.profile && a.storedSessionId === b.storedSessionId
/** Resolve on click only. Never use an ambient gateway for an unverified foreign request. */
// A gateway that is restarting (update, reconnect) answers with a transport error: retry briefly instead of
// failing the click. A 4xx answer is definitive for that gateway.
const MODEL_ROUTE_RETRY_MS = [0, 1500, 4000]
const transientRouteError = e => !/^4\d\d\b/.test(String(e?.message || e).replace(/^Error invoking remote method '[^']*':\s*/, ''))
export async function modelRequestRoute(action, sourceRoute, call, { delays = MODEL_ROUTE_RETRY_MS } = {}) {
  let sourceError = null, anyError = null
  for (const wait of delays) {
    if (wait) await new Promise(r => setTimeout(r, wait))
    const candidates = [sourceRoute.connectionId]
    try { for (const c of await host.connections?.() || []) if (!candidates.includes(c.id)) candidates.push(c.id) } catch {}
    let retry = false
    for (const connectionId of candidates.filter(Boolean)) {
      const route = { connectionId, profile: action.owner.profile, session: action.owner.storedSessionId }
      try {
        const data = await call(`/api/plugins/user-steps/requests/${encodeURIComponent(action.request_id)}?session=${encodeURIComponent(route.session)}`, route)
        if (data?.request?.requestId === action.request_id && sameModelOwner(data.request.owner, action.owner)) return { route, request: data.request }
      } catch (e) {
        anyError = anyError || e
        if (connectionId === sourceRoute.connectionId) { sourceError = e; retry = transientRouteError(e) }
      }
    }
    if (!retry) break
  }
  const why = String((sourceError || anyError)?.message || '').replace(/^Error invoking remote method '[^']*':\s*/, '').slice(0, 160)
  throw Error(`Couldn't reach the gateway that holds this question${why ? ` (${why})` : ''}. Nothing was sent; try again in a moment.`)
}
const modelApi = (path, opts) => window.hermesDesktop.api({ path, timeoutMs: 10_000, ...opts })
export async function chooseModelOption(row, option, sourceRoute, { call = modelApi } = {}) {
  const clicked = performance.now()
  const action = row.actions.find(a => a.kind === 'decide')
  if (!row.request_id || !action?.owner || !['gatewayId', 'profile', 'storedSessionId'].every(k => typeof action.owner[k] === 'string' && action.owner[k]) || !Number.isSafeInteger(action.revision) || action.revision < 0 || action.request_id !== row.request_id || action.revision !== row.request_revision || !option.choice_id) throw Error('No canonical decision binding')
  const key = modelDecisionKey(row)
  if (modelDecisionStatus(row)?.locked) return
  modelDecisions.set(key, { locked: true, text: 'Checking decision owner…' }); modelChanged()
  try {
    // Modern models already contain canonical owner/revision/consent facts.
    // Routing that published binding is local; the POST still revalidates it.
    // Only old producers need the owner GET, never a fallback for corrupt facts.
    let route, choices, state, resolved, scopeComplete, revision
    if (action.admission !== undefined) {
      const a = action.admission
      if (!a || !Array.isArray(a.choices) || typeof a.scope_complete !== 'boolean' || typeof a.resolved !== 'boolean') throw Error('Invalid decision binding. Refresh the panel before choosing.')
      // Installation owners are not hub gateway names. Only the producer's
      // source-owner proof binds an installation to this connection locally.
      // Foreign owners retain verified discovery until a trusted install-id
      // connection registry exists; never guess from the hub-name map.
      route = action.source_owner === true
        ? { connectionId: sourceRoute.connectionId, profile: action.owner.profile, session: action.owner.storedSessionId }
        : (await modelRequestRoute(action, sourceRoute, call)).route
      if (!route.connectionId) throw Error('The gateway that holds this question is not connected. Nothing was sent.')
      ;({ choices, state, resolved } = a); scopeComplete = a.scope_complete; revision = action.revision
    } else {
      const found = await modelRequestRoute(action, sourceRoute, call)
      route = found.route
      const request = found.request
      ;({ choices, state, revision } = request); resolved = !!request.resolution
      scopeComplete = !!request.scope?.complete && !!request.scope.markdown?.trim()
    }
    feedLog(`decision owner-ready elapsed_ms=${(performance.now()-clicked).toFixed(1)}`)
    const choice = choices?.find(c => c.id === option.choice_id)
    if (revision !== action.revision || state !== 'open' || resolved || !choice || !['approve', 'answer', 'revise', 'decline'].includes(choice.effect)) throw Error('Request changed. Refresh the panel before choosing.')
    if (['approve', 'answer'].includes(choice.effect) && !scopeComplete) throw Error('The complete scope is not available. No decision posted.')
    if (['revise'].includes(choice.effect) || choice.id === 'answer-text') throw Error('This choice needs text. Answer from the existing decision queue.')
    const decision = { session: route.session, expectedRevision: action.revision, choiceId: option.choice_id,
      ...(choice.effect === 'answer' ? { revisionText: choice.label } : {}), idempotencyKey: globalThis.crypto.randomUUID() }
    // Retain BEFORE POST. A timeout or reload must not permit a second choice or new key.
    window.localStorage.setItem(MODEL_INTENT_PREFIX + key, JSON.stringify({ route, sourceRoute, owner: action.owner, requestId: action.request_id, decision }))
    modelDecisions.set(key, { locked: true, text: 'Submitting decision…' }); modelChanged()
    const posted = performance.now()
    const data = await call(`/api/plugins/user-steps/requests/${encodeURIComponent(action.request_id)}/decisions`, { ...route, method: 'POST', body: decision })
    const responded = performance.now()
    feedLog(`decision response operation=${data?.operation?.operationId || 'unknown'} post_ms=${(performance.now()-posted).toFixed(1)} elapsed_ms=${(performance.now()-clicked).toFixed(1)}`)
    const intent = { route, sourceRoute, owner: action.owner, requestId: action.request_id, decision }
    // Modern owners return the durable canonical operation. Do not throw that
    // receipt away and wait on three redundant GETs before repainting.
    if (applyDecisionReceipt(row, intent, data)) {
      const operationId = data.operation.operationId || data.operation.id
      if (decisionTimings.size >= 100) decisionTimings.delete(decisionTimings.keys().next().value)
      decisionTimings.set(operationId, { clicked, posted, responded, operationId, ...intent, call })
      const state = data.operation.dispatchState || data.operation.state
      if (Number.isSafeInteger(data.request.revision) && data.request.revision >= decision.expectedRevision &&
          data.operation.idempotencyKey === decision.idempotencyKey &&
          ['recorded', 'pending', 'queued', 'dispatching', 'delivering', 'delivered'].includes(state)) {
        provisionalDecisions.set(operationId, { ...intent, key, operationId })
        modelChanged()
      }
      void refreshDecisionPanel(intent, call, { operationId })
    }
    else await checkModelDecision(row, { call }) // mixed-version owner
  } catch (e) {
    decisionFailed(key, e, true)
  }
}
function decisionFailed(key, e, admission = false, operationId = null) {
  for (const [id, p] of provisionalDecisions) if (p.key === key && (operationId == null || id === operationId)) provisionalDecisions.delete(id)
  const retained = JSON.parse(window.localStorage.getItem(MODEL_INTENT_PREFIX + key) || 'null')
  const message = String(e?.message || e).replace(/^Error invoking remote method '[^']*':\s*/, '').replace(/^Error:\s*/, '')
  // This exact server refusal occurs BEFORE the work is admitted. Generic 503,
  // transport failures and timeouts remain uncertain and can never repost.
  const notStarted = admission && !!retained && /^503\b[^\n]*Decision not started: capacity busy/.test(message)
  if (retained && notStarted) window.localStorage.setItem(MODEL_INTENT_PREFIX + key, JSON.stringify({ ...retained, notStarted: true }))
  modelDecisions.set(key, { locked: !!retained, retry: notStarted, text: notStarted ? 'Decision was not started. Retry the same decision.' : retained ? `Outcome unknown: ${e.message}. Check status; do not choose again.` : e.message })
  modelChanged()
}
function applyDecisionReceipt(row, intent, data) {
  const op = data?.operation, r = data?.request, id = op?.operationId || op?.id
  if (!id || !sameModelOwner(r?.owner, intent.owner) || r.requestId !== intent.requestId || r.revision < intent.decision.expectedRevision ||
    (op.requestId && op.requestId !== intent.requestId) || (op.owner && !sameModelOwner(op.owner, intent.owner)) ||
    (op.idempotencyKey && op.idempotencyKey !== intent.decision.idempotencyKey) || r.resolution?.operationId !== id) return false
  const state = op.dispatchState || op.state
  if (['failed', 'reverted', 'cancelled', 'rejected'].includes(state)) {
    for (const [opId, p] of provisionalDecisions) if (p.key === modelDecisionKey(row)) provisionalDecisions.delete(opId)
  }
  modelDecisions.set(modelDecisionKey(row), { locked: true, text: state === 'delivered' ? 'Decision delivered.' : `Decision recorded; delivery ${state || 'unconfirmed'}. Check status.` })
  modelChanged()
  return true
}
// One compact client-side timing record per real decision click, joined to the
// server's "Decision timing" line by operationId. Fire-and-forget: it never
// blocks, retries or alters the decision result.
const decisionTimings = new Map()
export function reportDecisionTiming(call, route, t) {
  try {
    if (!t?.operationId) return
    const r = n => Math.round(n * 10) / 10
    const body = { session: route.session, operation: String(t.operationId),
      posted_ms: r(t.posted - t.clicked), responded_ms: r(t.responded - t.clicked),
      refreshed_ms: r(t.refreshed - t.clicked), painted_ms: r(t.painted - t.clicked),
      ...(t.visible != null ? { visible_ms: r(t.visible - t.clicked) } : {}) }
    Promise.resolve(call('/api/plugins/user-steps/client-timing', { ...route, method: 'POST', body })).catch(() => {})
  } catch {}
}
// Observe the model actually committed by a mounted Panel, not a response bundle
// or a speculative render. No record if the chat is unfocused/unmounted. Bounded,
// ephemeral tracking only; no retries or persistent telemetry queue.
function observeDecisionRender(route, model) {
  const now = performance.now()
  for (const [id, t] of decisionTimings) {
    if (now - t.clicked > 600_000) { decisionTimings.delete(id); continue }
    if (routeKey(route) !== routeKey(t.sourceRoute) || !model) continue
    // Missing canonical requests can empty needs_you during an outage. That is
    // not authoritative departure; retain the timing until a known model commits.
    if (!Array.isArray(model.diagnostics) || model.diagnostics.includes('user-steps requests unavailable')) continue
    if (model.sections.needs_you.some(row => row.request_id === t.requestId &&
        row.actions.some(a => a.kind === 'decide' && sameModelOwner(a.owner, t.owner)))) continue
    if (t.painted == null) {
      t.painted = now
      if (provisionalDecisions.has(id)) t.visible = now
    }
    finishDecisionTiming(id, t)
  }
}
function finishDecisionTiming(id, t) {
  if (t?.painted == null || t.refreshed == null) return
  decisionTimings.delete(id)
  reportDecisionTiming(t.call, t.route, t)
}
async function refreshDecisionPanel(intent, call, timing = null) {
  const route = intent.sourceRoute || intent.route
  const started = performance.now()
  // A background response belongs to one frozen intent, not every same-ID ask
  // in this panel. Another owner/revision may have been answered in the meantime.
  const ownReceipt = (id, p) => id === timing?.operationId &&
    routeKey(p.sourceRoute) === routeKey(route) && sameModelOwner(p.owner, intent.owner) &&
    p.requestId === intent.requestId && p.decision.expectedRevision === intent.decision.expectedRevision &&
    p.decision.idempotencyKey === intent.decision.idempotencyKey
  try {
    const data = await call('/api/plugins/session-panel/decision-refresh', { ...route, method: 'POST', body: { session: route.session } })
    if (data?.bundle?.session !== route.session || !validModel(data.bundle.model) || typeof data.epoch !== 'string' || !Number.isSafeInteger(data.rev)) throw Error('Panel refresh unavailable')
    if (data.bundle.model.diagnostics?.includes('user-steps requests unavailable')) throw Error('Canonical decision status unavailable')
    const cur = feeds.get(connKey(route.connectionId, route.profile))
    if (cur && (cur.feed !== data.feed || cur.epoch !== data.epoch)) throw Error('Panel feed changed; check status')
    // An unrelated chat may have advanced the connection cursor. The per-scope
    // version fence below, not that global cursor, owns this model's freshness.
    // Replace the temporary receipt presentation with canonical classification.
    for (const [id, p] of provisionalDecisions) if (ownReceipt(id, p)) provisionalDecisions.delete(id)
    ingestBundle(route.connectionId, route.profile, data.bundle, data)
    modelChanged()
    feedLog(`decision model-applied refresh_ms=${(performance.now()-started).toFixed(1)} rev=${data.rev}`)
  } catch (e) {
    log('decision recorded; panel refresh deferred', e?.message || e)
    for (const [id, p] of provisionalDecisions) if (ownReceipt(id, p)) decisionFailed(p.key, e, false, id)
  } finally {
    const t = decisionTimings.get(timing?.operationId)
    if (t) { t.refreshed = performance.now(); finishDecisionTiming(timing.operationId, t) }
  }
}
export async function checkModelDecision(row, { call = modelApi } = {}) {
  const key = modelDecisionKey(row)
  const intent = JSON.parse(window.localStorage.getItem(MODEL_INTENT_PREFIX + key) || 'null')
  if (!intent) return
  let retryPost = false
  try {
    const { route, request } = await modelRequestRoute({ request_id: intent.requestId, owner: intent.owner }, intent.route, call)
    if (intent.notStarted) {
      if (request.revision !== intent.decision.expectedRevision || request.state !== 'open' || request.resolution) throw Error('Request changed. Refresh the panel before retrying.')
      // Explicit Retry only, and only the identical frozen choice/key. Clear the
      // refusal before sending: a lost response is unknown, never auto-retried.
      delete intent.notStarted
      window.localStorage.setItem(MODEL_INTENT_PREFIX + key, JSON.stringify(intent))
      modelDecisions.set(key, { locked: true, text: 'Submitting decision…' }); modelChanged()
      retryPost = true
      const data = await call(`/api/plugins/user-steps/requests/${encodeURIComponent(intent.requestId)}/decisions`, { ...route, method: 'POST', body: intent.decision })
      if (!applyDecisionReceipt(row, intent, data)) throw Error('Invalid decision receipt')
      await refreshDecisionPanel(intent, call, { operationId: data.operation.operationId || data.operation.id })
      return
    }
    const query = `?session=${encodeURIComponent(route.session)}`
    const found = await call(`/api/plugins/user-steps/operations${query}&idempotencyKey=${encodeURIComponent(intent.decision.idempotencyKey)}`, route)
    const id = found?.operation?.operationId || found?.operation?.id
    if (!id) throw Error('No operation receipt found yet')
    const data = await call(`/api/plugins/user-steps/operations/${encodeURIComponent(id)}${query}`, route)
    const op = data?.operation, r = data?.request
    if (!op || (op.operationId || op.id) !== id || !sameModelOwner(r?.owner, intent.owner) || r.requestId !== intent.requestId || r.revision < intent.decision.expectedRevision ||
      (op.requestId && op.requestId !== intent.requestId) || (op.owner && !sameModelOwner(op.owner, intent.owner))) throw Error('Invalid decision receipt')
    if (!applyDecisionReceipt(row, intent, data)) throw Error('Invalid decision receipt')
    await refreshDecisionPanel(intent, call, { operationId: id })
    // Keep intent even after confirmation: an old cached model must never offer the choice again.
    void request
  } catch (e) { decisionFailed(key, e, retryPost) }
  modelChanged()
}
/** Open the chat that owns a card, on that card's gateway (a remote gateway gets an explicit owner route). */
export async function openSessionAction(action, panel = panelRoute()) {
  const t = action?.target
  if (!str(t?.session) || typeof host.openSession !== 'function') return false
  const profile = str(t.profile) || 'default'
  const connectionId = t.gateway ? tasksBridge()?.connectionForHubGateway?.(t.gateway) : panel.connectionId
  if (!connectionId) { host.notify?.({ kind: 'error', message: 'The gateway that holds this session is not connected.' }); return false }
  let kind = null
  try { kind = (host.state?.gateways?.get?.() || []).find(g => g?.id === connectionId)?.kind || null } catch {}
  const route = { connectionId, mode: kind === 'local' ? 'local' : 'remote', profile, targetProfile: profile }
  try { await host.openSession(str(t.session), { route }); return true }
  catch (e) { host.notify?.({ kind: 'error', message: `Couldn’t open session: ${e?.message || e}` }); return false }
}
/** Open an http(s) link from a row (CI run, PR) with the OS handler; never a navigation of the Desktop window. */
export async function openUrl(url) {
  if (!/^https?:\/\//.test(str(url))) return false
  try { if (await osApi?.openExternal?.(url)) return true } catch {}
  try { if (typeof window !== 'undefined' && window.open?.(url, '_blank', 'noopener')) return true } catch {}
  host.notify?.({ kind: 'error', message: `Couldn’t open ${url}` })
  return false
}
/** True while the focused chat's cached hub model carries the one status line for this answered request, so the
 *  decision queue (user-steps) stays silent about it. */
export function showsDecision(requestId, route = panelRoute()) {
  if (unsupportedFeed(route)) return false
  const model = decisionPresentation(cachedModel(route), route)
  return !!requestId && !!model && MODEL_SECTIONS.some(([section]) => model.sections?.[section]?.some(row => row?.decision?.request_id === requestId))
}
export function PendingModel({ waiting, onRetry }) {
  return jsxs('div', { 'data-hub-model-pending': '', role: 'status', children: [
    waiting ? 'Loading session status…' : 'Session status is taking longer than usual. ',
    !waiting && jsx('button', { type: 'button', onClick: onRetry, children: 'Retry' })
  ] })
}
// Each mounted panel owns a short lease; sidebar registrations are not visibility.
// Scopes whose mounted Panel hosts the user-steps decision companion in its body (user-steps reads this to decide
// whether its "panel missing" error applies).
const hostedScopes = new Map()
const hostingChanged = () => { try { window.dispatchEvent?.(new window.Event('hermes:session-panel-hosting')) } catch {} }
/** Whether a mounted Panel hosts the companion for the focused chat. The connection comes from this plugin's own
 *  route (one source), so a caller that resolves the active connection differently cannot disagree with a working
 *  panel; the caller's session and profile must still be the focused ones. */
export function hostsQueue(route) {
  const own = panelRoute()
  return !!route?.session && route.session === own.session && (route.profile || 'default') === own.profile && (hostedScopes.get(routeKey(own)) || 0) > 0
}
/** The user-steps companion inside the panel body. Read at every render (the panel ticks each second), so a
 *  late-registered or hot-reloaded user-steps attaches without a reload; keyed by version, so exactly one mounts. */
export function companionFor(coverage) {
  const us = typeof window !== 'undefined' ? window.__userSteps : null
  return typeof us?.ActionQueue === 'function' ? jsx(us.ActionQueue, { coverage }, `user-steps:${str(us.version)}`) : null
}
export function watchPanel(route) {
  const subscriber = `panel-${Math.random().toString(36).slice(2)}-${Date.now()}`
  const bridge = typeof window !== 'undefined' ? window.hermesDesktop?.api : null
  if (!bridge || !route?.session || !route.connectionId) return () => {}
  const send = visible => {
    void Promise.resolve().then(() => bridge({ path: FEED_PATH, method: 'POST',
      connectionId: route.connectionId, profile: route.profile || 'default', timeoutMs: 5000,
      body: { sessions: [], subscriber, visible_session: visible ? route.session : null }
    })).catch(() => {})
  }
  const renew = () => send(typeof document === 'undefined' || !document.hidden)
  renew()
  const timer = setInterval(renew, 30000)
  if (typeof document !== 'undefined') document.addEventListener?.('visibilitychange', renew)
  return () => {
    clearInterval(timer)
    if (typeof document !== 'undefined') document.removeEventListener?.('visibilitychange', renew)
    send(false)
  }
}
export function Panel() {
  const [, tick] = useState(0)
  const [retry, setRetry] = useState(0)
  const [pending, setPending] = useState(null)
  const [process, setProcess] = useState(null)
  useValue(host.state.focusedStoredSessionId)
  useValue(host.state.focusedSessionOwner || host.state.connectionId)
  useValue(host.state.connectionId)
  useValue(host.state.profile || host.state.connectionId)
  const route = panelRoute(), scope = routeKey(route)
  useEffect(() => setProcess(null), [scope])
  useEffect(() => watchPanel(route), [scope])
  useEffect(() => {
    if (!route.session) return
    hostedScopes.set(scope, (hostedScopes.get(scope) || 0) + 1); hostingChanged()
    return () => { const n = (hostedScopes.get(scope) || 1) - 1; if (n > 0) hostedScopes.set(scope, n); else hostedScopes.delete(scope); hostingChanged() }
  }, [scope])
  useEffect(() => {
    if (typeof window === 'undefined') return
    const fn = () => tick(v => v + 1)
    window.addEventListener?.('hermes:user-steps-changed', fn)
    return () => window.removeEventListener?.('hermes:user-steps-changed', fn)
  }, [])
  useEffect(() => { const fn = () => tick(v => v + 1); modelSubs.add(fn); return () => modelSubs.delete(fn) }, [])
  useEffect(() => {
    const timer = setInterval(() => { if (!document.hidden) tick(v => v + 1) }, 1000)
    return () => clearInterval(timer)
  }, [])
  useEffect(() => {
    let active = true
    noteRecent(route)
    setPending({ scope, waiting: true })
    void feedReconcile(route)
    const wait = ms => new Promise(r => setTimeout(r, ms))
    void (async () => {
      await feedFocus(route, { active: () => active, wait })
      if (!active) return
      setPending({ scope, waiting: false })
      for (let i = 0; active && !cachedModel(route); i++) {
        await wait(FEED_FOCUS.retryMs[Math.min(i, FEED_FOCUS.retryMs.length - 1)])
        if (!active || cachedModel(route)) break
        await feedFocus(route, { active: () => active, wait, delays: [0] })
      }
    })()
    return () => { active = false }
  }, [scope, retry])
  const model = decisionPresentation(cachedModel(route), route)
  useEffect(() => { observeDecisionRender(route, model) })
  if (!route.session) return null
  if (unsupportedFeed(route)) return bodyFrame(jsx('div', { role: 'alert', 'data-session-panel-error': '',
    children: `Session panel isn't served for profile ${route.profile || 'default'} on ${route.connectionId}: ${feeds.get(connKey(route.connectionId, route.profile)).reason}. Linked task status remains in the task strip.` }, 'error'),
    companionFor({ state: 'unsupported', collapsed: false, drawn: {}, counted: {} }))
  if (!model) return bodyFrame(jsx(PendingModel, { waiting: pending?.scope !== scope || pending.waiting, onRetry: () => setRetry(v => v + 1) }, 'pending'),
    companionFor({ state: 'loading', collapsed: false, drawn: {}, counted: {} }))
  const collapsed = isCollapsed(model, readCollapsed(scope))
  const panel = renderModel(model, {
    companion: companionFor,
    // Cold/legacy cache has no source clock: absence of a heartbeat is not a voucher.
    vouched: feedVouches(route),
    collapsed,
    currentSession: route.session,
    onToggle: () => { writeCollapsed(scope, collapsed ? null : attentionKeys(model)); tick(v => v + 1) },
    onChoose: (row, option) => void chooseModelOption(row, option, route),
    decisionStatus: modelDecisionStatus, onCheck: row => void checkModelDecision(row),
    onAction: (row, action) => {
      if (action.kind === 'open_url') { void openUrl(action.url); return }
      if (action.kind === 'open_session') { void openSessionAction(action, route); return }
      if (action.kind === 'open_process') {
        try {
          if (typeof host.requestProfile !== 'function' || !sdk.Dialog) throw Error('Opening processes requires a newer Desktop build.')
          const target = Object.freeze({...action.target, title: row.title, detail: row.detail})
          const ownerRoute = processRoute(target, tasksBridge(), host.state?.gateways?.get?.())
          setProcess({target, route: ownerRoute, scope})
        } catch (e) { host.notify?.({kind: 'error', message: String(e?.message || e)}) }
        return
      }
      const t = action.target
      if (!t?.id) return
      const connectionId = t.gateway ? tasksBridge()?.connectionForHubGateway?.(t.gateway) : route.connectionId
      if (!connectionId) { host.notify?.({ kind: 'error', message: 'Card owner connection unavailable.' }); return }
      void tasksBridge()?.openTaskChip?.({ id: t.id, kind: 'kanban', gatewayId: t.gateway, profile: t.profile, board: t.board, connectionId })
    }
  })
  return jsxs('div', {children: [panel, process?.scope === scope && jsx(ProcessView, {
    target: process.target, route: process.route, onClose: () => setProcess(null)
  })]})
}

export default {
  id: ID,
  name: 'Session Panel',
  register(ctx) {
    osApi = ctx?.os || null
    if (typeof window !== 'undefined') window.__sessionPanel = { active: true, version: VERSION, covers, feedVouches, feedIsLive, cachedModel, showsDecision, clockRow, hostsQueue }
    ctx.register({ id: 'panel', area: COMPOSER_AREAS.top, order: -30, render: () => jsx(Panel, {}) })
    const subscribe = typeof ctx.onEvent === 'function' ? ctx.onEvent : host?.onEvent
    const offFeed = typeof subscribe === 'function' ? subscribe(FEED_EVENT, onFeedEvent) : null
    const offSidebar = watchSidebar()
    const feedFirst = setTimeout(() => void feedPass(), 1_000)
    const feedTimer = setInterval(() => { if (typeof document === 'undefined' || !document.hidden) void feedPass() }, FEED_CATCHUP_MS)
    const onVisible = () => { if (typeof document !== 'undefined' && !document.hidden) void feedPass() }
    if (typeof document !== 'undefined') document.addEventListener?.('visibilitychange', onVisible)
    return () => {
      clearTimeout(feedFirst)
      clearInterval(feedTimer)
      if (typeof document !== 'undefined') document.removeEventListener?.('visibilitychange', onVisible)
      if (typeof offFeed === 'function') offFeed()
      offSidebar()
      if (typeof window !== 'undefined' && window.__sessionPanel?.version === VERSION) delete window.__sessionPanel
    }
  }
}

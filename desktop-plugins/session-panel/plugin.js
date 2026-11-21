/** Session Panel: render the hub's PanelModel v1; no local work-state inference. */
import { COMPOSER_AREAS, host, useValue } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'session-panel'
const VERSION = 'session-panel-v15-hub-model'
const str = v => (typeof v === 'string' ? v : v == null ? '' : String(v))
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
// focused, the most recently active sidebar chats are kept warm too (one never opened in this window included).
export const WARM_SIDEBAR = 12
export const WARM_MAX = 20
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
const feeds = new Map()      // JSON [connectionId, profile] -> { connectionId, profile, feed, epoch, rev, sessions, liveAt, unsupported, at }
const feedFlights = new Map() // same key -> the catch-up in flight
const connKey = (connectionId, profile) => JSON.stringify([connectionId || '', profile || 'default'])
// One desktop.log line per feed state change (registered, no backend half, pushes started), like open-timing:
// `[panel-feed] <connection>/<profile> <state> ...`.
const feedLog = msg => { try { window.hermesDesktop?.logLine?.(`[panel-feed] ${msg}`) } catch {} }
const tasksBridge = () => (typeof window !== 'undefined' ? window.__sessionTasks : null)
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
export const resetFeedForTests = () => { feeds.clear(); feedFlights.clear(); feedQueued.clear(); modelCache.clear() }

/** Cache only the model. Missing/unsupported models invalidate this exact scope; raw cards are never a fallback. */
export function ingestBundle(connectionId, profile, b) {
  if (!connectionId || !b?.session) return false
  profile = profile || 'default'
  const scope = routeKey({ connectionId, profile, session: b.session })
  modelCache.delete(scope)
  if (b.model?.version === 1 && b.model.header && b.model.sections) {
    modelCache.set(scope, b.model)
    if (modelCache.size > 100) modelCache.delete(modelCache.keys().next().value)
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

async function feedCatchUpNow(target, { nowMs = Date.now(), reset = false, full = false } = {}) {
  const bridge = typeof window !== 'undefined' ? window.hermesDesktop?.api : null
  if (!bridge || !target?.connectionId || !target.sessions?.length) return false
  const profile = target.profile || 'default'
  const key = connKey(target.connectionId, profile)
  const st = feeds.get(key)
  if (st?.unsupported && nowMs - st.at < FEED_UNSUPPORTED_RETRY_MS) return false
  const unsupported = why => {
    if (!st?.unsupported) feedLog(`${target.connectionId}/${profile} no-feed ${why}`)
    feeds.set(key, { connectionId: target.connectionId, profile, unsupported: true, at: nowMs })
    return false
  }
  const sameFeed = !reset && st && !st.unsupported
  let r
  try {
    r = await bridge({ path: FEED_PATH, method: 'POST', connectionId: target.connectionId, profile, timeoutMs: 15_000,
      body: { sessions: target.sessions.slice(0, 200), since: sameFeed && !full ? st.rev || 0 : 0, epoch: sameFeed ? st.epoch || null : null } })
  } catch (e) {
    // The Desktop bridge rejects a 404 with "404: ...": this gateway has no backend half.
    if (/^404\b/.test(String(e?.message || ''))) return unsupported('404')
    log('feed catch-up failed', target.connectionId, e?.message || e)
    return false
  }
  if (!r || r.profile_unsupported || typeof r.epoch !== 'string') return unsupported(r?.profile_unsupported ? `profile ${r.profile}` : 'no epoch')
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
      return feedCatchUpNow(target, { nowMs, reset: true })
    }
  }
  const next = { connectionId: target.connectionId, profile, feedProfile: str(r.profile) || profile, feed: r.feed, epoch: r.epoch, rev: Number(r.rev) || 0,
    sessions: [...target.sessions], liveAt: cur?.feed === r.feed && cur?.epoch === r.epoch ? cur.liveAt : 0, at: Date.now() }
  feeds.set(key, next)
  if (cur?.epoch !== r.epoch || cur?.feed !== r.feed) feedLog(`${target.connectionId}/${profile} registered feed=${r.feed} epoch=${r.epoch} rev=${next.rev} watching=${target.sessions.length} bundles=${Object.keys(r.bundles || {}).length}`)
  next.fresh = cur?.feed === r.feed && cur?.epoch === r.epoch ? cur.fresh || [] : []
  for (const b of Object.values(r.bundles || {})) ingestBundle(target.connectionId, profile, b)
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
  for (const b of Object.values(p.bundles || {})) ingestBundle(st.connectionId, st.profile, b)
  confirmFeed(st, p.fresh)
  if (!wasLive) void feedCatchUp(target, { full: true })
}

/** Catch up every gateway once (the safety net, and on load). */
export async function feedPass() {
  const done = []
  for (const t of feedTargets()) if (await feedCatchUp(t)) done.push(t)
  return done
}
/** Focus moved: register the chat with its gateway's feed now if that feed does not watch it yet. */
export function feedFocus(route) {
  if (!route?.connectionId || !route?.session) return
  const st = feeds.get(connKey(route.connectionId, route.profile))
  if (st?.unsupported || st?.sessions?.includes(route.session)) return
  const t = feedTargets().find(x => x.connectionId === route.connectionId && (x.profile || 'default') === (route.profile || 'default'))
  if (t) void feedCatchUp(t)
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

/** Pure model -> React tree. No parsing, classification, I/O, clock or ambient state. */
export function renderModel(model, { onChoose, onAction, decisionStatus = () => null, onCheck } = {}) {
  if (model?.version !== 1 || !model.header || !model.sections) return null
  const details = row => row.details?.length > 0 && jsxs('details', { 'data-ask-details': '', style: modelSmall, children: [
    jsx('summary', { children: 'Details' }),
    ...row.details.map((text, i) => jsx('div', { style: { whiteSpace: 'pre-wrap', marginTop: 4 }, children: text }, i))
  ] })
  return jsxs('section', { 'data-session-panel': '', 'data-hub-model': model.revision, 'aria-label': 'Session status',
    style: { minWidth: 0, width: '100%', color: 'var(--foreground)', maxHeight: '55vh', overflowY: 'auto', padding: '6px 12px', boxSizing: 'border-box' }, children: [
    jsxs('header', { style: { ...modelSmall, display: 'flex', gap: 8, flexWrap: 'wrap' }, children: [
      jsx('strong', { children: 'This session' }),
      jsx('span', { 'data-session-status': '', children: model.header.state }),
      ...model.header.pips.map((pip, i) => jsx('span', { 'data-model-pip': pip.section, children: `${pip.count} ${pip.label}` }, i)),
      // Counts are the hub's values, not row lengths, even if a test deliberately makes them disagree.
      ...MODEL_SECTIONS.map(([section]) => jsx('span', { 'data-model-count': section, hidden: true, children: model.header.counts[section] }, section))
    ] }),
    ...MODEL_SECTIONS.map(([section, label]) => model.sections[section].length > 0 && jsxs('div', { 'data-model-section': section, children: [
      jsx('h3', { 'data-section': section.replaceAll('_', '-'), style: { fontSize: 12, margin: '10px 0 6px', color: 'var(--muted-foreground)' }, children: `${label} · ${model.header.counts[section]}` }),
      ...model.sections[section].map(row => {
        const status = decisionStatus(row)
        return jsxs('article', { 'data-panel-row': row.key, ...(section === 'needs_you' ? { 'data-ask-row': row.source } : {}),
          style: { border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px', marginBottom: 6, minWidth: 0, display: 'grid', gap: 6 }, children: [
          jsx('strong', { 'data-ask-title': '', style: { fontSize: 13, overflowWrap: 'anywhere' }, children: row.title }),
          row.detail && jsx('div', { style: modelSmall, children: row.detail }),
          section === 'needs_you' && jsxs('div', { style: { display: 'grid', gap: 6 }, children: [
            row.situation && jsx('div', { 'data-ask-situation': '', title: row.situation, style: { ...modelSmall, ...clamp(3) }, children: row.situation }),
            ...row.options.map(option => jsxs('button', { type: 'button', 'data-choice-letter': option.letter,
              ...(option.recommended ? { 'data-recommended-row': '' } : {}), disabled: !onChoose || !row.request_id || !option.choice_id || !row.actions.some(a => a.kind === 'decide' && a.owner && a.request_id === row.request_id && a.revision === row.request_revision) || !!status?.locked,
              'aria-label': `Choose option ${option.letter}: ${option.name}`,
              onClick: () => onChoose?.(row, option),
              style: { textAlign: 'left', font: 'inherit', color: 'inherit', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px', background: option.recommended ? 'color-mix(in srgb, var(--foreground) 6%, transparent)' : 'transparent', display: 'grid', gridTemplateColumns: '22px minmax(0,1fr)', gap: 8, cursor: 'pointer' }, children: [
              jsx('span', { 'data-option-badge': '', style: { fontWeight: 700 }, children: option.letter }),
              jsxs('span', { style: { minWidth: 0 }, children: [
                jsx('strong', { 'data-option-label': '', children: option.name }),
                option.recommended && jsx('span', { style: { ...pill('var(--foreground)'), marginLeft: 8 }, children: 'Recommended' }),
                jsx('span', { 'data-option-detail': '', title: option.detail, style: { display: 'block', ...modelSmall, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', color: 'var(--muted-foreground)' }, children: option.detail }),
                option.recommended && option.reason && jsx('span', { 'data-option-reason': '', style: { display: 'block', ...modelSmall }, children: `Why: ${option.reason}` })
              ] })
            ] }, option.letter)),
            row.only_you && jsx('div', { 'data-ask-only-you': '', style: modelSmall, children: row.only_you }),
            status?.text && jsx('div', { role: 'status', style: modelSmall, children: status.text }),
            status?.locked && onCheck && jsx('button', { type: 'button', onClick: () => onCheck(row), children: 'Check status' })
          ] }),
          details(row),
          ...row.actions.filter(action => action.kind === 'open_card').map((action, i) => jsx('button', { type: 'button', style: askLinkBtn, disabled: !onAction, onClick: () => onAction?.(row, action), children: action.label }, i))
        ] }, row.key)
      })
    ] }, section))
  ] })
}

const MODEL_INTENT_PREFIX = 'hermes.sessionPanel.decision.v1:'
const modelDecisions = new Map()
const modelDecisionKey = row => JSON.stringify([row.actions.find(a => a.kind === 'decide')?.owner, row.request_id, row.request_revision])
export function modelDecisionStatus(row) {
  const key = modelDecisionKey(row)
  if (modelDecisions.has(key)) return modelDecisions.get(key)
  try { if (window.localStorage.getItem(MODEL_INTENT_PREFIX + key)) return { locked: true, text: 'Decision outcome unknown — check status before choosing again.' } } catch {}
  return null
}
const sameModelOwner = (a, b) => !!a && !!b && a.gatewayId === b.gatewayId && a.profile === b.profile && a.storedSessionId === b.storedSessionId
/** Resolve on click only. Never use an ambient gateway for an unverified foreign request. */
async function modelRequestRoute(action, sourceRoute, call) {
  const candidates = [sourceRoute.connectionId]
  try { for (const c of await host.connections?.() || []) if (!candidates.includes(c.id)) candidates.push(c.id) } catch {}
  for (const connectionId of candidates.filter(Boolean)) {
    const route = { connectionId, profile: action.owner.profile, session: action.owner.storedSessionId }
    try {
      const data = await call(`/api/plugins/user-steps/requests/${encodeURIComponent(action.request_id)}?session=${encodeURIComponent(route.session)}`, route)
      if (data?.request?.requestId === action.request_id && sameModelOwner(data.request.owner, action.owner)) return { route, request: data.request }
    } catch {}
  }
  throw Error('The exact decision owner is unavailable. No decision was posted.')
}
const modelApi = (path, opts) => window.hermesDesktop.api({ path, timeoutMs: 10_000, ...opts })
export async function chooseModelOption(row, option, sourceRoute, { call = modelApi } = {}) {
  const action = row.actions.find(a => a.kind === 'decide')
  if (!row.request_id || !action?.owner || action.request_id !== row.request_id || action.revision !== row.request_revision || !option.choice_id) throw Error('No canonical decision binding')
  const key = modelDecisionKey(row)
  if (modelDecisionStatus(row)?.locked) return
  modelDecisions.set(key, { locked: true, text: 'Checking decision owner…' }); modelChanged()
  try {
    const { route, request } = await modelRequestRoute(action, sourceRoute, call)
    const choice = request.choices?.find(c => c.id === option.choice_id)
    if (request.revision !== action.revision || request.state !== 'open' || request.resolution || !choice) throw Error('Request changed. Refresh the panel before choosing.')
    if (['approve', 'answer'].includes(choice.effect) && (!request.scope?.complete || !request.scope.markdown?.trim())) throw Error('The complete scope is not available. No decision posted.')
    if (['revise'].includes(choice.effect) || choice.id === 'answer-text') throw Error('This choice needs text. Answer from the existing decision queue.')
    const decision = { session: route.session, expectedRevision: action.revision, choiceId: option.choice_id,
      ...(choice.effect === 'answer' ? { revisionText: choice.label } : {}), idempotencyKey: globalThis.crypto.randomUUID() }
    // Retain BEFORE POST. A timeout or reload must not permit a second choice or new key.
    window.localStorage.setItem(MODEL_INTENT_PREFIX + key, JSON.stringify({ route, owner: action.owner, requestId: action.request_id, decision }))
    modelDecisions.set(key, { locked: true, text: 'Submitting decision…' }); modelChanged()
    await call(`/api/plugins/user-steps/requests/${encodeURIComponent(action.request_id)}/decisions`, { ...route, method: 'POST', body: decision })
    await checkModelDecision(row, { call })
  } catch (e) {
    const retained = window.localStorage.getItem(MODEL_INTENT_PREFIX + key)
    modelDecisions.set(key, { locked: !!retained, text: retained ? `Outcome unknown: ${e.message}. Check status; do not choose again.` : e.message })
    modelChanged()
  }
}
export async function checkModelDecision(row, { call = modelApi } = {}) {
  const key = modelDecisionKey(row)
  const intent = JSON.parse(window.localStorage.getItem(MODEL_INTENT_PREFIX + key) || 'null')
  if (!intent) return
  try {
    const { route, request } = await modelRequestRoute({ request_id: intent.requestId, owner: intent.owner }, intent.route, call)
    const query = `?session=${encodeURIComponent(route.session)}`
    const found = await call(`/api/plugins/user-steps/operations${query}&idempotencyKey=${encodeURIComponent(intent.decision.idempotencyKey)}`, route)
    const id = found?.operation?.operationId || found?.operation?.id
    if (!id) throw Error('No operation receipt found yet')
    const data = await call(`/api/plugins/user-steps/operations/${encodeURIComponent(id)}${query}`, route)
    const op = data?.operation, r = data?.request
    if (!op || (op.operationId || op.id) !== id || !sameModelOwner(r?.owner, intent.owner) || r.requestId !== intent.requestId || r.revision < intent.decision.expectedRevision ||
      (op.requestId && op.requestId !== intent.requestId) || (op.owner && !sameModelOwner(op.owner, intent.owner))) throw Error('Invalid decision receipt')
    const state = op.dispatchState || op.state
    const confirmed = state === 'delivered' && r.resolution?.operationId === id
    modelDecisions.set(key, { locked: true, text: confirmed ? 'Decision delivered.' : `Decision recorded; delivery ${state || 'unconfirmed'}. Check status.` })
    // Keep intent even after confirmation: an old cached model must never offer the choice again.
    void request
  } catch (e) { modelDecisions.set(key, { locked: true, text: `Outcome unknown: ${e.message}. Check status.` }) }
  modelChanged()
}
export function Panel() {
  const [, tick] = useState(0)
  useValue(host.state.focusedStoredSessionId)
  useValue(host.state.focusedSessionOwner || host.state.connectionId)
  useValue(host.state.connectionId)
  useValue(host.state.profile || host.state.connectionId)
  const route = panelRoute(), scope = routeKey(route)
  useEffect(() => { const fn = () => tick(v => v + 1); modelSubs.add(fn); return () => modelSubs.delete(fn) }, [])
  useEffect(() => { noteRecent(route); feedFocus(route) }, [scope])
  const model = cachedModel(route)
  if (!route.session) return null
  if (!model) return jsx('div', { 'data-hub-model-pending': '', children: 'Hub status model not available yet.' })
  return renderModel(model, {
    onChoose: (row, option) => void chooseModelOption(row, option, route),
    decisionStatus: modelDecisionStatus, onCheck: row => void checkModelDecision(row),
    onAction: (row, action) => {
      const t = action.target
      const connectionId = t.gateway ? tasksBridge()?.connectionForHubGateway?.(t.gateway) : route.connectionId
      if (!connectionId) { host.notify?.({ kind: 'error', message: 'Card owner connection unavailable.' }); return }
      void tasksBridge()?.openTaskChip?.({ id: t.id, kind: 'kanban', gatewayId: t.gateway, profile: t.profile, board: t.board, connectionId })
    }
  })
}

export default {
  id: ID,
  name: 'Session Panel',
  register(ctx) {
    if (typeof window !== 'undefined') window.__sessionPanel = { active: true, version: VERSION, feedVouches, feedIsLive, cachedModel }
    ctx.register({ id: 'panel', area: COMPOSER_AREAS.top, order: -30, render: () => jsx(Panel, {}) })
    const subscribe = typeof ctx.onEvent === 'function' ? ctx.onEvent : host?.onEvent
    const offFeed = typeof subscribe === 'function' ? subscribe(FEED_EVENT, onFeedEvent) : null
    const feedFirst = setTimeout(() => void feedPass(), 1_000)
    const feedTimer = setInterval(() => { if (typeof document === 'undefined' || !document.hidden) void feedPass() }, FEED_CATCHUP_MS)
    const onVisible = () => { if (typeof document !== 'undefined' && !document.hidden) void feedPass() }
    if (typeof document !== 'undefined') document.addEventListener?.('visibilitychange', onVisible)
    return () => {
      clearTimeout(feedFirst)
      clearInterval(feedTimer)
      if (typeof document !== 'undefined') document.removeEventListener?.('visibilitychange', onVisible)
      if (typeof offFeed === 'function') offFeed()
      if (typeof window !== 'undefined' && window.__sessionPanel?.version === VERSION) delete window.__sessionPanel
    }
  }
}

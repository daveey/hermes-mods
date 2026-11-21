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
  if (validModel(b.model)) {
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
// P3b owns the remote courier cutover. Preserve the existing explicit draft (never send) meanwhile.
const draftOnly = row => row.source === 'card' && !row.request_id
export const chooseText = (row, option) => `Card ${row.id}: go with ${option.letter ? `option ${option.letter}` : 'this option'} (${option.name}).\n`
export const validModel = model => model?.version === 1 && !!model.header?.counts && Array.isArray(model.header?.pips) &&
  MODEL_SECTIONS.every(([section]) => Array.isArray(model.sections?.[section]))

// Palette and shapes carried over from the pre-hub panel so the cutover keeps the same look: one coloured box per
// state (header pips and row markers read as one legend), red-edged ask cards, accent-filled recommended option.
// Text colours keep >= 4.5:1 contrast on both themes via light-dark(); ACCENT_FILL carries white text.
export const SECTION_COLOR = { needs_you: '#f85149', stuck: '#f0883e', running: '#3b82f6', waiting: '#d29922', ready: '#3fb950', done: '#8b949e' }
const ACCENT_TEXT = 'light-dark(#0969da, #58a6ff)', ALERT_TEXT = 'light-dark(#cf222e, #ff7b72)', ACCENT_FILL = '#0969da'
const muted = { color: 'var(--muted-foreground)', fontSize: 12 }
const box = section => ({ width: 9, height: 9, borderRadius: 2, boxSizing: 'border-box', flex: 'none',
  ...(section === 'waiting' ? { border: `1.5px solid ${SECTION_COLOR.waiting}` } : { background: SECTION_COLOR[section] || SECTION_COLOR.done }) })
const ghostBtn = { minHeight: 26, padding: '2px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'transparent', color: 'var(--foreground)', font: 'inherit', fontSize: 12, whiteSpace: 'nowrap' }
const PANEL_CSS = `[data-session-panel] [data-choice-letter]:not(:disabled):hover{background:color-mix(in srgb,var(--foreground) 5%,transparent)}
[data-session-panel] [data-choice-letter][data-recommended-row]:not(:disabled):hover{background:color-mix(in srgb,${ACCENT_FILL} 15%,transparent)}
[data-session-panel] [data-choice-letter]:disabled{cursor:default;opacity:.6}
[data-session-panel] [data-line-row]{border-radius:5px}[data-session-panel] [data-line-row]:hover{background:color-mix(in srgb,var(--foreground) 5%,transparent)}
[data-session-panel] summary{cursor:pointer}`
const STATE_LABEL = { decision: 'needs your decision', needs_input: 'needs you', needs_user: 'needs you', working: 'working', done: 'done', stuck: 'stuck', idle: 'idle' }

/** Pure model -> React tree. No parsing, classification, I/O, clock or ambient state. */
export function renderModel(model, { onChoose, onAction, decisionStatus = () => null, onCheck } = {}) {
  if (!validModel(model)) return null
  const state = str(model.header.state)
  const alert = state === 'decision' || state === 'needs_input' || state === 'needs_user' || (model.header.counts.needs_you || 0) > 0
  const details = row => row.details?.length > 0 && jsxs('details', { 'data-ask-details': '', style: { ...modelSmall, ...muted, flex: 1, minWidth: 0 }, children: [
    jsx('summary', { style: { fontSize: 11.5 }, children: 'Details' }),
    ...row.details.map((text, i) => jsx('div', { style: { whiteSpace: 'pre-wrap', marginTop: 4, fontSize: 11.5 }, children: text }, i))
  ] })
  const openLinks = row => row.actions.filter(action => action.kind === 'open_card').map((action, i) => jsx('button', { type: 'button', 'data-open-card': '',
    style: { ...askLinkBtn, fontSize: 11.5, whiteSpace: 'nowrap', cursor: onAction ? 'pointer' : 'default' }, disabled: !onAction, onClick: () => onAction?.(row, action), children: action.label }, `open${i}`))
  const askRow = row => {
    const status = decisionStatus(row)
    const enabled = option => !!onChoose && (draftOnly(row) || (!!row.request_id && !!option.choice_id && row.actions.some(a => a.kind === 'decide' && a.owner && a.request_id === row.request_id && a.revision === row.request_revision))) && !status?.locked
    const decision = row.options.length > 0
    return jsxs('article', { 'data-panel-row': row.key, 'data-ask-row': row.source,
      style: { border: '1px solid var(--border)', borderLeft: `3px solid ${SECTION_COLOR.needs_you}`, borderRadius: 8, padding: '10px 12px', marginBottom: 8, minWidth: 0, display: 'grid', gap: 8 }, children: [
      jsxs('div', { style: { display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0 }, children: [
        jsx('span', { 'data-ask-kind': '', style: { fontSize: 10.5, fontWeight: 700, letterSpacing: 0.5, textTransform: 'uppercase', color: ALERT_TEXT, whiteSpace: 'nowrap' }, children: decision ? 'Needs your decision' : 'Needs you' }),
        row.id && jsx('span', { 'data-ask-card': '', style: { ...muted, marginLeft: 'auto', fontSize: 10.5, fontFamily: 'var(--font-mono, ui-monospace, monospace)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 }, children: row.source === 'card' ? row.id : 'question' })
      ] }),
      jsx('strong', { 'data-ask-title': '', style: { fontWeight: 600, fontSize: 14, lineHeight: 1.35, overflowWrap: 'anywhere', marginTop: -4 }, children: row.title }),
      row.detail && jsx('div', { style: { ...modelSmall, ...muted }, children: row.detail }),
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
      draftOnly(row) && decision && jsx('div', { style: { ...muted, fontSize: 11 }, children: 'Choosing drafts your answer; send it in the chat to decide.' }),
      status?.text && jsx('div', { role: 'status', style: { ...modelSmall, color: status.locked ? 'var(--foreground)' : ALERT_TEXT }, children: status.text }),
      status?.locked && onCheck && jsx('button', { type: 'button', style: { ...ghostBtn, justifySelf: 'start', cursor: 'pointer' }, onClick: () => onCheck(row), children: 'Check status' }),
      (row.details?.length > 0 || row.actions.some(a => a.kind === 'open_card')) && jsxs('div', { style: { display: 'flex', alignItems: 'flex-start', gap: 12, minWidth: 0 }, children: [
        details(row), ...openLinks(row)
      ] })
    ] }, row.key)
  }
  // Every other section: one compact line per row — state box, title, short detail; details/open on the right.
  // In progress keeps the old one-line shape: title, then where · model · how long right-aligned beside it.
  const lineRow = (row, section) => {
    const inline = (section === 'running' || section === 'done') && !!row.detail
    return jsxs('article', { 'data-panel-row': row.key, 'data-line-row': '',
      style: { display: 'grid', gridTemplateColumns: '12px minmax(0,1fr) auto', columnGap: 8, alignItems: 'start', padding: '3px 4px', fontSize: 13, minWidth: 0 }, children: [
      jsx('span', { 'aria-hidden': true, 'data-row-pip': section, style: { ...box(section), justifySelf: 'center', marginTop: 5 } }),
      jsxs('div', { style: { minWidth: 0 }, children: [
        jsxs('div', { style: { display: 'flex', alignItems: 'baseline', gap: 10, minWidth: 0 }, children: [
          jsx('strong', { 'data-ask-title': '', title: row.title, style: { flex: 1, minWidth: 0, fontWeight: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: row.title }),
          inline && jsx('span', { 'data-row-detail': '', 'data-run-meta': '', title: row.details?.join('\n') || row.detail,
            style: { ...muted, flex: 'none', whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums', ...(row.quiet ? { color: SECTION_COLOR.stuck } : {}) }, children: row.detail })
        ] }),
        !inline && row.detail && jsx('div', { 'data-row-detail': '', style: { ...muted, overflowWrap: 'anywhere', ...(section === 'stuck' ? { color: SECTION_COLOR.stuck } : {}) }, children: row.detail }),
        details(row)
      ] }),
      jsx('span', { style: { display: 'flex', gap: 8, paddingTop: 2 }, children: openLinks(row) })
    ] }, row.key)
  }
  return jsxs('section', { 'data-session-panel': '', 'data-hub-model': model.revision, 'aria-label': 'Session status',
    style: { minWidth: 0, width: '100%', color: 'var(--foreground)', maxHeight: '55vh', overflowY: 'auto', padding: '6px 12px 8px', boxSizing: 'border-box',
      borderBottom: '1px solid color-mix(in srgb, var(--border) 60%, transparent)', userSelect: 'text', WebkitUserSelect: 'text' }, children: [
    jsx('style', { 'data-panel-css': '', children: PANEL_CSS }),
    jsxs('header', { style: { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', fontSize: 12, lineHeight: '18px', minWidth: 0 }, children: [
      jsxs('span', { style: { display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--muted-foreground)' }, children: [
        jsx('span', { 'aria-hidden': true, style: { width: 7, height: 7, borderRadius: '50%', background: alert ? SECTION_COLOR.needs_you : (model.header.counts.running ? SECTION_COLOR.running : SECTION_COLOR.done) } }),
        jsx('span', { children: 'This session' })
      ] }),
      jsx('span', { 'data-session-status': '', title: STATE_LABEL[state] || state, style: { color: alert ? ALERT_TEXT : 'var(--muted-foreground)', fontWeight: alert ? 600 : 400 }, children: model.header.state }),
      ...model.header.pips.map((pip, i) => jsxs('span', { 'data-model-pip': pip.section, style: { display: 'inline-flex', alignItems: 'center', gap: 5, color: pip.section === 'needs_you' ? ALERT_TEXT : pip.section === 'stuck' ? SECTION_COLOR.stuck : 'var(--muted-foreground)', fontWeight: pip.section === 'needs_you' || pip.section === 'stuck' ? 600 : 400 }, children: [
        jsx('span', { 'aria-hidden': true, style: box(pip.section) }),
        `${pip.count} ${pip.label}`
      ] }, i)),
      // Counts are the hub's values, not row lengths, even if a test deliberately makes them disagree.
      ...MODEL_SECTIONS.map(([section]) => jsx('span', { 'data-model-count': section, hidden: true, children: model.header.counts[section] }, section))
    ] }),
    ...MODEL_SECTIONS.map(([section, label]) => {
      if (!model.sections[section].length) return false
      const heading = jsx('h3', { 'data-section': section.replaceAll('_', '-'), style: { display: 'inline', fontSize: 11, fontWeight: 700, letterSpacing: 0.4, textTransform: 'uppercase', margin: 0, color: section === 'needs_you' ? ALERT_TEXT : section === 'stuck' ? SECTION_COLOR.stuck : 'var(--muted-foreground)' }, children: `${label} · ${model.header.counts[section]}` })
      const rows = model.sections[section].map(row => section === 'needs_you' ? askRow(row) : lineRow(row, section))
      // Finished history stays one click away (closed by default), as before the cutover.
      if (section === 'done') return jsxs('details', { 'data-model-section': section, style: { marginTop: 12 }, children: [
        jsx('summary', { style: { margin: '0 0 6px', listStylePosition: 'inside', color: 'var(--muted-foreground)' }, children: heading }), ...rows
      ] }, section)
      return jsxs('div', { 'data-model-section': section, children: [jsx('div', { style: { margin: '12px 0 6px' }, children: heading }), ...rows] }, section)
    })
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
  if (draftOnly(row)) {
    if (routeKey(panelRoute()) !== routeKey(sourceRoute)) return
    await host.composer?.setDraft?.(sourceRoute.session, chooseText(row, option))
    host.composer?.focus?.(sourceRoute.session)
    return
  }
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
      if (!t?.id) return
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

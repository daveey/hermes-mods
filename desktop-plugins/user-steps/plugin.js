/**
 * Session decisions: one canonical v2 queue above the composer. Transcript directives
 * are passive receipts; legacy steps/cards are read-only references with a draft-only
 * request for complete context. Authorization uses durable owner-routed REST operations,
 * never composer.submit plus resolve or generic card unblocking. Existing links,
 * preview navigation and separately collapsed agent issues remain available.
 * Withdrawn (moot) asks leave the queue; "Not needed" withdraws an undecided ask
 * on its owner without deciding it, so nothing is sent to the agent.
 */
import * as latencySdk from '@hermes/plugin-sdk'
import { COMPOSER_AREAS, TRANSCRIPT_DIRECTIVE_AREA, host, useValue, MessageTextContent } from '@hermes/plugin-sdk'
import { useEffect, useState, useRef } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'user-steps'
const VERSION = 18
// The composer chrome is user-select:none app-wide; the companion queue and the in-message box must stay copyable.
const SELECTABLE = { userSelect: 'text', WebkitUserSelect: 'text', cursor: 'auto' }
const POLL_MS = 8_000
const BASE = '/api/plugins/user-steps'

const str = v => (typeof v === 'string' ? v : v == null ? '' : String(v))
const log = (...a) => console.info(`[${ID}]`, ...a)

// ── pure helpers (exported for tests) ───────────────────────────────────────

// Same link rules as blocked-panel (kept in sync by both test files).
// Web URLs, and absolute / home-relative local paths that start a word (so
// "7/10" or "and/or" never match). Trailing sentence punctuation is not part of the link.
// Scheme-less web addresses ("github.com/settings/billing", "www.x.io") link too, but only on a common TLD so
// file names like plugin.js / README.md never do; "user@host.com" doesn't (no word start).
const LINK_RE = /(https?:\/\/[^\s<>"'`]+)|(?:^|(?<=[\s("'`]))(~?\/[A-Za-z0-9._~-][^\s<>"'`()]*)|(?:^|(?<=[\s("'`]))((?:[A-Za-z0-9-]+\.)+(?:com|org|net|io|dev|ai|app|co|in|gov|edu|me|sh|so|gg|tv|us|uk|xyz)(?=[\/\s.,;:!?)\]}"'`]|$)(?:\/[^\s<>"'`]*)?)/g
/** href for a link segment: bare web addresses get https://, paths file://. */
export const linkHref = x => (x.kind === 'path' ? `file://${x.value}` : /^https?:\/\//i.test(x.value) ? x.value : `https://${x.value}`)
const TRAIL_RE = /[.,;:!?)\]}]+$/
const HAS_EXT_RE = /\.[A-Za-z0-9]{1,8}$/
/** A local path the space-delimited match cut short ("~/d/Morning" of "~/d/Morning Digest Board.html"):
 *  inside `backticks` the whole span to the closing backtick is the path; otherwise, when the cut part has
 *  no extension, up to 5 following words that each start with a capital or digit join it if the last one
 *  ends in an extension. Lowercase prose ("~/code/foo and bar.txt") never joins. */
export function widenPath(s, start, value) {
  if (s[start - 1] === '`') {
    const close = s.indexOf('`', start)
    const nl = s.indexOf('\n', start)
    if (close > start && (nl < 0 || close < nl) && !/[<>"]/.test(s.slice(start, close))) return s.slice(start, close)
  }
  if (HAS_EXT_RE.test(value.split('/').pop())) return value
  let pos = start + value.length
  let out = value
  for (let i = 0; i < 5; i++) {
    const w = s.slice(pos).match(/^ ([A-Z0-9][^\s<>"'`()/]*)/)
    if (!w) break
    const trail = w[1].match(TRAIL_RE)?.[0] || ''
    const word = trail ? w[1].slice(0, -trail.length) : w[1]
    out += ' ' + word
    if (HAS_EXT_RE.test(word)) return out
    if (trail) break
    pos += w[0].length
  }
  return value
}

/** text → [{ kind: 'text'|'url'|'path', value }] for rendering clickable links. */
export function linkSegments(text) {
  const s = str(text)
  const out = []
  let last = 0
  for (const m of s.matchAll(LINK_RE)) {
    let value = m[0]
    const trail = value.match(TRAIL_RE)?.[0] || ''
    if (trail) value = value.slice(0, -trail.length)
    const kind = m[2] ? 'path' : 'url'
    if (m.index < last) continue
    if (kind === 'path') value = widenPath(s, m.index, value)
    if (kind === 'path' && !/[A-Za-z]/.test(value)) continue
    if (m.index > last) out.push({ kind: 'text', value: s.slice(last, m.index) })
    out.push({ kind, value })
    last = m.index + value.length
  }
  if (last < s.length) out.push({ kind: 'text', value: s.slice(last) })
  return out
}

/** Inverse of the agent plugin's core.enc (attr-safe escapes). */
export function decodeAttr(s) {
  const map = { '\\': '\\', n: '\n', q: '"', '(': '{', ')': '}' }
  let out = ''
  const t = str(s)
  for (let i = 0; i < t.length; i++) {
    if (t[i] === '\\' && i + 1 < t.length && map[t[i + 1]] !== undefined) {
      out += map[t[i + 1]]
      i++
    } else out += t[i]
  }
  return out
}

export const directiveIds = attrs => str(attrs?.id).split(',').map(x => x.trim()).filter(x => /^us_[0-9a-f]{6,32}$/.test(x))

const short = (s, n = 140) => {
  const t = str(s).replace(/\s+/g, ' ').trim()
  return t.length > n ? `${t.slice(0, n - 1)}…` : t
}

// ── data ────────────────────────────────────────────────────────────────────

function activeConnection() {
  try {
    return host.activeConnectionId?.() || host.state?.connectionId?.get?.() || undefined
  } catch {
    return undefined
  }
}

function api(path, opts = {}) {
  const bridge = typeof window !== 'undefined' ? window.hermesDesktop?.api : null
  if (!bridge) return Promise.reject(new Error('Hermes desktop bridge unavailable'))
  const connectionId = opts.connectionId || activeConnection()
  return bridge({ path, timeoutMs: 10_000, ...opts, ...(connectionId ? { connectionId } : {}) })
}

const focusedId = () => {
  try {
    return host.state.focusedStoredSessionId.get() || null
  } catch {
    return null
  }
}

let snapshot = { stored: null, ledger: { steps: [], card: null }, kanban: [], issues: [], ok: null }
const subs = new Set()
let running = null // scope of the read in flight; a read for another chat starts at once instead of queueing
let rerun = false
let unsubKanban = null
const stepCache = new Map() // id → { at, step }
// Last-known ledger/kanban/issues per chat (memory, newest LEDGER_CACHE_MAX), painted on a switch before the read.
const LEDGER_CACHE_MAX = 100
const ledgerCache = new Map()


const emit = () => subs.forEach(fn => fn(snapshot))

function blockedPanel() {
  return typeof window !== 'undefined' ? window.__blockedPanel : null
}
/** A decision, withdrawal or list answer can change a Kanban card (its ask delivered, the card unblocked): have
 *  blocked-panel read the cards itself now (forced: never from a neighbour's copy taken before the change). */
export function refreshBlockedAfterAction(bp = blockedPanel()) {
  try { if (typeof bp?.refresh === 'function') { void bp.refresh(true); return true } } catch {}
  return false
}
const matchingBlocked = (s, route) => s?.stored === route.session && (!s.connectionId || s.connectionId === route.connectionId) && (!s.profile || s.profile === route.profile)

function attachKanban() {
  if (unsubKanban) return
  const bp = blockedPanel()
  if (typeof bp?.subscribe !== 'function') return
  unsubKanban = bp.subscribe(s => {
    const match = matchingBlocked(s, decisionRoute()) && snapshot.scope === routeKey(decisionRoute())
    snapshot = { ...snapshot, kanban: match ? s.items || [] : [], issues: match ? s.issues || [] : [] }
    emit()
  })
}

/** A switch paints the chat's last-known ledger at once, even while another chat's read is still running. */
function paintCachedLedger() {
  const stored = focusedId(), scope = routeKey(decisionRoute())
  if (!stored || snapshot.scope === scope || !ledgerCache.has(scope)) return
  snapshot = { ...ledgerCache.get(scope), stored, scope }
  emit()
}

export async function refresh(call = api) {
  paintCachedLedger()
  const pending = routeKey(decisionRoute())
  if (running === pending) {
    rerun = true
    return
  }
  const me = pending
  running = me
  try {
    attachKanban()
    const stored = focusedId()
    if (!stored) {
      snapshot = { ...snapshot, stored, ledger: { steps: [], card: null } }
      return emit()
    }
    const route = decisionRoute()
    const connectionId = route.connectionId
    let ledger = snapshot.scope === routeKey(route)
      ? snapshot.ledger : { connectionId, steps: [], card: null }
    let ok = snapshot.ok
    try {
      if (host.state?.focusedSessionOwner && !connectionId) throw Error('Session owner unavailable')
      const r = await call(`${BASE}/steps?session=${encodeURIComponent(stored)}`, { ...route, timeoutMs: 6_000 })
      // A connection switch may keep the same session ID. Drop the old owner's
      // response before it can populate either the shared snapshot or the step cache.
      if (!currentRoute(route)) {
        rerun = true
        return
      }
      ledger = { connectionId, steps: Array.isArray(r?.steps) ? r.steps : [], card: r?.card || null, own_cards: Array.isArray(r?.own_cards) ? r.own_cards : [] }
      ok = true
      for (const s of ledger.steps) stepCache.set(JSON.stringify([connectionId, route.profile, stored, s.id]), { at: Date.now(), step: s })
    } catch (e) {
      ok = false
      log('steps fetch failed', e?.message || e)
    }
    if (!currentRoute(route)) return // the newer chat's own read owns the snapshot now
    const bp = blockedPanel()?.snapshot
    snapshot = {
      stored,
      scope: routeKey(route),
      ledger,
      kanban: matchingBlocked(bp, route) ? bp.items || [] : snapshot.scope === routeKey(route) ? snapshot.kanban : [],
      issues: matchingBlocked(bp, route) ? bp.issues || [] : snapshot.scope === routeKey(route) ? snapshot.issues || [] : [],
      ok
    }
    if (ok) {
      ledgerCache.delete(snapshot.scope)
      ledgerCache.set(snapshot.scope, { ledger: snapshot.ledger, kanban: snapshot.kanban, issues: snapshot.issues, ok })
      while (ledgerCache.size > LEDGER_CACHE_MAX) ledgerCache.delete(ledgerCache.keys().next().value)
    }
    emit()
  } finally {
    if (running === me) {
      running = null
      if (rerun) {
        rerun = false
        void refresh(call)
      }
    }
  }
}



/** GET /step for a directive id (cached 5 s so a re-render doesn't refetch). `call` injectable for tests. */
export async function fetchStep(id, session, call = api) {
  const route = decisionRoute()
  const key = JSON.stringify([route.connectionId, route.profile, session, id])
  const hit = stepCache.get(key)
  if (hit && Date.now() - hit.at < 5_000) return hit.step
  if (host.state?.focusedSessionOwner && !route.connectionId) throw Error('Session owner unavailable')
  const r = await call(`${BASE}/step?id=${encodeURIComponent(id)}&s=${encodeURIComponent(str(session))}`, { ...route, timeoutMs: 6_000 })
  if (!currentRoute(route)) return null
  const step = r?.step || null
  if (step) stepCache.set(key, { at: Date.now(), step })
  return step
}



// ── UI ──────────────────────────────────────────────────────────────────────

const AMBER = 'var(--warning, #d29922)'
const btn = () => ({
  minHeight: 44,
  minWidth: 44,
  padding: '2px 10px',
  borderRadius: 6,
  border: '1px solid color-mix(in srgb, var(--border, #8884) 90%, transparent)',
  background: 'transparent',
  color: 'var(--foreground, inherit)',
  fontSize: 11.5,
  lineHeight: '18px',
  cursor: 'pointer',
  fontFamily: 'inherit'
})

let osApi = null // ctx.os, set in register()
let previewApi = null // ctx.openPreview (fork core), set in register()

/** Markdown files open rendered in the app's preview pane instead of the OS handler. */
export const isMarkdownPath = p => /\.(md|markdown|mdx)$/i.test(String(p || ''))

async function openLink(value, kind) {
  if (kind === 'path' && isMarkdownPath(value) && previewApi) {
    try {
      if (await previewApi(value)) return
    } catch {
      /* fall through to the OS handler */
    }
  }
  try {
    if (await osApi?.openExternal?.(value)) return
  } catch {
    /* fall through */
  }
  try {
    if (typeof window !== 'undefined' && window.open?.(value, '_blank')) return
  } catch {
    /* fall through */
  }
  notify('error', `Couldn\u2019t open ${value}`)
}

/** Text with its URLs and local paths as clickable links (paths open with the OS handler). */
export function Linked({ text }) {
  const segs = linkSegments(text)
  if (!segs.some(x => x.kind !== 'text')) return str(text)
  return segs.map((x, i) =>
    x.kind === 'text'
      ? x.value
      : jsx('a', {
          href: linkHref(x),
          'data-user-steps-link': x.kind,
          title: x.kind === 'url' ? linkHref(x) : `Open ${x.value}`,
          onClick: e => {
            e.preventDefault()
            e.stopPropagation()
            void openLink(x.kind === 'url' ? linkHref(x) : x.value, x.kind)
          },
          style: { color: 'var(--accent-foreground, var(--primary, #58a6ff))', textDecoration: 'underline', textUnderlineOffset: 2, cursor: 'pointer', wordBreak: 'break-all' },
          children: x.value
        }, i)
  )
}

function notify(kind, message) {
  try {
    host.notify?.({ kind, message })
  } catch {
    /* older hosts */
  }
}

/** The in-message framed box for `::user-step{...}`. */
export function StepBox({ attrs, streaming, deps }) {
  useValue(host.state.focusedSessionOwner || host.state.connectionId || host.state.focusedStoredSessionId)
  const route = decisionRoute(), scope = routeKey(route)
  const ids = directiveIds(attrs)
  const fallback = decodeAttr(attrs?.t)
  const [state, setState] = useState({ loading: true, steps: [], failed: false, scope })
  const [tick, setTick] = useState(0)
  const key = ids.join(',')
  // Re-read when the shared snapshot changes: decisions and ledger refreshes update this receipt too.
  useEffect(() => {
    const fn = () => setTick(t => t + 1)
    subs.add(fn)
    return () => subs.delete(fn)
  }, [])
  useEffect(() => {
    let live = true
    setState(st => st.scope === scope ? st : { loading: true, steps: [], failed: false, scope })
    Promise.all(ids.map(id => fetchStep(id, attrs?.s, deps?.call)))
      .then(steps => {
        if (!live || !currentRoute(route)) return
        const got = steps.filter(Boolean)
        // keep a known step when a refetch fails transiently; never re-open one we saw resolved
        setState(st => (got.length || !st.steps.length ? { loading: false, steps: got, failed: steps.every(s => !s) && !st.steps.length, scope } : { ...st, loading: false }))
      })
      .catch(() => live && currentRoute(route) && setState(st => (st.steps.length ? { ...st, loading: false } : { loading: false, steps: [], failed: true, scope })))
    return () => {
      live = false
    }
  }, [key, tick, scope, attrs?.s])
  if (!ids.length || state.failed || state.scope !== scope) {
    // ledger unreachable / unknown ids: the paragraph as plain text (empty t = the text is already above)
    return fallback ? jsx('div', { 'data-user-step-fallback': '', style: { whiteSpace: 'pre-wrap' }, children: jsx(Linked, { text: fallback }) }) : null
  }
  const text = fallback || state.steps.map(s => s.text).join('\n')
  // With the session panel loaded, an answered step has exactly one status line (on its card/ask row there); the
  // transcript gets no second "Decision reference" block for it.
  if (panelActive() && state.steps.length && state.steps.every(s => (s.status || 'open') === 'open'
    && decisionRecorded({ ...attrs, gatewayId: attrs?.gatewayId || s.requestOwner?.gatewayId, profile: attrs?.profile || s.requestOwner?.profile, requestId: attrs?.requestId || s.requestId }, [s.id], scope))) return null
  return jsxs('div', {
    'data-user-step': key,
    style: {
      ...SELECTABLE,
      margin: '6px 0',
      padding: '8px 10px',
      borderRadius: 8,
      border: '1px solid var(--border)',
      background: 'transparent',
      display: 'flex',
      flexDirection: 'column',
      gap: 6
    },
    children: [
      jsxs('div', {
        key: 'h',
        style: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: AMBER, fontWeight: 600 },
        children: ['Decision reference']
      }),
      jsx('div', { key: 't', 'data-user-step-text': '', style: { whiteSpace: 'pre-wrap', lineHeight: 1.5 }, children: jsx(Linked, { text }) }),
      state.loading && jsx('div', { key: 'l', style: { fontSize: 11, opacity: 0.6 }, children: 'Loading\u2026' }),
      ...state.steps.map(s => {
        // Each step's receipt follows its own request: a withdrawn sibling in the same directive never relabels it.
        const receipt = { ...attrs, gatewayId: attrs?.gatewayId || s.requestOwner?.gatewayId, profile: attrs?.profile || s.requestOwner?.profile, requestId: attrs?.requestId || s.requestId }
        if ((s.status || 'open') === 'open')
          return jsxs('div', {
            style: { display: 'flex', flexDirection: 'column', gap: 4 },
            children: [
              state.steps.length > 1 && jsx('div', { style: { fontSize: 12, opacity: 0.85 }, children: jsx(Linked, { text: s.text }) }),
              jsx('small', { children: receiptStatus(receipt, [s.id], scope) })
            ]
          }, s.id)
        // A withdrawal also retires the ledger step; it reads as no longer needed, never as a completed (✓) decision.
        const linked = linkedRequest(receipt, [s.id], scope)
        const status = linked?.state === 'withdrawn' ? withdrawnReceipt(linked) : `\u2713 ${s.resolution || 'Resolved'}`
        return jsx('div', { 'data-user-step-resolved': s.id, style: { fontSize: 11.5, opacity: 0.65 }, children: `${status}${state.steps.length > 1 ? ` \u2014 ${s.text}` : ''}` }, s.id)
      })
    ]
  })
}

const rowStyle = { display: 'flex', flexDirection: 'column', gap: 4, padding: '6px 0', borderTop: '1px solid color-mix(in srgb, var(--border, #8884) 60%, transparent)' }

// Optional on older Desktop builds. Never let telemetry change decision submission or retain answer text.
export function decisionTiming(owner) {
  const name = 'ui.decision_submit'
  let token
  try {
    if (typeof latencySdk.startUiLatency === 'function') token = latencySdk.startUiLatency(name, {
      gateway: owner.gatewayId, profile: owner.profile, session_id: owner.storedSessionId,
    })
  } catch { /* telemetry is best effort */ }
  const invoke = method => {
    if (token === undefined) return
    try { latencySdk[method]?.(name, ...(method === 'completeUiLatency' ? [{}, token] : [token])) } catch { /* telemetry is best effort */ }
  }
  return { complete: () => invoke('completeUiLatency'), cancel: () => invoke('cancelUiLatency') }
}

// Canonical v2 decisions. The server, not composer text or card status, records authorization.
export const requestKey = r => r?.owner?.gatewayId && r?.owner?.profile && r?.requestId
  ? JSON.stringify([r.owner.gatewayId, r.owner.profile, r.requestId]) : null
export const sourceKey = r => r?.gatewayId && r?.profile && r?.id && ['message', 'user_step', 'kanban'].includes(r.type) && (r.type !== 'kanban' || r.board)
  ? JSON.stringify([r.gatewayId, r.profile, r.type, r.type === 'kanban' ? r.board : '', r.id]) : null
// A limit's permissionRequired is a boolean flag (backend core.py validates it as one since 4affdc54); requests stored
// before that carry a non-empty string naming the permission. Both are valid: a stale contract here rejected the
// whole queue ("Invalid action request owner or schema") for one flagged row, hiding every ask in that chat.
const validPermission = p => p === undefined || typeof p === 'boolean' || (typeof p === 'string' && !!p.trim())
// Approve grants a boolean-flagged limit (core.py permissionGrants, 2026-11-24): the card says so. A legacy string flag
// names a permission outside this request and stays separate.
// Only an Approve grants; a request with no approve choice (a go answer to a question) grants nothing, so says so.
const permissionNote = (p, approvable = true) => p === true ? (approvable ? ' — permission: granted if you approve' : ' — needs separate permission') : typeof p === 'string' && p.trim() ? ` — separately requires ${p}` : ''
export function validRequest(r) {
  const text = s => typeof s === 'string' && !!s.trim()
  return !!requestKey(r) && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(r.requestId) &&
    r.schemaVersion === 2 && Number.isInteger(r.revision) && r.revision >= 1 && text(r.owner.storedSessionId) &&
    ['approval','choice','input','external_step'].includes(r.kind) && text(r.title) &&
    ['needs_context','open','resolved','superseded','cancelled','withdrawn'].includes(r.state) &&
    typeof r.scope?.markdown === 'string' && typeof r.scope.complete === 'boolean' &&
    Array.isArray(r.scope.sourceRefs) && r.scope.sourceRefs.every(sourceKey) &&
    Array.isArray(r.aliases) && r.aliases.every(sourceKey) &&
    Array.isArray(r.limits) && r.limits.every(l => text(l?.key) && text(l.value) && validPermission(l.permissionRequired)) &&
    Array.isArray(r.exclusions) && r.exclusions.every(text) &&
    (text(r.outcomes?.revise) || (['choice','input','external_step'].includes(r.kind) && text(r.outcomes?.answer))) &&
    Array.isArray(r.choices) && r.choices.length > 0 && new Set(r.choices.map(c => c?.id)).size === r.choices.length &&
    r.choices.every(c => text(c?.id) && text(c.label) && ['approve','revise','decline','answer'].includes(c.effect) &&
      (c.explain === undefined || text(c.explain)) && (c.recommended === undefined || c.recommended === true)) &&
    r.choices.filter(c => c.recommended === true).length <= 1
}
export function projectRequests(requests) {
  const byId = new Map()
  for (const r of Array.isArray(requests) ? requests : []) {
    const key = requestKey(r)
    if (!key || !validRequest(r)) continue
    if (!byId.has(key) || byId.get(key).revision < r.revision) byId.set(key, r)
  }
  return [...byId.values()]
}
// A serving-gateway card row carries no gateway id (the backend tags only other gateways' rows), so it has no
// sourceKey. The owner's own bound question (server presentation.sourceRef, same gateway as its owner) still covers
// it when profile, board and id match exactly. Aliases never match without the full key.
const untaggedCard = s => s?.type === 'kanban' && !s.gatewayId && !!s.profile && !!s.id
const boundToUntagged = (r, s) => {
  const ref = cardPresentation(r)?.sourceRef
  return ref?.type === 'kanban' && ref.gatewayId === r.owner.gatewayId && ref.profile === r.owner.profile &&
    ref.profile === s.profile && (ref.board || 'default') === (s.board || 'default') && ref.id === s.id
}
const liveFirst = matches => { const live = matches.filter(r => r.state !== 'withdrawn'); return live.length ? live : matches }
export function requestForSource(requests, source) {
  if (untaggedCard(source)) { const pool = liveFirst(projectRequests(requests).filter(r => boundToUntagged(r, source))); return pool.length === 1 ? pool[0] : null }
  const key = sourceKey(source)
  if (!key) return null
  const matches = projectRequests(requests).filter(r => {
    // The mirror's display identity is server-bound; arbitrary scope.sourceRefs
    // are only citations and must never suppress another card. This is UI dedup,
    // not decision authority, and deliberately survives local Skip.
    const ref = cardPresentation(r)?.sourceRef
    return (r.aliases || []).some(a => sourceKey(a) === key) ||
      (ref?.type === 'kanban' && ref.gatewayId === r.owner.gatewayId && ref.profile === r.owner.profile && sourceKey(ref) === key)
  })
  // A withdrawn ask still retires its own references (they never resurface as legacy asks or blocked chips),
  // but never competes with a live request that claims the same source.
  const live = matches.filter(r => r.state !== 'withdrawn')
  const pool = live.length ? live : matches
  return pool.length === 1 ? pool[0] : null // ambiguous provenance never suppresses another request
}
// Every request bound (server presentation.sourceRef) to this card, in list order. Bound requests are each
// drawn as their own tile, so several asks on one card are all shown and the card is never also a legacy row.
// Without a bound request this falls back to the singular alias match (ambiguous aliases still match nothing).
export function requestsForSource(requests, source) {
  if (untaggedCard(source)) return liveFirst(projectRequests(requests).filter(r => boundToUntagged(r, source)))
  const key = sourceKey(source)
  if (!key) return []
  const bound = projectRequests(requests).filter(r => {
    const ref = cardPresentation(r)?.sourceRef
    return ref?.type === 'kanban' && ref.gatewayId === r.owner.gatewayId && ref.profile === r.owner.profile && sourceKey(ref) === key
  })
  const live = bound.filter(r => r.state !== 'withdrawn')
  if (bound.length) return live.length ? live : bound
  const one = requestForSource(requests, source)
  return one ? [one] : []
}
function decisionRoute() {
  const owner = host.state?.focusedSessionOwner?.get?.()
  return { connectionId: owner ? owner.connectionId : activeConnection(), profile: owner?.targetProfile || owner?.profile || host.state?.profile?.get?.() || 'default', session: focusedId() }
}
const routeKey = r => JSON.stringify([r.connectionId || '', r.profile, r.session])
const currentRoute = route => routeKey(route) === routeKey(decisionRoute())
const operations = new Map() // authoritative readbacks shared by mounted tiles
const INTENT_PREFIX = 'hermes.actionIntent.v2:'
// Persist only retry intent, NOT authorization or delivery success. Every recovery queries the owner.
const cardPresentation = r => r?.presentation?.kind === 'kanban-question' && typeof r.presentation.question === 'string' && typeof r.presentation.details === 'string' ? r.presentation : null
function recoverIntent(key, request) {
  try {
    const d = JSON.parse(window.localStorage.getItem(INTENT_PREFIX + key) || 'null')
    return d?.session === request.owner.storedSessionId && d.expectedRevision === request.revision && typeof d.idempotencyKey === 'string' && (request.choices.some(c => c.id === d.choiceId) || (cardPresentation(request)?.other === true && d.choiceId === 'answer-text')) ? d : null
  } catch { return null }
}
function rememberIntent(key, decision) {
  if (typeof window !== 'undefined') window.localStorage.setItem(INTENT_PREFIX + key, JSON.stringify(decision))
}
let canonical = { scope: null, requests: [], error: null }
// Last confirmed request list per chat (memory only, newest CANONICAL_CACHE_MAX chats). A switch paints it at once
// marked `stale`: counts and rows show immediately, but every decision control stays disabled until this chat's
// own read lands, so nothing is ever answered from a cached revision.
const CANONICAL_CACHE_MAX = 100
const canonicalCache = new Map()

const canonicalSubs = new Set()
const readbacks = new Set()
let requestRead = 0
/** The canonical request a transcript directive refers to: exact owner plus request ID or a qualified user_step alias. */
function linkedRequest(attrs, ids, scope) {
  if (canonical.scope !== scope) return null
  return canonical.requests.find(r => r.owner.storedSessionId === attrs?.s && r.owner.gatewayId === attrs?.gatewayId && r.owner.profile === attrs?.profile && (r.requestId === attrs?.requestId || (r.aliases || []).some(a => a.type === 'user_step' && ids.includes(a.id) && a.gatewayId === attrs.gatewayId && a.profile === attrs.profile))) || null
}
const withdrawnReceipt = r => {
  const reason = typeof r?.withdrawal?.reason === 'string' ? short(r.withdrawal.reason, 300) : ''
  return reason ? `No longer needed \u2014 ${reason}` : 'No longer needed.'
}
function decisionRecorded(attrs, ids, scope) {
  if (canonical.scope !== scope) return false
  const linked = linkedRequest(attrs, ids, scope)
  if (!linked || linked.state === 'withdrawn') return false
  const op = operations.get(`${scope}:${requestKey(linked)}`)
  return !!(linked.resolution || op?.decision) && op?.phase !== 'cancelled'
}
function receiptStatus(attrs, ids, scope) {
  if (canonical.scope !== scope) return 'Reference only — review and decide above the composer.'
  const linked = linkedRequest(attrs, ids, scope)
  if (linked?.state === 'withdrawn') return withdrawnReceipt(linked)
  const state = linked && operations.get(`${scope}:${requestKey(linked)}`)
  if (state?.phase === 'cancelled') return 'Decision not started — no permission was delivered. A replacement revision requires a fresh decision.'
  return state?.phase === 'confirmed' ? 'Confirmed — decision delivered.' : linked?.resolution || state?.decision ? 'Decision recorded; delivery status is above the composer.' : 'Reference only — review and decide above the composer.'
}
function signalDecisionChanges() {
  if (typeof window !== 'undefined' && window.dispatchEvent) window.dispatchEvent(new window.Event('hermes:user-steps-changed'))
}
const publishCanonical = () => {
  if (canonical.scope && !canonical.stale && !canonical.error) {
    canonicalCache.delete(canonical.scope)
    canonicalCache.set(canonical.scope, canonical.requests)
    while (canonicalCache.size > CANONICAL_CACHE_MAX) canonicalCache.delete(canonicalCache.keys().next().value)
  }
  canonicalSubs.forEach(fn => fn(canonical)); signalDecisionChanges(); emit()
}
// Owner capabilities: GET /ping at most once per route scope per FEATURE_TTL_MS. A failed ping is not cached (the
// next tile mount asks again) and an answer expires, so an owner that restarts onto a newer plugin is noticed.
const FEATURE_TTL_MS = 5 * 60_000
const pings = new Map() // routeKey -> { at, promise: Promise<string[]> }
const pinged = new Map() // routeKey -> { at, list: string[] } once answered
const freshFeatures = scope => { const p = pinged.get(scope); return p && Date.now() - p.at <= FEATURE_TTL_MS ? p.list : null }
function ownerFeatures(route, call = api) {
  const scope = routeKey(route), cached = pings.get(scope)
  if (cached && Date.now() - cached.at <= FEATURE_TTL_MS) return cached.promise
  const entry = { at: Date.now(), promise: null }
  entry.promise = (async () => {
    try {
      if (host.state?.focusedSessionOwner && !route.connectionId) throw Error('Session owner unavailable')
      const r = await call(`${BASE}/ping`, { ...route, timeoutMs: 6_000 })
      const list = Array.isArray(r?.features) ? r.features.filter(f => typeof f === 'string') : []
      pinged.set(scope, { at: Date.now(), list })
      return list
    } catch (e) {
      log('ping failed', e?.message || e)
      if (pings.get(scope) === entry) pings.delete(scope)
      return []
    }
  })()
  pings.set(scope, entry)
  return entry.promise
}

/** Background warm for a chat that is NOT focused: read its asks and ledger into the per-chat caches only. A cached
 *  ask is painted stale with every decision control locked until the chat's own read lands, so warming never makes
 *  anything answerable. Invalid or failed reads leave the caches alone. */
export async function warm(route, call = api) {
  if (!route?.connectionId || !route?.session) return false
  const scope = routeKey(route)
  if (scope === routeKey(decisionRoute())) return false // the focused chat has its own live reads
  const [steps, reqs] = await Promise.all([
    call(`${BASE}/steps?session=${encodeURIComponent(route.session)}`, { ...route, timeoutMs: 6_000 }).catch(() => null),
    call(`${BASE}/requests?session=${encodeURIComponent(route.session)}&include=withdrawn`, route).catch(() => null)
  ])
  let ok = false
  if (steps && Array.isArray(steps.steps)) {
    const prev = ledgerCache.get(scope)
    ledgerCache.delete(scope)
    ledgerCache.set(scope, { ledger: { connectionId: route.connectionId, steps: steps.steps, card: steps.card || null, own_cards: Array.isArray(steps.own_cards) ? steps.own_cards : [] }, kanban: prev?.kanban || [], issues: prev?.issues || [], ok: true })
    while (ledgerCache.size > LEDGER_CACHE_MAX) ledgerCache.delete(ledgerCache.keys().next().value)
    ok = true
  }
  if (reqs && Array.isArray(reqs.requests)) {
    const requests = projectRequests(reqs.requests)
    const valid = requests.length === new Set(reqs.requests.map(requestKey)).size && requests.every(r => r.owner.storedSessionId === route.session && r.owner.profile === route.profile)
    if (valid) {
      canonicalCache.delete(scope)
      canonicalCache.set(scope, requests)
      while (canonicalCache.size > CANONICAL_CACHE_MAX) canonicalCache.delete(canonicalCache.keys().next().value)
      ok = true
    }
  }
  return ok
}


export async function refreshRequests(call = api) {
  const generation = ++requestRead
  const route = decisionRoute(), scope = routeKey(route)
  if (!route.session) return
  if (canonical.scope !== scope && canonicalCache.has(scope)) {
    canonical = { scope, requests: canonicalCache.get(scope), error: null, stale: true }
    publishCanonical()
  }
  try {
    if (host.state?.focusedSessionOwner && !route.connectionId) throw Error('Session owner unavailable')
    // Withdrawn rows are requested so receipts and alias suppression know them; the queue never shows them.
    const result = await call(`${BASE}/requests?session=${encodeURIComponent(route.session)}&include=withdrawn`, route)
    if (!currentRoute(route) || generation !== requestRead) return
    if (!Array.isArray(result?.requests)) throw Error('Invalid action queue response')
    const requests = projectRequests(result.requests)
    if (requests.length !== new Set(result.requests.map(requestKey)).size || requests.some(r => r.owner.storedSessionId !== route.session || r.owner.profile !== route.profile)) throw Error('Invalid action request owner or schema')
    canonical = { scope, requests, error: null }
    // A withdrawn request can carry no decision for its revision: a retry intent saved for it is dead.
    for (const r of requests) {
      const key = `${scope}:${requestKey(r)}`
      if (r.state === 'withdrawn' && !r.resolution && recoverIntent(key, r)) { try { window.localStorage.removeItem(INTENT_PREFIX + key) } catch {} }
    }
    publishCanonical()
    readbacks.forEach(check => check())
  } catch (e) {
    if (!currentRoute(route) || generation !== requestRead) return
    // A new chat has no stored row until its first turn lands: it cannot own asks yet, so this is an
    // empty queue, not an outage (the panel used to flash an error on every new session).
    // A profile without the user-steps backend (bot profiles enable only guards) answers 404 "Plugin not
    // found": nothing there can mint asks, so it is an empty queue too. A bare "Not Found" still surfaces.
    if (errorStatus(e) === 404 && /Unknown stored session|Plugin not found/.test(str(e?.message))) {
      canonical = { scope, requests: [], error: null }
      return publishCanonical()
    }
    // The Desktop deliberately skips timer polls of /api/plugins/* while the backend link is slow or the Mac is
    // settling after a wake (SlowLinkHeldError/PowerSettlingError; IPC strips `code`, the name survives in the
    // message). That is "no new answer yet", not an outage: keep the last authoritative queue; the next poll retries.
    if (isSkippedRead(e)) return
    const reason = /Timed out connecting to Hermes backend/i.test(str(e?.message))
      ? 'Hermes did not return the action queue in time.'
      : /lane_busy/.test(str(e?.message))
        ? 'Hermes is busy with other feed reads; this queue read did not start.'
        : 'Hermes could not read the action queue.'
    canonical = { scope, requests: canonical.scope === scope ? canonical.requests : [], error: `Action queue unavailable on ${route.connectionId || 'this device'} (${route.profile || 'default'}): ${reason} Choose Reload actions to retry this read; it does not submit or resend a decision. Earlier asks remain unavailable until the queue reloads. Details: ${e.message}` }
    publishCanonical()
  }
}
const operationId = op => op?.operationId || op?.id
// Owner-side phase of a delivered decision (v1 owner receipts). Delivery is transport admission only: it never
// implies the agent acknowledged or applied it. Anything malformed or absent stays 'unknown'; nothing is inferred.
const OWNER_PHASES = ['unknown', 'acknowledged', 'uncertain', 'applied', 'not_applied']
export const ownerPhase = consumer => {
  if (!consumer || !OWNER_PHASES.includes(consumer.phase) || !Array.isArray(consumer.receipts)) return 'unknown'
  if (consumer.phase !== 'unknown' && consumer.phase !== 'uncertain' && !consumer.receipts.some(x => x?.phase === consumer.phase)) return 'unknown'
  if (consumer.phase === 'uncertain' && !consumer.receipts.some(x => x?.phase === 'reserved')) return 'unknown'
  if (consumer.phase === 'not_applied' && consumer.receipts.some(x => x?.phase === 'reserved')) return 'uncertain' // a reservation is never shown as a denial
  if (consumer.phase === 'applied' && !consumer.receipts.some(x => x?.phase === 'applied' && Array.isArray(x.evidence) && x.evidence.length)) return 'unknown'
  return consumer.phase
}
export const ownerPhaseText = consumer => {
  const phase = ownerPhase(consumer)
  const applied = phase === 'applied' && consumer.receipts.find(x => x.phase === 'applied')
  const notApplied = phase === 'not_applied' && consumer.receipts.find(x => x.phase === 'not_applied')
  return {
    unknown: 'No owner acknowledgment recorded.',
    acknowledged: 'Agent acknowledged this exact revision; not yet applied.',
    uncertain: 'Agent reserved the application before acting; whether it was applied is uncertain.',
    // Evidence references are the agent's own claim: this surface never calls them verified.
    applied: applied ? `Agent reports it applied (${applied.evidence.map(e => `${e.type} ${e.id}`).join(', ')}); agent-reported, unverified.` : '',
    not_applied: notApplied ? `Agent did not apply it: ${notApplied.reason || 'no reason given'}.` : '',
  }[phase]
}
const operationState = op => { const s = op?.dispatchState || op?.state || op?.status; return s === 'delivery_failed' ? 'failed' : s }
const isSkippedRead = e => e?.code === 'POWER_SETTLING' || /PowerSettlingError:/.test(str(e?.message))
const errorStatus = e => e?.status || e?.statusCode || e?.response?.status || Number(str(e?.message).match(/\b(400|403|404|409|422)\b/)?.[1])
const actionStyle = { ...btn(), minHeight: 44, minWidth: 44, maxWidth: '100%', whiteSpace: 'normal', overflowWrap: 'anywhere' }
const flowStyle = { display: 'flex', flexWrap: 'wrap', gap: 8 }
const planStyle = { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', minWidth: 0 }
const needsText = c => c.effect === 'revise' || (c.effect === 'answer' && c.id === 'answer-text')
// Missing plan/step = agent work. It never counts as something "for you".
export const agentPending = r => !!r.resolution || !['choice','input'].includes(r.kind) && (!r.scope?.complete || !r.scope?.markdown?.trim() || r.state !== 'open' || (r.kind === 'approval' && !(r.choices || []).some(c => c.effect === 'approve')))
const choiceLabel = (c, choices, kind) => kind !== 'approval' ? (c.authorizes === true ? `✓ ${c.label}` : c.label) : c.effect === 'revise' && c.label !== 'Change plan' && choices.filter(x => x.effect === 'revise').length === 1 ? 'Request changes' : c.label
const WITHDRAW_REASON = 'Dismissed in Desktop as not needed'
const WITHDRAW_TITLE = 'Remove this ask without deciding; nothing is sent to the agent'
const withdrawing = new Set() // tile keys with a withdraw POST in flight: one per tile, also across two mounted tiles

function saveReadback(route, request) {
  if (!currentRoute(route)) return
  if (canonical.scope === routeKey(route)) {
    canonical = { ...canonical, requests: projectRequests(canonical.requests.some(r => requestKey(r) === requestKey(request)) ? canonical.requests.map(r => requestKey(r) === requestKey(request) ? request : r) : [...canonical.requests, request]) }
    publishCanonical()
  }
}

// A decision recorded while its session is mid-turn fails delivery ("busy"). The row retries the SAME operation on a
// timer until it lands, so an Approve never sits waiting for a manual Retry. Tests shorten the interval.
export const autoRetry = { ms: 15_000 }
const sessionBusy = op => /busy/i.test(str(op?.error))
const failedText = op => sessionBusy(op)
  ? 'Decision recorded. The session is busy with another turn; it is delivered automatically when that turn ends.'
  : 'Decision recorded; delivery unconfirmed. Work may have started. Retrying automatically with the same operation.'

// `companion`: the hub session panel draws every ask's options and posts plain-option decisions. The row then keeps
// only what the panel cannot do: free-text choices (revise / Other), the plan-less ask's composer draft, "Not needed"
// withdraw and the recorded-decision status with Check status / Retry. The panel never decides without a canonical
// request, and its decisions land here as `resolution`, so readback recovery needs no panel cooperation.
export const panelActive = () => typeof window !== 'undefined' && !!window.__sessionPanel?.active
/** Whether the hosting panel drew THIS ask (exact owner/request/revision) with its options in the same render.
 *  `coverage` comes from the mounted panel that hosts this queue; registration or a cached model alone is not
 *  coverage. A collapsed panel counts the ask in its header instead of drawing it. */
export function askCoverage(coverage, r) {
  const key = requestKey(r)
  // Mixed-version rollout: an older session panel draws the ask itself and never reports coverage. Stay silent
  // like the queue that shipped with it, not "isn't showing this chat" beside a panel that is.
  if (coverage?.state === 'legacy') return { covered: true, counted: false, reason: 'legacy' }
  if (!coverage || typeof coverage !== 'object' || !key) return { covered: false, counted: false, reason: 'unmounted' }
  if (coverage.collapsed && coverage.counted?.[key] === r.revision) return { covered: false, counted: true, reason: 'collapsed' }
  if (coverage.state === 'presented' && coverage.drawn?.[key] === r.revision) return { covered: true, counted: false, reason: 'presented' }
  return { covered: false, counted: false, reason: ['loading', 'unsupported', 'empty', 'presented'].includes(coverage.state) ? (coverage.state === 'presented' ? 'omitted' : coverage.state) : 'unmounted' }
}
export const COVERAGE_TEXT = {
  loading: 'Loading this ask\u2019s options in session status\u2026',
  unsupported: 'Session status isn\u2019t available for this chat, so this ask\u2019s options can\u2019t be shown. Nothing has been decided.',
  empty: 'Session status hasn\u2019t shown this ask yet; its options appear there once it updates. Nothing has been decided.',
  omitted: 'Session status hasn\u2019t shown this ask yet; its options appear there once it updates. Nothing has been decided.',
  unmounted: 'Session status isn\u2019t showing this chat, so this ask\u2019s options can\u2019t be shown. Nothing has been decided.'
}
export function DecisionRow({ request, route, call = api, unavailable = false, coverage = null }) {
  const key = `${routeKey(route)}:${requestKey(request)}`
  const [state, setState] = useState(() => {
    const decision = recoverIntent(key, request)
    return operations.get(key) || { request, phase: decision ? 'unknown' : 'idle', decision, text: decision?.revisionText || '' }
  })
  const [editing, setEditing] = useState(null)
  const input = useRef(null), trigger = useRef(null), live = useRef(true), article = useRef(null)
  const [dismissal, setDismissal] = useState({ busy: false, note: '', error: '' })
  const latest = useRef(request)
  latest.current = request
  useEffect(() => { live.current = true; const fn = () => { const s = operations.get(key); if (s) setState(s) }; canonicalSubs.add(fn); return () => { live.current = false; canonicalSubs.delete(fn) } }, [key])
  const update = patch => {
    if (!live.current || !currentRoute(route)) return
    const next = { ...(operations.get(key) || state), ...patch }
    operations.set(key, next); setState(next); canonicalSubs.forEach(fn => fn(canonical))
  }
  const r = state.request?.revision > request.revision ? state.request
    : request.revision > (state.request?.revision || 0) || request.resolution || !['open','needs_context'].includes(request.state) ? request : state.request || request
  useEffect(() => {
    if (request.revision > state.request.revision && !request.resolution && ['open','needs_context'].includes(request.state)) {
      update({ request, phase: 'idle', decision: null, operation: null, error: '' })
    }
  }, [request.revision])
  const locked = !!state.decision || !!r.resolution || !['open','needs_context'].includes(r.state)
  // "Not needed" retires an undecided ask on its owner. It is offered only when no decision is recorded or in
  // flight, no decision card is bound to it (that card decides it), and the owner's ping advertises withdraw.
  const withdrawable = !locked && ['open','needs_context'].includes(r.state) && !['submitting','checking'].includes(state.phase)
    && !(r.aliases || []).some(a => a?.type === 'kanban')
  const [features, setFeatures] = useState(() => freshFeatures(routeKey(route)))
  useEffect(() => {
    if (!withdrawable || features) return
    let on = true
    void ownerFeatures(route, call).then(list => { if (on) setFeatures(list) })
    return () => { on = false }
  }, [routeKey(route), withdrawable])
  const canWithdraw = withdrawable && !!features?.includes('withdraw')
  const close = () => { setEditing(null); queueMicrotask(() => trigger.current?.focus()) }
  useEffect(() => { if (editing) input.current?.focus() }, [editing])
  const query = `?session=${encodeURIComponent(route.session)}`
  const readOperation = async id => call(`${BASE}/operations/${encodeURIComponent(id)}${query}`, route)
  const readReceipts = async id => {
    try {
      const consumer = await call(`${BASE}/operations/${encodeURIComponent(id)}/receipts${query}`, route)
      if (live.current && currentRoute(route) && operationId(operations.get(key)?.operation) === id) update({ consumer: ownerPhase(consumer) === 'unknown' ? null : consumer })
    } catch {} // an older backend has no receipts route: the owner phase simply stays unknown
  }
  const findOperation = async decision => {
    try { return await call(`${BASE}/operations${query}&idempotencyKey=${encodeURIComponent(decision.idempotencyKey)}`, route) }
    catch (e) { if (errorStatus(e) === 404) return { operation: null }; throw e }
  }
  const apply = data => {
    if (!live.current || !currentRoute(route)) return
    if (!validRequest(data?.request) || requestKey(data.request) !== requestKey(request) || data.request.owner.storedSessionId !== route.session || data.request.revision < Math.max(latest.current.revision, operations.get(key)?.request?.revision || 0)) throw Error('Invalid or stale decision readback')
    if (!data.operation || !operationId(data.operation) || !['pending','failed','delivered','cancelled'].includes(operationState(data.operation))) throw Error('Invalid operation readback')
    if (data.operation.requestId && data.operation.requestId !== request.requestId) throw Error('Operation belongs to a different request')
    if (data.operation.owner && (data.operation.owner.gatewayId !== request.owner.gatewayId || data.operation.owner.profile !== route.profile || data.operation.owner.storedSessionId !== route.session)) throw Error('Operation belongs to a different owner')
    const status = operationState(data.operation)
    if (['delivered','cancelled'].includes(status) && data.request.resolution?.operationId !== operationId(data.operation)) throw Error('Delivery not yet confirmed by request readback')
    if (status === 'cancelled' && data.request.state !== 'cancelled') throw Error('Cancellation not yet confirmed by request readback')
    const retiring = remoteRetiring(data.request, data.operation)
    update({ request: data.request, operation: data.operation, phase: retiring ? 'retiring' : status === 'delivered' ? 'confirmed' : status, error: status === 'failed' ? failedText(data.operation) : '' })
    if (status === 'delivered' || (status === 'cancelled' && !retiring)) { try { window.localStorage.removeItem(INTENT_PREFIX + key) } catch {} }
    saveReadback(route, data.request)
    if (status === 'delivered') void readReceipts(operationId(data.operation))
  }
  const check = async () => {
    if (!currentRoute(route)) return
    const op = state.operation || (r.resolution && { operationId: r.resolution.operationId })
    update({ phase: 'checking', error: '' })
    try {
      if (operationId(op)) apply(await readOperation(operationId(op)))
      else if (state.decision) {
        const found = await findOperation(state.decision)
        if (!found.operation) { update({ phase: 'unknown', error: 'No operation found yet. Retry safely with the same key.' }); return }
        apply(await readOperation(operationId(found.operation)))
      } else update({ phase: 'unknown', error: 'No operation receipt is available. Reload actions.' })
    } catch (e) { update({ phase: 'unknown', error: e.message }) }
  }
  // Reconnect/remount: recorded resolution is not delivery confirmation. Read the durable operation.
  useEffect(() => { if ((r.resolution?.operationId || (state.phase === 'unknown' && state.decision)) && (!state.operation || state.phase === 'checking')) void check() }, [r.resolution?.operationId, state.decision?.idempotencyKey])
  useEffect(() => {
    const poll = () => {
      if (['pending','failed','unknown','retiring'].includes(state.phase)) void check()
      else if (state.phase === 'confirmed' && operationId(state.operation) && !['applied','not_applied'].includes(ownerPhase(state.consumer))) void readReceipts(operationId(state.operation))
    }
    readbacks.add(poll); return () => readbacks.delete(poll)
  }, [state.phase, state.operation, state.decision, state.consumer])
  useEffect(() => {
    const onStorage = e => {
      if (e.key !== INTENT_PREFIX + key || operations.get(key)?.decision) return
      const decision = recoverIntent(key, request)
      if (decision) update({ decision, phase: 'unknown', text: decision.revisionText || '' })
    }
    window.addEventListener('storage', onStorage); return () => window.removeEventListener('storage', onStorage)
  }, [key, request.revision])
  const send = async (choice, retry = false) => {
    if (!currentRoute(route) || unavailable || withdrawing.has(key) || ['submitting','checking'].includes(operations.get(key)?.phase)) return
    if (!retry && (locked || operations.get(key)?.decision || !choice || (['approve','answer'].includes(choice.effect) && (!r.scope?.complete || !r.scope?.markdown?.trim() || r.state !== 'open')))) return
    const text = choice?.effect === 'answer' && !needsText(choice) ? choice.label : state.text || ''
    if (!retry && needsText(choice) && !text.trim()) return
    let decision = retry ? state.decision : { session: route.session, expectedRevision: r.revision, choiceId: choice.id, revisionText: ['revise','answer'].includes(choice.effect) ? text.trim() : undefined, idempotencyKey: globalThis.crypto.randomUUID() }
    const knownId = operationId(state.operation) || r.resolution?.operationId
    if (retry && knownId) {
      update({ phase: 'submitting', error: '' })
      try {
        const current = await readOperation(knownId)
        if (!currentRoute(route)) return
        if (!['delivered','cancelled'].includes(operationState(current.operation))) {
          await call(`${BASE}/operations/${encodeURIComponent(knownId)}/retry${query}`, { ...route, method: 'POST' })
        }
        apply(await readOperation(knownId))
      } catch (e) { update({ phase: 'unknown', error: `Delivery not confirmed: ${e.message}. Retry retains the recorded operation.` }) }
      return
    }
    if (!decision) return check()
    try { rememberIntent(key, decision) } catch { update({ error: 'Cannot retain a safe retry key. Check browser storage before submitting.' }); return }
    update({ phase: 'submitting', decision, error: '' }) // synchronous shared lock prevents a double click/two tiles
    const timing = !retry ? decisionTiming(r.owner) : null
    let posted = false
    try {
      if (retry) {
        const found = await findOperation(decision)
        if (!currentRoute(route)) return
        if (found?.operation && operationState(found.operation) !== 'failed') { apply(await readOperation(operationId(found.operation))); return }
      }
      const result = await call(`${BASE}/requests/${encodeURIComponent(r.requestId)}/decisions`, { ...route, method: 'POST', body: decision })
      posted = true
      if (!operationId(result?.operation)) throw Error('Missing operation receipt; outcome unknown')
      // End on the authoritative recording receipt, before a separate delivery read (not paint).
      timing?.complete()
      if (!currentRoute(route)) return
      update({ operation: result.operation, phase: 'checking' })
      // Recording is authoritative even if the subsequent delivery read is slow or lost.
      // This does not promote the operation to delivered or permit a second decision.
      const recorded = result.request
      if (validRequest(recorded) && requestKey(recorded) === requestKey(r) && recorded.owner.storedSessionId === route.session
        && recorded.revision >= Math.max(r.revision, latest.current.revision)
        && recorded.resolution?.operationId === operationId(result.operation)) {
        update({ request: recorded }); saveReadback(route, recorded)
      }
      apply(await readOperation(operationId(result.operation))) // POST success alone is never confirmation
      if (live.current && currentRoute(route)) setEditing(null)
    } catch (e) {
      if (!currentRoute(route)) return
      const status = errorStatus(e)
      if ([409,422,400,403].includes(status)) {
        try {
          const fresh = await call(`${BASE}/requests/${encodeURIComponent(r.requestId)}${query}`, route)
          if (validRequest(fresh?.request) && requestKey(fresh.request) === requestKey(r) && fresh.request.owner.storedSessionId === route.session && fresh.request.revision >= r.revision) {
            if (fresh.operation) { apply(fresh); return }
            try { window.localStorage.removeItem(INTENT_PREFIX + key) } catch {}
            update({ request: fresh.request, decision: null, operation: null, phase: 'stale', error: 'Request changed or was rejected. Review the current scope before choosing again.' }); saveReadback(route, fresh.request); return
          }
        } catch { /* retain locked unknown outcome if readback fails */ }
      }
      // A lost POST must be queried before any resend, including after reconnect.
      try {
        const found = await findOperation(decision)
        if (found?.operation) { apply(await readOperation(operationId(found.operation))); return }
      } catch { /* explicit unknown below */ }
      update({ phase: 'unknown', error: `Outcome unknown: ${e.message}. Check status or retry safely.` })
    } finally {
      timing?.cancel() // identity fenced: a failed/unknown request cannot cancel a newer click
      // The decision was recorded (the POST answered), whatever the delivery read says: the card may have changed.
      if (posted) refreshBlockedAfterAction()
    }
  }
  const failedId = state.phase === 'failed' ? operationId(state.operation) : null
  useEffect(() => {
    if (!failedId || unavailable) return
    const t = setInterval(() => { if (live.current && currentRoute(route) && operations.get(key)?.phase === 'failed') void send(null, true) }, autoRetry.ms)
    return () => clearInterval(t)
  }, [failedId, unavailable, key])
  // Withdraw through the same owner route as decisions. It records no decision and delivers nothing.
  const withdraw = async () => {
    if (!canWithdraw || unavailable || withdrawing.has(key) || operations.get(key)?.decision || ['submitting','checking'].includes(operations.get(key)?.phase) || !currentRoute(route)) return
    withdrawing.add(key) // synchronous lock: a double click (or a second tile) sends one request
    const node = article.current, hadFocus = !!node?.contains(document.activeElement)
    const outcome = { busy: false, note: '', error: '' }
    setDismissal({ ...outcome, busy: true })
    try {
      const result = await call(`${BASE}/requests/${encodeURIComponent(r.requestId)}/withdraw`, { ...route, method: 'POST', body: { session: route.session, expectedRevision: r.revision, reason: WITHDRAW_REASON } })
      if (!currentRoute(route)) return
      // The tile is leaving: keep keyboard focus in the queue (or hand it back to the composer).
      const active = document.activeElement
      if (hadFocus && (!active || active === document.body || node.contains(active))) {
        const next = [node.nextElementSibling, node.previousElementSibling].find(x => x?.matches?.('[data-action-request]'))
        if (next) next.focus(); else host.composer?.focus?.(route.session)
      }
      const done = result?.request
      if (validRequest(done) && done.state === 'withdrawn' && requestKey(done) === requestKey(r) && done.owner.storedSessionId === route.session) saveReadback(route, done)
      await refreshRequests(call)
      refreshBlockedAfterAction()
    } catch (e) {
      if (!currentRoute(route)) return
      if (errorStatus(e) === 409) {
        await refreshRequests(call)
        outcome.note = 'This ask changed or was already decided; refreshed.'
      } else outcome.error = `Couldn\u2019t remove this ask: ${e?.message || e}. It is still in your queue.`
    } finally {
      withdrawing.delete(key)
      if (live.current) setDismissal(outcome)
    }
  }
  const choices = Array.isArray(r.choices) ? r.choices : []
  const selected = choices.find(c => c.id === (r.resolution?.choiceId || state.operation?.decision?.choiceId || state.decision?.choiceId))
  // Pre-v2 migrations stored one placeholder choice; it is agent work, never a user-facing control.
  const legacyPlaceholder = c => c.id === 'request-context' && c.effect === 'revise' && c.label === 'Request complete action'
  const question = ['choice','input'].includes(r.kind)
  const approvalPending = !question && (!r.scope?.complete || !r.scope?.markdown?.trim() || r.state !== 'open' || (r.kind === 'approval' && !choices.some(c => c.effect === 'approve')))
  // A plan-less ask shows no greyed-out Approve and no "request changes": the user can only ask for the plan or hide it.
  const shown = choices.filter(c => !legacyPlaceholder(c) && (!approvalPending || !['revise','approve'].includes(c.effect)))
  const approvalReason = approvalPending ? (r.kind === 'approval'
    ? 'The agent asked you to approve this without a plan. Nothing can be approved until it posts one; no permission is granted.'
    : 'The agent asked you to do this without a complete step. Nothing is needed until it posts one; no permission is granted.')
    : question && !shown.length ? 'Answer in chat — this question closes once your reply is recorded there. No permission is granted.' : ''
  const phaseText = { idle: '', submitting: 'Recording decision…', checking: 'Checking authoritative delivery status…', pending: 'Decision recorded; delivery pending. Work is not confirmed resumed.', failed: sessionBusy(state.operation) ? 'Decision recorded; waiting for the session to finish its current turn.' : 'Decision recorded; delivery unconfirmed. Work may have started.', unknown: 'Delivery outcome unknown.', cancelled: 'Decision not started — no permission was delivered. Ask the agent for a replacement revision, then decide again.', confirmed: 'Confirmed — decision delivered.', stale: 'Scope updated — review required.' }[state.phase]
  const delivering = !!r.resolution && !['confirmed','cancelled'].includes(state.phase)
  // What picking an option does, in one line: the producer's own explain, else what the stored scope already says.
  // The generic "grants no permission" outcome is not repeated per option.
  const explainOf = c => typeof c.explain === 'string' && c.explain.trim() ? c.explain.trim()
    : c.authorizes === true ? 'Approves running exactly the plan above.'
    : r.kind === 'approval' && ['approve','decline'].includes(c.effect) ? str(r.outcomes?.[c.effect]).trim() : ''
  const recommended = shown.find(c => c.recommended === true)
  const ordered = recommended ? [recommended, ...shown.filter(c => c !== recommended)] : shown
  const own = ordered.filter(needsText)
  const choiceDisabled = c => unavailable || dismissal.busy || (['approve','answer'].includes(c.effect) && (!r.scope?.complete || !r.scope?.markdown?.trim() || r.state !== 'open'))
  // Nothing for this plugin to draw under the panel: the panel already shows the ask and posts its plain options.
  if (remoteRetiring(r, state.operation)) return jsx('div', { 'data-remote-retiring': '', role: 'status', children: r.delivery?.text || 'Delivering…' })
  const cover = locked ? { covered: true } : askCoverage(coverage, r)
  const quiet = state.phase === 'idle' && !editing && !state.error && !dismissal.note && !dismissal.error
  // A collapsed panel still counts this ask in its header; the user minimized it, so nothing is drawn here.
  if (!locked && quiet && cover.counted) return null
  if (!locked && quiet && cover.covered && !own.length && !approvalPending && !canWithdraw) return null
  return jsxs('article', { ref: article, 'data-action-request': requestKey(r), 'data-decision-audience': agentPending(r) ? 'agent' : 'human', tabIndex: -1, 'aria-label': r.title, style: { ...rowStyle, minWidth: 0 }, children: [
    jsx('h3', { style: { margin: '2px 0 4px', fontSize: 14, fontWeight: 600, lineHeight: 1.4, overflowWrap: 'anywhere', minWidth: 0 }, children: r.title }),
    // Never an apparently actionable title-only row: say why this ask's options are not on screen.
    !locked && !cover.covered && !cover.counted && jsx('div', { 'data-ask-coverage': cover.reason, role: 'status', style: { color: 'var(--muted-foreground)', fontSize: 12, padding: '2px 0 4px', overflowWrap: 'anywhere' }, children: COVERAGE_TEXT[cover.reason] || COVERAGE_TEXT.unmounted }),
    !locked && approvalReason && jsx('div', { 'data-approval-blocked': '', 'data-agent-completion-pending': '', style: { color: AMBER, padding: '8px 0', fontSize: 12 }, children: approvalReason }),
    jsx('div', { role: 'status', 'aria-live': 'polite', style: delivering ? { color: 'var(--muted-foreground)' } : undefined, children: delivering ? `Delivering to chat… ${phaseText}` : phaseText }),
    state.phase === 'confirmed' && jsx('div', { 'data-owner-phase': ownerPhase(state.consumer), children: ownerPhaseText(state.consumer) }),
    selected && locked && jsx('div', { children: `${state.operation || r.resolution ? 'Recorded choice' : 'Submitted choice'}: ${selected.label}. ${state.phase === 'cancelled' ? 'Cancelled before delivery.' : explainOf(selected)}` }),
    state.error && jsx('div', { role: delivering ? 'status' : 'alert', style: delivering ? { color: 'var(--muted-foreground)' } : undefined, children: state.error }),
    !locked && jsxs('div', { style: flowStyle, children: [
      ...own.map(c => jsx('button', { type: 'button', 'data-decision-effect': c.effect, title: explainOf(c) || c.label, style: { ...actionStyle, fontSize: 12.5, borderColor: 'transparent', background: 'transparent', color: 'var(--muted-foreground)' }, disabled: choiceDisabled(c), onClick: e => { trigger.current = e.currentTarget; setEditing(c) }, children: choiceLabel(c, choices, r.kind) }, c.id)),
      ...(approvalPending ? [
        jsx('button', { type: 'button', 'data-ask-plan': '', style: actionStyle, onClick: () => askAgentForPlan(r, route), children: r.kind === 'approval' ? 'Ask agent for the plan' : 'Ask agent for the full step' }, 'ask-plan'),
        // An owner without the withdraw route (older backend) can only hide it on this device.
        !canWithdraw && jsx('button', { type: 'button', 'data-dismiss-request': '', style: actionStyle, title: 'Hide this until the agent posts a new revision', onClick: () => dismissRequest(r), children: 'Dismiss' }, 'dismiss')
      ] : []),
      canWithdraw && jsx('button', { type: 'button', 'data-withdraw-request': '', style: { ...actionStyle, marginLeft: 'auto', borderColor: 'transparent', background: 'transparent', color: 'var(--muted-foreground)', fontSize: 12 }, title: WITHDRAW_TITLE, disabled: unavailable || dismissal.busy, onClick: () => void withdraw(), children: dismissal.busy ? 'Removing\u2026' : 'Not needed' })
    ] }),
    dismissal.note && jsx('div', { role: 'status', 'data-withdraw-note': '', style: { fontSize: 12 }, children: dismissal.note }),
    dismissal.error && jsx('div', { role: 'alert', 'data-withdraw-error': '', children: dismissal.error }),
    editing && jsxs('div', { onKeyDown: e => { e.stopPropagation(); if (e.key === 'Escape') { e.preventDefault(); close() } else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void send(editing) } }, children: [
      jsx('label', { children: jsxs('span', { children: ['Revision or answer', jsx('textarea', { ref: input, 'aria-label': 'Revision or answer', value: state.text || '', disabled: locked, onChange: e => update({ text: e.target.value }), rows: 3, style: { width: '100%', boxSizing: 'border-box', font: 'inherit', color: 'var(--foreground)', background: 'var(--card)', ...planStyle } })] }) }),
      jsxs('div', { style: flowStyle, children: [jsx('button', { type: 'button', style: actionStyle, disabled: locked || dismissal.busy || !state.text?.trim(), onClick: () => send(editing), children: editing.effect === 'answer' ? 'Send answer' : 'Send revision' }), jsx('button', { type: 'button', style: actionStyle, onClick: close, children: 'Cancel' })] })
    ] }),
    ['pending','failed','unknown'].includes(state.phase) && jsxs('div', { style: flowStyle, children: [jsx('button', { type: 'button', style: actionStyle, onClick: check, children: 'Check status' }), jsx('button', { type: 'button', style: actionStyle, onClick: () => send(null, true), children: 'Retry' })] })
  ] })
}

// A plan-less ask is agent work; the user may hide it. Keyed by revision, so a revised ask (the plan) shows again.
const DISMISSED_REQ_KEY = 'hermes.userSteps.dismissedRequests.v1'
function readDismissedRequests() {
  try { const v = JSON.parse(window.localStorage.getItem(DISMISSED_REQ_KEY) || '[]'); return Array.isArray(v) ? v.filter(k => typeof k === 'string') : [] } catch { return [] }
}
const dismissedRequestKey = r => `${requestKey(r)}#${r.revision}`
export const isRequestDismissed = r => readDismissedRequests().includes(dismissedRequestKey(r))
export function dismissRequest(r) {
  try { window.localStorage.setItem(DISMISSED_REQ_KEY, JSON.stringify([...new Set([...readDismissedRequests(), dismissedRequestKey(r)])].slice(-200))) } catch {}
  canonical = { ...canonical }
  canonicalSubs.forEach(fn => fn(canonical))
}
function askAgentForPlan(r, route) {
  const session = route?.session || focusedId()
  const what = r.kind === 'approval' ? 'the full plan' : 'the complete step'
  void host.composer?.setDraft?.(session, `Please post ${what} for "${r.title}" (what exactly you will do, and its limits) so I can decide.\n`)
  host.composer?.focus?.(session)
}
/** A recorded decision whose delivery is failed/pending/unconfirmed stays on screen (with Retry) until confirmed. */
function remoteRetiring(r, op) {
  // A newer exact operation readback supersedes cached presentation metadata.
  if (op && ['delivered','cancelled'].includes(operationState(op))) return operationState(op) === 'cancelled' && op.retired?.evidence === 'remote-retiring'
  return !!r.resolution && (r.delivery?.state === 'delivering' || (operationState(op) === 'cancelled' && op?.retired?.evidence === 'remote-retiring'))
}
function requestNeedsReadback(scope, r) {
  if (r.state === 'withdrawn' && !r.resolution) return false // nothing can be pending on a withdrawn revision
  const key = `${scope}:${requestKey(r)}`, op = operations.get(key)
  if (remoteRetiring(r, op?.operation)) return true // proof refusal is not terminal delivery evidence
  if (['confirmed','cancelled'].includes(op?.phase) && op.request?.revision === r.revision && operationId(op.operation) === r.resolution?.operationId) return false
  // Terminal backend state is durable delivery confirmation: an old answered ask is neither pending nor shown.
  // Local in-flight decisions and recovery intents still get their readback.
  if (['resolved','superseded','cancelled'].includes(r.state) && r.resolution?.revision === r.revision && !op?.decision && !recoverIntent(key, r)) return false
  return !!(op?.decision || r.resolution?.operationId || recoverIntent(key, r))
}
/** Open asks for the focused chat that need the user (agent-pending and dismissed ones excluded). */
export function openAskCount() { return openAskKeys().length }
export function openAskKeys() {
  const scope = routeKey(decisionRoute())
  if (!decisionRoute().session || canonical.scope !== scope) return []
  return canonical.requests.filter(r => {
    // Delivery is agent work once the choice is recorded; readback membership is separate.
    return !r.resolution && ['open','needs_context'].includes(r.state) && !agentPending(r) && !isRequestDismissed(r)
  }).map(r => `${scope}:${requestKey(r)}:${r.revision}`).sort()
}

/** Queue membership before search/minimize; shared with card-status dedup. */
function representedInQueue(scope, r) {
  const pending = requestNeedsReadback(scope, r)
  return ((!r.resolution && ['open','needs_context'].includes(r.state)) || pending) && (pending || !isRequestDismissed(r))
}
export function presentationForSource(source) {
  const scope = routeKey(decisionRoute())
  if (!decisionRoute().session || canonical.scope !== scope) return null
  const rs = requestsForSource(canonical.requests, source), r = rs[0]
  return r ? { state: r.state, dismissed: rs.every(isRequestDismissed), representedInQueue: rs.some(x => representedInQueue(scope, x)), count: rs.length } : null
}
export function hasQueuePresentation() {
  const scope = routeKey(decisionRoute())
  return !!decisionRoute().session && canonical.scope === scope && canonical.requests.some(r => representedInQueue(scope, r))
}

/** Desktop loads disk plugins asynchronously, so the panel gets `panelWait.ms` after this plugin registers before its
 * absence counts as a failure. Then: an explicit error, not a substitute view. */
export const panelWait = { ms: 10000, since: 0 }
export const PANEL_MISSING_TEXT = 'Session panel did not load, so this chat\u2019s status and decisions can\u2019t be shown. ' +
  'Check Settings \u203a Plugins for session-panel, or desktop.log for \u201cruntime load failed (session-panel)\u201d.'
export const PANEL_UNMOUNTED_TEXT = 'Session panel is loaded but isn\u2019t showing this chat, so its decisions can\u2019t be shown here.'
/** The panel hosts the decision companion inside its own bounded frame. This plugin's composer contribution is only
 *  the explicit error for a panel that never loaded (or never mounted for this chat), never a second queue. */
const panelHostsQueue = () => panelActive() && typeof window.__sessionPanel.hostsQueue === 'function' && window.__sessionPanel.hostsQueue(decisionRoute())
// Mixed-version rollout only: a session panel older than the hosting frame has no hostsQueue and never draws the
// companion, so the queue keeps its own composer slot there and behaves as it did beside that panel.
const LEGACY_COVERAGE = { state: 'legacy' }
const legacyPanel = () => panelActive() && typeof window.__sessionPanel.hostsQueue !== 'function'
export function PanelMissing() {
  const [, tick] = useState(0)
  useEffect(() => {
    const t = setInterval(() => tick(v => v + 1), 1000)
    // React at once when a panel starts or stops hosting this chat's queue, not on the next second.
    const fn = () => tick(v => v + 1)
    window.addEventListener?.('hermes:session-panel-hosting', fn)
    fn() // re-read after subscribing: a panel mounted in this same commit announced itself before we listened
    return () => { clearInterval(t); window.removeEventListener?.('hermes:session-panel-hosting', fn) }
  }, [])
  if (!focusedId() || panelHostsQueue() || legacyPanel() || Date.now() - panelWait.since < panelWait.ms) return null
  const n = openAskCount()
  return jsx('div', { role: 'alert', 'data-user-steps-panel-missing': panelActive() ? 'unmounted' : '', style: { width: '100%', boxSizing: 'border-box', padding: 10, border: '1px solid var(--destructive, #ef4444)', borderRadius: 8, color: 'var(--foreground)', fontSize: 13 },
    children: (panelActive() ? PANEL_UNMOUNTED_TEXT : PANEL_MISSING_TEXT) + (n ? ` ${n} decision${n === 1 ? ' is' : 's are'} waiting.` : '') })
}

export const queuePoll = { ms: POLL_MS }
/** The requests the queue draws a row for. The panel carries ordinary pending delivery; genuine failures/unknown
 *  outcomes keep recovery here. */
function queueRequests(snap, scope, audience = 'all') {
  const panelCarries = r => {
    if (!r.resolution || typeof window === 'undefined' || !window.__sessionPanel?.showsDecision?.(r.requestId)) return false
    const op = operations.get(`${scope}:${requestKey(r)}`)
    return !(op?.phase === 'unknown' || (op?.phase === 'failed' && !sessionBusy(op)) || op?.error && !sessionBusy(op))
  }
  return snap.requests.filter(r => representedInQueue(scope, r) && !panelCarries(r) && (audience === 'all' || (audience === 'agent') === agentPending(r)))
}
/** Whether ActionQueue currently renders anything for the focused chat (the same test it applies), so the hosting
 *  panel paints no empty frame around a queue that draws nothing. */
export function queueVisible() {
  const route = decisionRoute(), scope = routeKey(route)
  if (!route.session || canonical.scope !== scope) return false
  return queueRequests(canonical, scope).length > 0 || !!canonical.error
}
// The session panel owns the frame, headings, options and minimize control.
export function ActionQueue({ audience = 'all', coverage = null } = {}) {
  const [snap, setSnap] = useState(canonical)
  useValue(host.state.focusedStoredSessionId)
  useValue(host.state.focusedSessionOwner || host.state.connectionId || host.state.focusedStoredSessionId)
  useValue(host.state.profile || host.state.focusedStoredSessionId)
  useValue(host.state.connectionId || host.state.focusedStoredSessionId)
  const route = decisionRoute(), scope = routeKey(route)
  useEffect(() => { canonicalSubs.add(setSnap); return () => canonicalSubs.delete(setSnap) }, [])
  useEffect(() => { void refreshRequests() }, [scope])
  useEffect(() => {
    const t = setInterval(() => { if (typeof document === 'undefined' || !document.hidden) void refreshRequests() }, queuePoll.ms)
    return () => clearInterval(t)
  }, [])
  if (!route.session || snap.scope !== scope) return null
  const requests = queueRequests(snap, scope, audience)
  if (!requests.length && (!snap.error || audience === 'agent')) return null
  return jsxs('section', { 'data-action-queue': '', 'data-embedded': '', 'aria-label': 'Session decisions', onKeyDown: e => { if (['Enter','Escape','ArrowDown','ArrowUp'].includes(e.key)) e.stopPropagation() }, style: { ...SELECTABLE, width: '100%', minWidth: 0, color: 'var(--foreground)' }, children: [
    jsx('style', { children: '[data-action-queue] button:disabled{opacity:.5;cursor:not-allowed}[data-action-queue] button:not(:disabled):hover{filter:brightness(1.15)}[data-action-queue] :is(button,textarea,input,[tabindex]):focus-visible{outline:2px solid var(--ring,var(--primary));outline-offset:2px}' }),
    snap.error && jsxs('div', { role: 'alert', children: [snap.error, jsx('button', { style: actionStyle, onClick: () => refreshRequests(), children: 'Reload actions' })] }),
    // No scroll container of its own: the hosting panel body is the one bounded scroller (header stays reachable).
    jsx('div', { 'data-action-queue-body': '', style: { minWidth: 0 }, onKeyDown: e => { if (e.target.tagName !== 'ARTICLE') return; const rows = [...e.currentTarget.querySelectorAll('article')], i = rows.indexOf(e.target); if (['ArrowDown','ArrowUp','Enter'].includes(e.key)) { e.stopPropagation(); e.preventDefault(); if (e.key === 'Enter') e.target.querySelector('button:not(:disabled)')?.focus(); else rows[(i + (e.key === 'ArrowDown' ? 1 : rows.length - 1)) % rows.length]?.focus() } }, children: requests.map(r => jsx(DecisionRow, { request: r, route, coverage, unavailable: !!snap.error || !!snap.stale }, `${scope}:${requestKey(r)}`)) })
  ] })
}

/** An authorizing numbered option is answerable only when the list froze the COMPLETE request beside it. Older lists
 * (no `display`) fail closed here; the server refuses them too. Presentation only: the server stays authoritative. */
export const listDisplayComplete = e => {
  const d = e?.display
  return !!d && typeof d === 'object' && d.scopeComplete === true && typeof d.scopeMarkdown === 'string' && !!d.scopeMarkdown.trim()
    && Array.isArray(d.limits) && Array.isArray(d.exclusions) && !!d.outcomes && typeof d.outcomes === 'object'
}

/** `::decision-list{id="<sourceId>" d="<digest>"}`: the numbered list frozen when the agent showed it. Buttons post the
 * DISPLAYED snapshot digest and number to the authenticated mounted route; a typed bare number is never trusted. */
export function DecisionList({ attrs, deps }) {
  useValue(host.state.focusedSessionOwner || host.state.connectionId || host.state.focusedStoredSessionId)
  const call = deps?.call || api
  const route = decisionRoute(), scope = routeKey(route)
  const sourceId = decodeAttr(attrs?.id), marked = str(attrs?.d)
  const [state, setState] = useState({ scope, list: null, failed: false, sent: null, error: '', busy: false })
  const keys = useRef(new Map()) // one idempotency key per (digest, n): a retry replays the same operation
  const busy = useRef(false)
  useEffect(() => {
    let live = true
    setState({ scope, list: null, failed: false, sent: null, error: '', busy: false })
    if (!sourceId || !marked || !route.session) { setState(st => ({ ...st, failed: true })); return }
    call(`${BASE}/lists/${encodeURIComponent(sourceId)}?session=${encodeURIComponent(route.session)}`, route)
      .then(r => { if (live && currentRoute(route)) setState(st => ({ ...st, list: r?.list?.digest === marked && Array.isArray(r.list.entries) ? r.list : null, failed: r?.list?.digest !== marked })) })
      .catch(() => { if (live && currentRoute(route)) setState(st => ({ ...st, failed: true })) })
    return () => { live = false }
  }, [sourceId, marked, scope])
  const list = state.scope === scope ? state.list : null
  if (!list) return state.failed ? jsx('div', { 'data-decision-list-unavailable': '', style: { fontSize: 11, opacity: 0.7 }, children: 'Numbered options unavailable \u2014 answer from the decision queue above the composer.' }) : null
  const answer = async n => {
    if (busy.current) return
    busy.current = true
    setState(st => ({ ...st, busy: true, error: '' }))
    const kk = `${list.digest}:${n}`
    if (!keys.current.has(kk)) keys.current.set(kk, globalThis.crypto.randomUUID())
    try {
      const result = await call(`${BASE}/lists/${encodeURIComponent(sourceId)}/answers`, { ...route, method: 'POST', body: { session: route.session, snapshotDigest: list.digest, n, idempotencyKey: keys.current.get(kk) } })
      if (!currentRoute(route)) return
      setState(st => ({ ...st, sent: { n, delivery: str(result?.operation?.dispatchState) || 'recorded' }, busy: false }))
      refreshBlockedAfterAction()
    } catch (e) {
      if (currentRoute(route)) setState(st => ({ ...st, busy: false, error: `Outcome unknown: ${e.message}. The answer may or may not have been recorded. Retry (the same request key is reused, so it cannot be applied twice) or check the decision queue.` }))
    } finally { busy.current = false }
  }
  return jsxs('div', {
    'data-decision-list': list.digest,
    style: { ...SELECTABLE, margin: '6px 0', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)', display: 'flex', flexDirection: 'column', gap: 6 },
    children: [
      ...list.entries.map(e => {
        const d = e.display, authorizing = ['approve', 'answer'].includes(e.effect)
        // Everything shown is the FROZEN snapshot; the live request (possibly revised since) is never consulted.
        const complete = listDisplayComplete(e)
        const blocked = authorizing && !complete
        const outcome = d && typeof d.outcomes === 'object' && d.outcomes ? str(d.outcomes[e.effect]).trim() : ''
        return jsxs('div', { 'data-decision-list-entry': e.n, style: { display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }, children: [
          e.title && jsx('strong', { 'data-decision-list-title': '', style: { overflowWrap: 'anywhere' }, children: str(e.title) }),
          d && typeof d.scopeMarkdown === 'string' && d.scopeMarkdown.trim() && jsx('div', { 'data-decision-list-scope': '', tabIndex: 0, 'aria-label': authorizing ? 'Complete plan' : 'Question', style: { ...planStyle, whiteSpace: 'normal', lineHeight: 1.5, maxHeight: '30vh', overflowY: 'auto' }, children: jsx(MessageTextContent, { media: false, text: d.scopeMarkdown }) }),
          d && Array.isArray(d.limits) && d.limits.length > 0 && jsxs('div', { 'data-decision-list-limits': '', children: [jsx('strong', { children: 'Limits' }), jsx('ul', { style: { margin: '4px 0', paddingLeft: 20, ...planStyle }, children: d.limits.map((l, i) => jsx('li', { children: `${str(l.key)}: ${str(l.value)}${permissionNote(l.permissionRequired, e.effect === 'approve')}` }, i)) })] }),
          d && Array.isArray(d.exclusions) && d.exclusions.length > 0 && jsxs('div', { 'data-decision-list-exclusions': '', children: [jsx('strong', { children: 'Not included' }), jsx('ul', { style: { margin: '4px 0', paddingLeft: 20, ...planStyle }, children: d.exclusions.map((t, i) => jsx('li', { children: str(t) }, i)) })] }),
          outcome && jsx('div', { 'data-decision-list-outcome': '', style: planStyle, children: `If you choose this: ${outcome}` }),
          blocked && jsx('div', { 'data-decision-list-blocked': '', role: 'note', style: { color: AMBER, fontSize: 12 }, children: 'This list does not show the complete request, so this option cannot be answered here. Use the decision queue above the composer.' }),
          jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }, children: [
            jsx('button', { type: 'button', style: actionStyle, disabled: state.busy || !!state.sent || e.open === false || blocked, 'aria-label': `Choose option ${e.n}${e.title ? ` for ${str(e.title)}` : ''}: ${str(e.label)}`, onClick: () => void answer(e.n), children: String(e.n) }),
            jsx('span', { children: str(e.label) })
          ] })
        ] }, e.n)
      }),
      state.sent && jsx('small', { role: 'status', children: `Answered ${state.sent.n} \u2014 ${state.sent.delivery}` }),
      state.error && jsx('div', { role: 'alert', style: { fontSize: 11 }, children: state.error })
    ]
  })
}

export default {
  id: ID,
  name: 'User Steps',
  register(ctx) {
    osApi = ctx?.os || null
    previewApi = typeof ctx?.openPreview === 'function' ? (t => ctx.openPreview(t)) : null
    // The session panel draws every ask (options, card tiles, Open card) and hosts this plugin's compact companion
    // queue (free text, pending-delivery Check/Retry, plan request, withdraw) inside its own bounded frame, so the
    // composer gets one session-status block. This slot shows only the explicit error for a missing panel, never the
    // old standalone queue + strip: a silent fallback hid a broken panel load behind an outdated view.
    panelWait.since = Date.now()
    ctx.register({ id: 'strip', area: COMPOSER_AREAS.top, order: -25, render: () => legacyPanel() ? jsx(ActionQueue, { coverage: LEGACY_COVERAGE }) : jsx(PanelMissing, {}) })
    ctx.register({ id: 'user-step-directive', area: TRANSCRIPT_DIRECTIVE_AREA, data: { name: 'user-step', render: props => jsx(StepBox, props) } })
    ctx.register({ id: 'decision-list-directive', area: TRANSCRIPT_DIRECTIVE_AREA, data: { name: 'decision-list', render: props => jsx(DecisionList, props) } })
    const onVis = () => { if (typeof document !== 'undefined' && document.hidden) return; void refresh(); void refreshRequests() }
    if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVis)
    if (typeof window !== 'undefined') window.__userSteps = { version: VERSION, ownsComposer: true, get snapshot() { return snapshot }, refresh, refreshRequests, warm, subscribe: fn => { canonicalSubs.add(fn); return () => canonicalSubs.delete(fn) }, requestForSource: source => canonical.scope === routeKey(decisionRoute()) ? requestForSource(canonical.requests, source) : null, requestsForSource: source => canonical.scope === routeKey(decisionRoute()) ? requestsForSource(canonical.requests, source) : [], presentationForSource, hasQueuePresentation, queueVisible, ActionQueue, openAskCount, openAskKeys }
    signalDecisionChanges()
    return () => {
      if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVis)
      if (typeof window !== 'undefined' && window.__userSteps?.version === VERSION) delete window.__userSteps
      signalDecisionChanges()
      unsubKanban?.()
      unsubKanban = null
    }
  }
}

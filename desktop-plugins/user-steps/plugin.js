/**
 * Session decisions: one canonical v2 queue above the composer. Transcript directives
 * are passive receipts; legacy steps/cards are read-only references with a draft-only
 * request for complete context. Authorization uses durable owner-routed REST operations,
 * never composer.submit plus resolve or generic card unblocking. Existing links,
 * preview navigation and separately collapsed agent issues remain available.
 */
import { COMPOSER_AREAS, TRANSCRIPT_DIRECTIVE_AREA, host, useValue, MessageTextContent } from '@hermes/plugin-sdk'
import { useEffect, useState, useRef } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'user-steps'
const VERSION = 10
// The composer chrome is user-select:none app-wide; the strip and the in-message box must stay copyable.
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

/** The buttons a step offers. kind 'submit' sends `text` as a visible user turn then resolves with `resolution`;
 *  kind 'draft' prefills the composer and leaves the step open (the reply resolves it). */
export function actionsFor(step) {
  const t = short(step?.text)
  switch (step?.kind) {
    case 'approve':
      return [
        { label: 'Do it', primary: true, kind: 'submit', text: `Go: ${t}`, resolution: 'Go' },
        { label: 'No', kind: 'submit', text: `No: ${t}`, resolution: 'No' }
      ]
    case 'do':
      return [
        { label: 'Done', primary: true, kind: 'submit', text: `\u2713 Done: ${t}`, resolution: 'Done' },
        { label: 'Can\u2019t', kind: 'submit', text: `\u2717 Can\u2019t: ${t}`, resolution: 'Can\u2019t' }
      ]
    case 'choose': {
      const opts = (Array.isArray(step.options) ? step.options : []).map(str).filter(Boolean)
      if (opts.length >= 2)
        return opts.map((o, i) => ({ label: o, primary: i === 0, kind: 'submit', text: `Chose: ${o} \u2014 ${t}`, resolution: `Chose: ${o}` }))
      return [{ label: 'Reply\u2026', primary: true, kind: 'draft', text: `Re: ${t} \u2014 ` }]
    }
    default:
      return [{ label: 'Reply\u2026', primary: true, kind: 'draft', text: `Re: ${t} \u2014 ` }]
  }
}

/** Run one action. Returns 'sent' | 'drafted' | 'no-composer'. Resolve only after the turn was handed to a
 *  visible composer, so a failed click never closes a step silently. Deps are injectable for tests. */
export async function performAction(step, action, sessionId, deps = {}) {
  const composer = deps.composer || host.composer
  const call = deps.call || api
  if (action.kind === 'draft') {
    const ok = await composer?.setDraft?.(sessionId, action.text)
    composer?.focus?.(sessionId)
    return ok === false ? 'no-composer' : 'drafted'
  }
  throw new Error('Legacy actions cannot authorize or resolve work. Request a complete canonical action.')
}

/** Defense against old/malformed blocked-panel snapshots. Presentation, not authorization. */
export function actionableCard(item) {
  if (!item || item.loaded === false || ['transient', 'dependency', 'time'].includes(item.kind)) return false
  const r = item.human_request
  return !!r && typeof r === 'object' && !Array.isArray(r) &&
    ['approval', 'reply', 'user_terminal', 'physical', 'external'].includes(r.interaction) &&
    ['action', 'why_human', 'scope', 'evidence'].every(k => typeof r[k] === 'string' && r[k].trim())
}

/** Ledger steps then validated Kanban requests, oldest first; omit the rolling card. */
export function projectStrip(ledger, kanbanItems) {
  // the chat's own rolling cards (current + already archived: blocked-panel's snapshot can lag the archive)
  const own = new Set([str(ledger?.card?.id), ...(Array.isArray(ledger?.own_cards) ? ledger.own_cards.map(str) : [])].filter(Boolean))
  const steps = (Array.isArray(ledger?.steps) ? ledger.steps : [])
    .filter(s => s?.id && (s.status || 'open') === 'open')
    .slice()
    .sort((a, b) => (Number(a.created) || 0) - (Number(b.created) || 0))
    .map(s => ({ type: 'step', key: `s:${s.id}`, step: s, actions: actionsFor(s) }))
  const cards = (Array.isArray(kanbanItems) ? kanbanItems : [])
    .filter(it => it?.id && actionableCard(it) && !(own.has(str(it.id)) &&
      str(it.connectionId || it.gateway) === str(ledger?.connectionId) &&
      (it.board || 'default') === (ledger?.card?.board || 'default')))
    .slice()
    .sort((a, b) => (Number(a.at) || 0) - (Number(b.at) || 0))
    .map(it => ({ type: 'card', key: `c:${it.connectionId || it.gateway ? `${it.connectionId || it.gateway}:` : ''}${it.board || 'default'}:${it.id}`, item: it }))
  return [...steps, ...cards]
}

/** First line of what a blocked card wants (blocked-panel's checklist when available). */
export function cardSummary(item, checklist, askSummary) {
  try {
    // Only validated explicit requests enter this projection, never task descriptions.
    if (typeof askSummary === 'function') return short(askSummary(item, item.rewritten), 160)
    const list = typeof checklist === 'function' ? checklist(item, item.rewritten) : null
    if (list?.steps?.length) return short(list.steps[0], 160)
  } catch {
    /* fall through */
  }
  return short(item?.text || item?.title, 160)
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
let timer = null
let running = false
let rerun = false
let unsubKanban = null
const stepCache = new Map() // id → { at, step }

const emit = () => subs.forEach(fn => fn(snapshot))

// Agent issues need nothing from the user, so the user may dismiss them. The key includes the failure time, so a
// NEW failure on the same card shows again; a dismissed one stays hidden across reloads (localStorage).
const DISMISS_KEY = 'hermes.userSteps.dismissedIssues.v1'
const DISMISS_CAP = 300
let dismissed = null
export const issueKey = i => JSON.stringify([i?.gatewayId || i?.gateway || i?.connectionId || '', i?.profile || '', i?.board || 'default', i?.id, i?.at ?? ''])
function dismissedSet() {
  if (dismissed) return dismissed
  try {
    const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(DISMISS_KEY) : null
    dismissed = new Set(Array.isArray(JSON.parse(raw || '[]')) ? JSON.parse(raw || '[]') : [])
  } catch {
    dismissed = new Set()
  }
  return dismissed
}
export function isDismissed(issue) {
  return dismissedSet().has(issueKey(issue))
}
export function dismissIssues(list) {
  const set = dismissedSet()
  for (const i of list || []) set.add(issueKey(i))
  const keep = [...set].slice(-DISMISS_CAP)
  dismissed = new Set(keep)
  try {
    if (typeof localStorage !== 'undefined') localStorage.setItem(DISMISS_KEY, JSON.stringify(keep))
  } catch {}
  snapshot = { ...snapshot }
  emit()
}

function blockedPanel() {
  return typeof window !== 'undefined' ? window.__blockedPanel : null
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

export async function refresh(call = api) {
  if (running) {
    rerun = true
    return
  }
  running = true
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
      // response before it can populate either the strip or the step cache.
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
    if (!currentRoute(route)) {
      rerun = true
      return
    }
    const bp = blockedPanel()?.snapshot
    snapshot = {
      stored,
      scope: routeKey(route),
      ledger,
      kanban: matchingBlocked(bp, route) ? bp.items || [] : snapshot.scope === routeKey(route) ? snapshot.kanban : [],
      issues: matchingBlocked(bp, route) ? bp.issues || [] : snapshot.scope === routeKey(route) ? snapshot.issues || [] : [],
      ok
    }
    emit()
  } finally {
    running = false
    if (rerun) {
      rerun = false
      void refresh(call)
    }
  }
}

export const getSnapshot = () => snapshot

function subscribe(fn) {
  subs.add(fn)
  if (!timer) {
    const poll = () => { if (typeof document !== 'undefined' && document.hidden) return; void refresh(); void refreshRequests() }
    timer = setInterval(poll, POLL_MS)
    void refresh()
  }
  return () => {
    subs.delete(fn)
    if (!subs.size && timer) {
      clearInterval(timer)
      timer = null
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

export const clearStepCache = () => stepCache.clear()

// ── UI ──────────────────────────────────────────────────────────────────────

const AMBER = 'var(--warning, #d29922)'
const btn = (primary = false) => ({
  minHeight: 44,
  minWidth: 44,
  padding: '2px 10px',
  borderRadius: 6,
  border: `1px solid ${primary ? AMBER : 'color-mix(in srgb, var(--border, #8884) 90%, transparent)'}`,
  background: primary ? `color-mix(in srgb, ${AMBER} 22%, transparent)` : 'transparent',
  color: 'var(--foreground, inherit)',
  fontSize: 11.5,
  lineHeight: '18px',
  cursor: 'pointer',
  fontFamily: 'inherit'
})
const KIND_ICON = { approve: 'codicon-question', do: 'codicon-checklist', choose: 'codicon-list-selection', input: 'codicon-comment' }

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

// Legacy references answer in chat; there is no user button for requesting agent work.
function replyInChat(text) {
  const session = focusedId()
  void host.composer?.setDraft?.(session, `Re: ${text}\n`)
  host.composer?.focus?.(session)
}
function StepButtons({ step }) {
  return jsx('button', { type: 'button', style: actionStyle, onClick: () => replyInChat(step.text), children: 'Reply in chat' })
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
  // Re-read when the shared snapshot changes: a click in the strip (or the model resolving the step) must flip
  // this box too. Listening only; the poll timer belongs to the strip.
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
      ...state.steps.map(s =>
        (s.status || 'open') === 'open'
          ? jsxs('div', {
              style: { display: 'flex', flexDirection: 'column', gap: 4 },
              children: [
                state.steps.length > 1 && jsx('div', { style: { fontSize: 12, opacity: 0.85 }, children: jsx(Linked, { text: s.text }) }),
                jsx('small', { children: receiptStatus({ ...attrs, gatewayId: attrs?.gatewayId || s.requestOwner?.gatewayId, profile: attrs?.profile || s.requestOwner?.profile, requestId: attrs?.requestId || s.requestId }, ids, scope) })
              ]
            }, s.id)
          : jsx('div', { 'data-user-step-resolved': s.id, style: { fontSize: 11.5, opacity: 0.65 }, children: `\u2713 ${s.resolution || 'Resolved'}${state.steps.length > 1 ? ` \u2014 ${s.text}` : ''}` }, s.id)
      )
    ]
  })
}

const rowStyle = { display: 'flex', flexDirection: 'column', gap: 4, padding: '6px 0', borderTop: '1px solid color-mix(in srgb, var(--border, #8884) 60%, transparent)' }

function CardRow({ item }) {
  const bp = blockedPanel()
  return jsxs('div', { 'data-user-steps-card': item.id, style: rowStyle, children: [
    jsx('strong', { children: item.title }),
    jsx('div', { style: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }, children: jsx(Linked, { text: item.text || item.human_request?.action }) }),
    jsx('small', { children: 'Legacy reference — not an approval request.' }),
    jsxs('div', { style: flowStyle, children: [typeof bp?.openCard === 'function' && jsx('button', { type: 'button', style: actionStyle, onClick: () => bp.openCard(item), children: 'Open card' })] })
  ] })
}

/** A card parked by a worker/dispatcher failure: an agent issue, never a task for the user. */
export function IssueRow({ issue }) {
  const bp = blockedPanel()
  return jsxs('div', {
    'data-user-steps-issue': issue.id,
    style: rowStyle,
    children: [
      jsxs('div', {
        style: { display: 'flex', gap: 6, alignItems: 'baseline', minWidth: 0 },
        children: [
          jsx('i', { 'aria-hidden': true, className: 'codicon codicon-warning', style: { opacity: 0.7, flex: 'none' } }),
          jsx('span', { style: { fontWeight: 600, fontSize: 12.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }, title: issue.title, children: issue.title }),
          jsx('span', { style: { marginLeft: 'auto', flex: 'none', fontSize: 10.5, fontFamily: 'var(--font-mono, ui-monospace, monospace)', opacity: 0.6 }, children: issue.id })
        ]
      }),
      jsx('div', { style: { fontSize: 12.5, lineHeight: 1.45 }, children: issue.reason }),
      jsx('div', { 'data-user-steps-owner': true, style: { fontSize: 11.5 }, children: `Owner: ${issue.owner || 'unassigned'}` }),
      jsxs('div', {
        style: { display: 'flex', gap: 6, alignItems: 'center' },
        children: [
          jsx('span', { style: { fontSize: 11.5, opacity: 0.75 }, children: 'No action required from you.' }),
          typeof bp?.openCard === 'function' && jsx('button', { type: 'button', style: { ...btn(), marginLeft: 'auto' }, onClick: () => bp.openCard(issue), children: 'Open card' }),
          jsx('button', { type: 'button', 'data-user-steps-dismiss': issue.id, style: { ...btn(), ...(typeof bp?.openCard === 'function' ? {} : { marginLeft: 'auto' }) }, title: 'Hide this agent issue (a new failure on this card shows again)', onClick: () => dismissIssues([issue]), children: 'Dismiss' })
        ]
      })
    ]
  })
}

function StepRow({ step, sessionId }) {
  return jsxs('div', {
    'data-user-steps-step': step.id,
    style: rowStyle,
    children: [
      jsxs('div', {
        style: { display: 'flex', gap: 6, alignItems: 'baseline' },
        children: [
          jsx('i', { 'aria-hidden': true, className: `codicon ${KIND_ICON[step.kind] || 'codicon-comment'}`, style: { color: AMBER, flex: 'none' } }),
          jsx('span', { style: { fontSize: 12.5, lineHeight: 1.45 }, children: jsx(Linked, { text: step.text }) })
        ]
      }),
      jsx(StepButtons, { step, deps: { sessionId } })
    ]
  })
}

export function Strip() {
  const [snap, setSnap] = useState(snapshot)
  const focused = useValue(host.state.focusedStoredSessionId)
  useValue(host.state.focusedSessionOwner || host.state.connectionId || host.state.focusedStoredSessionId)
  useValue(host.state.profile || host.state.focusedStoredSessionId)
  const route = decisionRoute(), scope = routeKey(route)
  useEffect(() => subscribe(setSnap), [])
  useEffect(() => { void refresh() }, [focused, scope])
  if (!focused || snap.stored !== focused || (snap.scope && snap.scope !== scope)) return null
  const requests = canonical.scope === scope ? canonical.requests : []
  const rows = projectStrip(snap.ledger, snap.kanban).filter(row => !requestForSource(requests, row.type === 'card'
    ? { gatewayId: row.item.gatewayId || row.item.gateway, profile: row.item.profile, type: 'kanban', board: row.item.board, id: row.item.id }
    : { gatewayId: row.step.requestOwner?.gatewayId || row.step.gatewayId || row.step.owner?.gatewayId, profile: row.step.requestOwner?.profile || row.step.profile || row.step.owner?.profile, type: 'user_step', id: row.step.id }))
  const issues = (snap.issues || []).filter(i => !isDismissed(i))
  if (!rows.length && !issues.length) return null
  return jsxs('div', { 'data-slot': 'user-steps', style: { ...SELECTABLE, minWidth: 0, color: 'var(--foreground)' }, children: [
    rows.length > 0 && jsxs('details', { children: [jsx('summary', { style: { minHeight: 44, cursor: 'pointer' }, children: `Legacy references (${rows.length}) — not approvals` }), ...rows.map(r => r.type === 'step' ? jsx(StepRow, { step: r.step }, r.key) : jsx(CardRow, { item: r.item }, r.key))] }),
    issues.length > 0 && jsxs('details', { children: [jsx('summary', { style: { minHeight: 44, cursor: 'pointer' }, children: `Agent issues (${issues.length}) — no action required from you` }), jsx('button', { type: 'button', 'data-user-steps-dismiss-all': true, style: actionStyle, onClick: () => dismissIssues(issues), children: 'Dismiss agent issues' }), ...issues.map(i => jsx(IssueRow, { issue: i }, `i:${i.board}:${i.id}`))] })
  ] })
}

// Canonical v2 decisions. The server, not composer text or card status, records authorization.
export const requestKey = r => r?.owner?.gatewayId && r?.owner?.profile && r?.requestId
  ? JSON.stringify([r.owner.gatewayId, r.owner.profile, r.requestId]) : null
export const sourceKey = r => r?.gatewayId && r?.profile && r?.id && ['message', 'user_step', 'kanban'].includes(r.type) && (r.type !== 'kanban' || r.board)
  ? JSON.stringify([r.gatewayId, r.profile, r.type, r.type === 'kanban' ? r.board : '', r.id]) : null
export function validRequest(r) {
  const text = s => typeof s === 'string' && !!s.trim()
  return !!requestKey(r) && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(r.requestId) &&
    r.schemaVersion === 2 && Number.isInteger(r.revision) && r.revision >= 1 && text(r.owner.storedSessionId) &&
    ['approval','choice','input','external_step'].includes(r.kind) && text(r.title) &&
    ['needs_context','open','resolved','superseded','cancelled'].includes(r.state) &&
    typeof r.scope?.markdown === 'string' && typeof r.scope.complete === 'boolean' &&
    Array.isArray(r.scope.sourceRefs) && r.scope.sourceRefs.every(sourceKey) &&
    Array.isArray(r.aliases) && r.aliases.every(sourceKey) &&
    Array.isArray(r.limits) && r.limits.every(l => text(l?.key) && text(l.value) && (l.permissionRequired === undefined || text(l.permissionRequired))) &&
    Array.isArray(r.exclusions) && r.exclusions.every(text) &&
    (text(r.outcomes?.revise) || (['choice','input','external_step'].includes(r.kind) && text(r.outcomes?.answer))) &&
    Array.isArray(r.choices) && r.choices.length > 0 && new Set(r.choices.map(c => c?.id)).size === r.choices.length &&
    r.choices.every(c => text(c?.id) && text(c.label) && ['approve','revise','decline','answer'].includes(c.effect))
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
export function requestForSource(requests, source) {
  const key = sourceKey(source)
  if (!key) return null
  const matches = projectRequests(requests).filter(r => (r.aliases || []).some(a => sourceKey(a) === key))
  return matches.length === 1 ? matches[0] : null // ambiguous provenance never suppresses another request
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
function recoverIntent(key, request) {
  try {
    const d = JSON.parse(window.localStorage.getItem(INTENT_PREFIX + key) || 'null')
    return d?.session === request.owner.storedSessionId && d.expectedRevision === request.revision && typeof d.idempotencyKey === 'string' && request.choices.some(c => c.id === d.choiceId) ? d : null
  } catch { return null }
}
function rememberIntent(key, decision) {
  if (typeof window !== 'undefined') window.localStorage.setItem(INTENT_PREFIX + key, JSON.stringify(decision))
}
let canonical = { scope: null, requests: [], error: null }
const canonicalSubs = new Set()
const readbacks = new Set()
let requestRead = 0
function receiptStatus(attrs, ids, scope) {
  if (canonical.scope !== scope) return 'Reference only — review and decide above the composer.'
  const linked = canonical.requests.find(r => r.owner.storedSessionId === attrs?.s && r.owner.gatewayId === attrs?.gatewayId && r.owner.profile === attrs?.profile && (r.requestId === attrs?.requestId || (r.aliases || []).some(a => a.type === 'user_step' && ids.includes(a.id) && a.gatewayId === attrs.gatewayId && a.profile === attrs.profile)))
  const state = linked && operations.get(`${scope}:${requestKey(linked)}`)
  if (state?.phase === 'cancelled') return 'Decision not started — no permission was delivered. A replacement revision requires a fresh decision.'
  return state?.phase === 'confirmed' ? 'Confirmed — decision delivered.' : linked?.resolution || state?.decision ? 'Decision recorded; delivery status is above the composer.' : 'Reference only — review and decide above the composer.'
}
function signalDecisionChanges() {
  if (typeof window !== 'undefined' && window.dispatchEvent) window.dispatchEvent(new window.Event('hermes:user-steps-changed'))
}
const publishCanonical = () => { canonicalSubs.forEach(fn => fn(canonical)); signalDecisionChanges(); emit() }
export async function refreshRequests(call = api) {
  const generation = ++requestRead
  const route = decisionRoute(), scope = routeKey(route)
  if (!route.session) return
  try {
    if (host.state?.focusedSessionOwner && !route.connectionId) throw Error('Session owner unavailable')
    const result = await call(`${BASE}/requests?session=${encodeURIComponent(route.session)}`, route)
    if (!currentRoute(route) || generation !== requestRead) return
    if (!Array.isArray(result?.requests)) throw Error('Invalid action queue response')
    const requests = projectRequests(result.requests)
    if (requests.length !== new Set(result.requests.map(requestKey)).size || requests.some(r => r.owner.storedSessionId !== route.session || r.owner.profile !== route.profile)) throw Error('Invalid action request owner or schema')
    canonical = { scope, requests, error: null }
    publishCanonical()
    readbacks.forEach(check => check())
  } catch (e) {
    if (!currentRoute(route) || generation !== requestRead) return
    canonical = { scope, requests: canonical.scope === scope ? canonical.requests : [], error: `Action queue unavailable: ${e.message}. Legacy references are not authorization.` }
    publishCanonical()
  }
}
const operationId = op => op?.operationId || op?.id
const operationState = op => { const s = op?.dispatchState || op?.state || op?.status; return s === 'delivery_failed' ? 'failed' : s }
const errorStatus = e => e?.status || e?.statusCode || e?.response?.status || Number(str(e?.message).match(/\b(400|403|404|409|422)\b/)?.[1])
const actionStyle = { ...btn(), minHeight: 44, minWidth: 44, maxWidth: '100%', whiteSpace: 'normal', overflowWrap: 'anywhere' }
const flowStyle = { display: 'flex', flexWrap: 'wrap', gap: 8 }
const planStyle = { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', minWidth: 0 }
const SUCCESS = 'var(--success, #3fb950)'
const DANGER = 'var(--destructive, #f85149)'
const sectionLabel = { margin: '10px 0 6px', fontSize: 12, fontWeight: 700 }
const choiceStyle = effect => ({ ...actionStyle, fontSize: 12.5, fontWeight: effect === 'approve' ? 700 : 500,
  borderColor: effect === 'approve' ? SUCCESS : effect === 'decline' ? DANGER : 'var(--border)',
  background: effect === 'approve' ? `color-mix(in srgb, ${SUCCESS} 22%, var(--card))` : 'transparent',
  color: effect === 'decline' ? DANGER : 'var(--foreground)' })
const needsText = c => c.effect === 'revise' || (c.effect === 'answer' && c.id === 'answer-text')
// Missing plan/step = agent work. It never counts as something "for you".
export const agentPending = r => !['choice','input'].includes(r.kind) && (!r.scope?.complete || !r.scope?.markdown?.trim() || r.state !== 'open' || (r.kind === 'approval' && !(r.choices || []).some(c => c.effect === 'approve')))
const choiceLabel = (c, choices, kind) => kind !== 'approval' ? c.label : c.effect === 'approve' && choices.filter(x => x.effect === 'approve').length === 1 ? 'Approve' : c.effect === 'revise' && choices.filter(x => x.effect === 'revise').length === 1 ? 'Request changes' : c.label

function saveReadback(route, request) {
  if (!currentRoute(route)) return
  if (canonical.scope === routeKey(route)) {
    canonical = { ...canonical, requests: projectRequests(canonical.requests.some(r => requestKey(r) === requestKey(request)) ? canonical.requests.map(r => requestKey(r) === requestKey(request) ? request : r) : [...canonical.requests, request]) }
    publishCanonical()
  }
}

export function DecisionRow({ request, route, call = api, unavailable = false }) {
  const key = `${routeKey(route)}:${requestKey(request)}`
  const [state, setState] = useState(() => {
    const decision = recoverIntent(key, request)
    return operations.get(key) || { request, phase: decision ? 'unknown' : 'idle', decision, text: decision?.revisionText || '' }
  })
  const [editing, setEditing] = useState(null)
  const input = useRef(null), trigger = useRef(null), live = useRef(true)
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
  const close = () => { setEditing(null); queueMicrotask(() => trigger.current?.focus()) }
  useEffect(() => { if (editing) input.current?.focus() }, [editing])
  const query = `?session=${encodeURIComponent(route.session)}`
  const readOperation = async id => call(`${BASE}/operations/${encodeURIComponent(id)}${query}`, route)
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
    update({ request: data.request, operation: data.operation, phase: status === 'delivered' ? 'confirmed' : status, error: status === 'failed' ? 'Decision recorded; delivery unconfirmed. Work may have started. Retry uses the same operation.' : '' })
    if (['delivered','cancelled'].includes(status)) { try { window.localStorage.removeItem(INTENT_PREFIX + key) } catch {} }
    saveReadback(route, data.request)
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
  useEffect(() => { if ((r.resolution?.operationId || (state.phase === 'unknown' && state.decision)) && !state.operation) void check() }, [r.resolution?.operationId, state.decision?.idempotencyKey])
  useEffect(() => {
    const poll = () => { if (['pending','failed','unknown'].includes(state.phase)) void check() }
    readbacks.add(poll); return () => readbacks.delete(poll)
  }, [state.phase, state.operation, state.decision])
  useEffect(() => {
    const onStorage = e => {
      if (e.key !== INTENT_PREFIX + key || operations.get(key)?.decision) return
      const decision = recoverIntent(key, request)
      if (decision) update({ decision, phase: 'unknown', text: decision.revisionText || '' })
    }
    window.addEventListener('storage', onStorage); return () => window.removeEventListener('storage', onStorage)
  }, [key, request.revision])
  const send = async (choice, retry = false) => {
    if (!currentRoute(route) || unavailable || ['submitting','checking'].includes(operations.get(key)?.phase)) return
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
    try {
      if (retry) {
        const found = await findOperation(decision)
        if (!currentRoute(route)) return
        if (found?.operation && operationState(found.operation) !== 'failed') { apply(await readOperation(operationId(found.operation))); return }
      }
      const result = await call(`${BASE}/requests/${encodeURIComponent(r.requestId)}/decisions`, { ...route, method: 'POST', body: decision })
      if (!currentRoute(route)) return
      if (!operationId(result?.operation)) throw Error('Missing operation receipt; outcome unknown')
      update({ operation: result.operation, phase: 'checking' })
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
    }
  }
  const choices = Array.isArray(r.choices) ? r.choices : []
  const selected = choices.find(c => c.id === (r.resolution?.choiceId || state.operation?.decision?.choiceId || state.decision?.choiceId))
  // Pre-v2 migrations stored one placeholder choice; it is agent work, never a user-facing control.
  const legacyPlaceholder = c => c.id === 'request-context' && c.effect === 'revise' && c.label === 'Request complete action'
  const question = ['choice','input'].includes(r.kind)
  const approvalPending = !question && (!r.scope?.complete || !r.scope?.markdown?.trim() || r.state !== 'open' || (r.kind === 'approval' && !choices.some(c => c.effect === 'approve')))
  const shown = choices.filter(c => !legacyPlaceholder(c) && (!approvalPending || c.effect !== 'revise'))
  const approvalReason = approvalPending ? (r.kind === 'approval'
    ? 'Agent completion pending — the agent must provide the full exact plan and approvable action. Work stays blocked; no action is needed from you.'
    : 'Agent completion pending — the agent must provide the complete step. Work stays blocked; no action is needed from you.')
    : question && !shown.length ? 'Answer in chat — this question closes once your reply is recorded there. No permission is granted.' : ''
  const phaseText = { idle: '', submitting: 'Recording decision…', checking: 'Checking authoritative delivery status…', pending: 'Decision recorded; delivery pending. Work is not confirmed resumed.', failed: 'Decision recorded; delivery unconfirmed. Work may have started.', unknown: 'Delivery outcome unknown.', cancelled: 'Decision not started — no permission was delivered. Ask the agent for a replacement revision, then decide again.', confirmed: 'Confirmed — decision delivered.', stale: 'Scope updated — review required.' }[state.phase]
  return jsxs('article', { 'data-action-request': requestKey(r), tabIndex: -1, 'aria-label': r.title, style: { ...rowStyle, minWidth: 0 }, children: [
    jsx('h3', { style: { margin: '4px 0 0', fontSize: 14, overflowWrap: 'anywhere' }, children: r.title }),
    jsx('small', { style: { color: 'var(--muted-foreground)', fontSize: 11 }, children: `Revision ${r.revision}` }),
    jsx('h4', { style: sectionLabel, children: 'Read this' }),
    jsx('div', { 'data-action-scope': '', style: { ...planStyle, whiteSpace: 'normal', lineHeight: 1.55, maxHeight: '30vh', overflowY: 'auto' }, tabIndex: 0, 'aria-label': r.kind === 'approval' ? 'Complete plan' : 'Question', children: jsx(MessageTextContent, { media: false, text: r.scope?.markdown || (r.kind === 'approval' ? 'The full plan has not been provided.' : 'The question has not been provided.') }) }),
    !!r.limits?.length && jsxs('div', { children: [jsx('strong', { children: 'Limits' }), jsx('ul', { style: { margin: '4px 0', paddingLeft: 20, ...planStyle }, children: r.limits.map((l,i) => jsx('li', { children: `${l.key}: ${l.value}${l.permissionRequired ? ` — separately requires ${l.permissionRequired}` : ''}` }, i)) })] }),
    !!r.exclusions?.length && jsxs('div', { style: planStyle, children: [jsx('strong', { children: 'Not included' }), jsx('ul', { style: { margin: '4px 0', paddingLeft: 20 }, children: r.exclusions.map((text,i) => jsx('li', { children: text }, i)) })] }),
    jsx('h4', { style: { ...sectionLabel, paddingTop: 10, borderTop: '1px solid var(--border)' }, children: 'Your decision' }),
    jsx('div', { style: { ...planStyle, lineHeight: 1.5 }, children: shown.map(c => r.outcomes?.[c.effect] && jsxs('div', { 'data-decision-outcome': '', children: [jsx('strong', { children: `${choiceLabel(c, choices, r.kind)}: ` }), jsx(MessageTextContent, { media: false, text: `${c.label}. ${r.outcomes[c.effect]}` })] }, c.id)) }),
    !locked && approvalReason && jsx('div', { 'data-approval-blocked': '', 'data-agent-completion-pending': '', style: { color: AMBER, padding: '8px 0', fontSize: 12 }, children: approvalReason }),
    jsx('div', { role: 'status', 'aria-live': 'polite', children: phaseText }),
    selected && locked && jsx('div', { children: `${state.operation || r.resolution ? 'Recorded choice' : 'Submitted choice'}: ${selected.label}. ${state.phase === 'cancelled' ? 'Cancelled before delivery.' : r.outcomes?.[selected.effect] || ''}` }),
    state.error && jsx('div', { role: 'alert', children: state.error }),
    !locked && jsxs('div', { style: flowStyle, children: [
      r.kind === 'approval' && !choices.some(c => c.effect === 'approve') && jsx('button', { type: 'button', style: choiceStyle('approve'), disabled: true, title: 'Agent completion pending.', children: 'Approve' }),
      ...shown.map(c => jsx('button', { type: 'button', 'data-decision-effect': c.effect, title: c.label, style: choiceStyle(c.effect), disabled: unavailable || (['approve','answer'].includes(c.effect) && (!r.scope?.complete || !r.scope?.markdown?.trim() || r.state !== 'open')), onClick: e => { if (needsText(c)) { trigger.current = e.currentTarget; setEditing(c) } else void send(c) }, children: choiceLabel(c, choices, r.kind) }, c.id))
    ] }),
    editing && jsxs('div', { onKeyDown: e => { e.stopPropagation(); if (e.key === 'Escape') { e.preventDefault(); close() } else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void send(editing) } }, children: [
      jsx('label', { children: jsxs('span', { children: ['Revision or answer', jsx('textarea', { ref: input, 'aria-label': 'Revision or answer', value: state.text || '', disabled: locked, onChange: e => update({ text: e.target.value }), rows: 3, style: { width: '100%', boxSizing: 'border-box', font: 'inherit', color: 'var(--foreground)', background: 'var(--card)', ...planStyle } })] }) }),
      jsxs('div', { style: flowStyle, children: [jsx('button', { type: 'button', style: actionStyle, disabled: locked || !state.text?.trim(), onClick: () => send(editing), children: editing.effect === 'answer' ? 'Send answer' : 'Send revision' }), jsx('button', { type: 'button', style: actionStyle, onClick: close, children: 'Cancel' })] })
    ] }),
    ['pending','failed','unknown'].includes(state.phase) && jsxs('div', { style: flowStyle, children: [jsx('button', { type: 'button', style: actionStyle, onClick: check, children: 'Check status' }), jsx('button', { type: 'button', style: actionStyle, onClick: () => send(null, true), children: 'Retry' })] })
  ] })
}

const MINIMIZED_KEY = 'hermes.userSteps.minimized.v1'
function readMinimized() {
  try { const v = JSON.parse(window.localStorage.getItem(MINIMIZED_KEY) || '[]'); return Array.isArray(v) ? v.filter(k => typeof k === 'string') : [] } catch { return [] }
}
function writeMinimized(keys) {
  try { if (keys.length) window.localStorage.setItem(MINIMIZED_KEY, JSON.stringify(keys.slice(-200))); else window.localStorage.removeItem(MINIMIZED_KEY) } catch {}
}
export function ActionQueue() {
  const [snap, setSnap] = useState(canonical), [filter, setFilter] = useState('')
  const [minimizedKeys, setMinimizedState] = useState(readMinimized)
  const setMinimized = keys => { writeMinimized(keys); setMinimizedState(keys) }
  useValue(host.state.focusedStoredSessionId)
  useValue(host.state.focusedSessionOwner || host.state.connectionId || host.state.focusedStoredSessionId)
  useValue(host.state.profile || host.state.focusedStoredSessionId)
  useValue(host.state.connectionId || host.state.focusedStoredSessionId)
  const route = decisionRoute(), scope = routeKey(route)
  useEffect(() => { canonicalSubs.add(setSnap); return () => canonicalSubs.delete(setSnap) }, [])
  useEffect(() => { void refreshRequests() }, [scope])
  if (!route.session || snap.scope !== scope) return null
  const needsReadback = r => {
    const key = `${scope}:${requestKey(r)}`, op = operations.get(key)
    if (['confirmed','cancelled'].includes(op?.phase) && op.request?.revision === r.revision && operationId(op.operation) === r.resolution?.operationId) return false
    return !!(op?.decision || r.resolution?.operationId || recoverIntent(key, r))
  }
  const requests = snap.requests.filter(r => (!r.resolution && ['open','needs_context'].includes(r.state)) || needsReadback(r))
  if (!requests.length && !snap.error) return null
  const visible = requests.filter(r => needsReadback(r) || `${r.title} ${r.scope?.markdown}`.toLowerCase().includes(filter.toLowerCase()))
  // Minimized holds only while every shown request was already shown when the user minimized; a new one (or a new
  // revision) reopens the panel so a fresh ask is never hidden. Persisted so it survives reloads and chat switches.
  const openKeys = requests.map(r => `${requestKey(r)}#${r.revision}`)
  const minimized = !snap.error && openKeys.length > 0 && openKeys.every(k => minimizedKeys.includes(k))
  return jsxs('section', { 'data-action-queue': '', 'aria-label': 'Session decisions', onKeyDown: e => { if (['Enter','Escape','ArrowDown','ArrowUp'].includes(e.key)) e.stopPropagation() }, style: { ...SELECTABLE, width: '100%', minWidth: 0, boxSizing: 'border-box', padding: 10, border: '1px solid var(--border)', borderRadius: 8, background: 'var(--card)', color: 'var(--foreground)' }, children: [
    jsx('style', { children: '[data-action-queue] [data-streamdown="strong"]{font-weight:600}[data-decision-outcome]{margin:4px 0}[data-decision-outcome] .aui-md,[data-decision-outcome] .aui-md>div,[data-decision-outcome] p{display:inline;margin:0}[data-action-queue] button:disabled{opacity:.5;cursor:not-allowed}[data-action-queue] button:not(:disabled):hover{filter:brightness(1.15)}[data-action-scope] :is(pre,table){max-width:100%;overflow:auto}[data-action-queue] :is(button,textarea,input,[tabindex]):focus-visible{outline:2px solid var(--ring,var(--primary));outline-offset:2px}' }),
    jsxs('div', { 'data-action-queue-header': '', style: { display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }, children: [
      jsx('h2', { style: { margin: 0, fontSize: 14, flex: '0 1 auto', minWidth: 0, overflowWrap: 'anywhere' }, children: (n => n ? `For you (${n})` : `Waiting on the agent (${requests.length}) — nothing needed from you`)(requests.filter(r => ['open','needs_context'].includes(r.state) && !agentPending(r)).length) }),
      minimized && jsx('span', { 'data-action-queue-summary': '', style: { flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 12.5, color: 'var(--muted-foreground)' }, children: requests.map(r => r.title).join(' · ') }),
      jsx('button', { type: 'button', 'data-action-queue-toggle': '', 'aria-expanded': !minimized, 'aria-label': minimized ? 'Expand decisions' : 'Minimize decisions', title: minimized ? 'Expand' : 'Minimize (reopens when a new decision arrives)', onClick: () => setMinimized(minimized ? minimizedKeys.filter(k => !openKeys.includes(k)) : [...new Set([...minimizedKeys, ...openKeys])]), style: { ...btn(), marginLeft: 'auto', flex: 'none', minHeight: 28, minWidth: 28, padding: '2px 6px' }, children: jsx('i', { 'aria-hidden': true, className: `codicon ${minimized ? 'codicon-chevron-up' : 'codicon-chevron-down'}` }) })
    ] }),
    !minimized && snap.error && jsxs('div', { role: 'alert', children: [snap.error, jsx('button', { style: actionStyle, onClick: () => refreshRequests(), children: 'Reload actions' })] }),
    !minimized && requests.length > 1 && jsx('input', { 'aria-label': 'Filter decisions', value: filter, onChange: e => setFilter(e.target.value), onKeyDown: e => { e.stopPropagation(); if (['ArrowDown','Enter'].includes(e.key)) { e.preventDefault(); e.currentTarget.closest('section').querySelector('article')?.focus() } }, style: { width: '100%', boxSizing: 'border-box', minHeight: 44 } }),
    !minimized && jsx('div', { onKeyDown: e => { if (e.target.tagName !== 'ARTICLE') return; const rows = [...e.currentTarget.querySelectorAll('article')], i = rows.indexOf(e.target); if (['ArrowDown','ArrowUp','Enter'].includes(e.key)) { e.stopPropagation(); e.preventDefault(); if (e.key === 'Enter') e.target.querySelector('button:not(:disabled)')?.focus(); else rows[(i + (e.key === 'ArrowDown' ? 1 : rows.length - 1)) % rows.length]?.focus() } }, children: visible.map(r => jsx(DecisionRow, { request: r, route, unavailable: !!snap.error }, `${scope}:${requestKey(r)}`)) })
  ] })
}

export default {
  id: ID,
  name: 'User Steps',
  register(ctx) {
    osApi = ctx?.os || null
    previewApi = typeof ctx?.openPreview === 'function' ? (t => ctx.openPreview(t)) : null
    ctx.register({ id: 'strip', area: COMPOSER_AREAS.top, order: -25, render: () => jsxs('div', { style: { minWidth: 0, width: '100%' }, children: [jsx(ActionQueue, {}), jsx(Strip, {})] }) })
    ctx.register({ id: 'user-step-directive', area: TRANSCRIPT_DIRECTIVE_AREA, data: { name: 'user-step', render: props => jsx(StepBox, props) } })
    const onVis = () => { if (typeof document !== 'undefined' && document.hidden) return; void refresh(); void refreshRequests() }
    if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVis)
    if (typeof window !== 'undefined') window.__userSteps = { version: VERSION, ownsComposer: true, get snapshot() { return snapshot }, refresh, subscribe: fn => { canonicalSubs.add(fn); return () => canonicalSubs.delete(fn) }, requestForSource: source => canonical.scope === routeKey(decisionRoute()) ? requestForSource(canonical.requests, source) : null }
    signalDecisionChanges()
    return () => {
      if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVis)
      if (typeof window !== 'undefined' && window.__userSteps?.version === VERSION) delete window.__userSteps
      signalDecisionChanges()
      unsubKanban?.()
      unsubKanban = null
      if (timer) clearInterval(timer)
      timer = null
    }
  }
}

/**
 * Session Panel — one panel above the composer that answers, for the focused chat:
 *   is work moving, how much is left, what is waiting on me, and what is stuck.
 *
 *   header       agent working now / idle since …, and a progress bar over the chat's kanban cards:
 *                done · running · needs you · stuck · waiting · queued
 *   Needs you    user-steps' decision cards (window.__userSteps.ActionQueue, embedded) and its earlier asks
 *   Stuck        a running card with no event for 2 h, a parked card whose agent-owned wait stalled
 *                (backend `wait.wait_state === 'stalled'`), or a sub-agent whose tool count has not moved
 *                for 30 min; each with numbered steps and Nudge agent / Open card. A stale chip (session-tasks'
 *                last-known copy after a failed read) is never stuck or needs-you: it lists under Waiting as
 *                "status unknown", since the card may have been answered or archived since
 *   In progress  running and in-review cards, sub-agents, background processes, rented machines
 *   Waiting      parked cards and what they wait on
 *   footer       finished and not-yet-started cards, folded
 *
 * Data: session-tasks' snapshot (window.__sessionTasks: cards, sub-agents, processes, machines; it keeps polling),
 * user-steps' asks (window.__userSteps), and finished cards from user-steps' /session-cards (every 30 s; core's
 * list has no finished cards). While this plugin is loaded, session-tasks and user-steps hide their own strips
 * (they check window.__sessionPanel.active). Nudge only drafts a message in the composer; the user sends it.
 */
import { COMPOSER_AREAS, host, useValue } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'session-panel'
const VERSION = 'session-panel-v1'
export const STUCK_CARD_S = 2 * 3600 // running card with no event this long
export const STUCK_SUB_MS = 30 * 60_000 // sub-agent with no new tool call this long
const DONE_POLL_MS = 30_000
const COLLAPSED_KEY = 'hermes.sessionPanel.collapsed.v1'

const str = v => (typeof v === 'string' ? v : v == null ? '' : String(v))
const log = (...a) => console.info(`[${ID}]`, ...a)

// ── pure helpers (exported for tests) ───────────────────────────────────────

export function ago(sec, nowMs = Date.now()) {
  const t = Number(sec)
  if (!t) return ''
  const s = Math.max(0, Math.floor(nowMs / 1000 - t))
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86_400) return `${Math.floor(s / 3600)} h ago`
  return `${Math.floor(s / 86_400)} d ago`
}

export function clock(sec, nowMs = Date.now()) {
  const t = Number(sec)
  if (!t) return ''
  const d = new Date(t * 1000), now = new Date(nowMs)
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  return d.toDateString() === now.toDateString() ? hm : `${['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()]} ${d.getDate()} ${hm}`
}

/** Sub-agent silence: key → { count, at } updated in place; returns key → ms since its tool count last changed.
 *  A sub-agent first seen now counts from now, so a panel opened late never calls it stuck on sight. */
export function trackSubagents(chips, nowMs, memory) {
  const out = new Map(), live = new Set()
  for (const c of chips || []) {
    if (c?.kind !== 'sub') continue
    live.add(c.key)
    const seen = memory.get(c.key)
    if (!seen || seen.count !== c.toolCount) memory.set(c.key, { count: c.toolCount, at: nowMs })
    out.set(c.key, nowMs - memory.get(c.key).at)
  }
  for (const k of [...memory.keys()]) if (!live.has(k)) memory.delete(k)
  return out
}

/** Sort the chat's work into the panel's sections. `isAsked(chip)` = a user-steps ask already covers this card. */
export function classify(chips, { nowMs = Date.now(), silence = new Map(), isAsked = () => false } = {}) {
  const now = nowMs / 1000
  const out = { active: [], stuck: [], waiting: [], queued: [], unasked: [], lastActivity: 0 }
  const seen = at => { if (Number(at) > out.lastActivity) out.lastActivity = Number(at) }
  for (const c of chips || []) {
    if (!c || !c.key) continue
    if (c.kind === 'bg') { out.active.push({ ...item(c), detail: `background process · started ${ago(c.since, nowMs)}` }); seen(c.since); continue }
    if (c.kind === 'machine') { out.active.push({ ...item(c), detail: 'rented machine' }); continue }
    if (c.kind === 'sub') {
      const quiet = silence.get(c.key) || 0
      seen(nowMs / 1000 - quiet / 1000)
      const tools = `${c.toolCount || 0} tool call${c.toolCount === 1 ? '' : 's'}${c.lastTool ? `, last ${c.lastTool}` : ''}`
      if (quiet >= STUCK_SUB_MS) out.stuck.push({ ...item(c), reason: 'sub-silent', quietMin: Math.floor(quiet / 60_000), detail: `sub-agent · no new tool call for ${Math.floor(quiet / 60_000)} min (${tools})` })
      else out.active.push({ ...item(c), detail: `sub-agent · ${tools}` })
      continue
    }
    if (c.kind === 'kanban-status') { out.waiting.push({ ...item(c), detail: str(c.title) || 'status unknown' }); continue }
    if (c.kind !== 'kanban') continue
    seen(c.activityAt)
    const last = Math.max(Number(c.activityAt) || 0, Number(c.since) || 0)
    const on = c.waitingOn ? `waiting on ${c.waitingOn}${c.waitingSince ? ` since ${clock(c.waitingSince, nowMs)}` : ''}` : ''
    // A stale chip is session-tasks' last-known copy after a failed read: the card may have been answered,
    // re-blocked or archived since, so it is never stuck (no nudge) or needs-you, only "status unknown".
    if (c.stale) { out.waiting.push({ ...item(c), detail: `status unknown · last known: ${c.look === 'needs-you' ? 'needs you' : c.state || 'unknown'}${last ? ` (${ago(last, nowMs)})` : ''}; the card could not be re-read` }); continue }
    if (c.look === 'needs-you') { if (!isAsked(c)) out.unasked.push({ ...item(c), detail: on || 'needs you' }); continue }
    if (c.look === 'stalled') { out.stuck.push({ ...item(c), reason: 'stalled', detail: `stalled: ${on || 'agent-owned wait over 2 h'}` }); continue }
    if (c.state === 'running') {
      if (last && now - last >= STUCK_CARD_S) out.stuck.push({ ...item(c), reason: 'no-progress', lastAt: last, detail: `running, no progress since ${clock(last, nowMs)} (${ago(last, nowMs)})${c.activity ? ` · last event: ${c.activity}` : ''}` })
      else out.active.push({ ...item(c), detail: `running${c.assignee ? ` · ${c.assignee}` : ''}${last ? ` · ${ago(last, nowMs)}` : ''}` })
      continue
    }
    if (c.state === 'review') { out.active.push({ ...item(c), detail: `in review${last ? ` · ${ago(last, nowMs)}` : ''}` }); continue }
    if (['todo', 'ready'].includes(c.state)) { out.queued.push({ ...item(c), detail: c.state === 'ready' ? 'ready to start' : 'not started' }); continue }
    out.waiting.push({ ...item(c), detail: on || (c.state === 'scheduled' ? 'scheduled' : c.state === 'unknown' ? 'status unknown' : c.state) })
  }
  return out
}

function item(c) {
  return { key: c.key, kind: c.kind, label: str(c.label) || str(c.id) || 'task', id: c.id || null, chip: c }
}

/** Progress-bar counts. Cards only (sub-agents and processes are listed, not counted); needs you = open asks
 *  plus unasked cards that need the user. */
export function counts(sorted, { asks = 0, done = null } = {}) {
  const cards = list => list.filter(i => i.kind === 'kanban').length
  return {
    done: done ?? 0,
    running: cards(sorted.active),
    needsYou: asks + sorted.unasked.length,
    stuck: cards(sorted.stuck),
    waiting: cards(sorted.waiting),
    queued: cards(sorted.queued)
  }
}

/** Numbered steps that get a stuck item moving again. Only steps this panel can start, or the agent will take. */
export function unblockSteps(i, nowMs = Date.now()) {
  const back = 'If the agent needs something from you, it comes back under Needs you.'
  if (i.reason === 'stalled') {
    return [`It is ${i.chip.waitingOn ? `waiting on ${i.chip.waitingOn}` : 'parked'}${i.chip.waitingSince ? ` since ${clock(i.chip.waitingSince, nowMs)}` : ''}, and nothing is producing that.`,
      'Nudge agent drafts a message asking the agent to restart or reroute it; you press send.', back]
  }
  if (i.reason === 'no-progress') {
    return [`No events on this card since ${clock(i.lastAt, nowMs)}${i.chip.activity ? ` (last: ${i.chip.activity})` : ''}.`,
      'Open card to read its last run, or Nudge agent to have it check the worker.', back]
  }
  return [`No new tool call for ${i.quietMin} min${i.chip.lastTool ? ` (last: ${i.chip.lastTool})` : ''}.`,
    'Nudge agent drafts a message asking the agent to check on it: let it finish, restart it, or stop it.', back]
}

/** The composer draft Nudge agent writes. */
export function nudgeText(i, nowMs = Date.now()) {
  if (i.kind === 'sub') {
    return `Sub-agent "${i.label}" has made no tool call for ${i.quietMin} min. Check on it: let it finish, restart it, or stop it, and tell me if you need anything from me.\n`
  }
  const why = i.reason === 'stalled'
    ? `has been ${i.chip.waitingOn ? `waiting on ${i.chip.waitingOn}` : 'parked'}${i.chip.waitingSince ? ` since ${clock(i.chip.waitingSince, nowMs)}` : ''} with nothing producing it`
    : `has had no progress since ${clock(i.lastAt, nowMs)}${i.chip.activity ? ` (last event: ${i.chip.activity})` : ''}`
  return `Card ${i.id} ("${i.label}") ${why}. Check it, then either get it moving or tell me exactly what you need from me.\n`
}

/** Finished cards among `/session-cards` rows (one per qualified card), newest first. */
export function doneRows(tasks) {
  const seen = new Set(), out = []
  for (const t of Array.isArray(tasks) ? tasks : []) {
    const key = `${str(t?.gateway)}:${str(t?.profile)}:${str(t?.board) || 'default'}:${str(t?.id)}`
    if (!t?.id || str(t.status) !== 'done' || seen.has(key)) continue
    seen.add(key)
    out.push({ key, id: t.id, label: str(t.title) || t.id, at: Number(t.activity_at) || 0 })
  }
  return out.sort((a, b) => b.at - a.at)
}

/** Attention keys: a new ask or a new stuck item reopens a collapsed panel. */
export const attentionKeys = (asks, sorted) => [`asks:${asks}`, ...sorted.unasked.map(i => i.key), ...sorted.stuck.map(i => i.key)]

// ── data ────────────────────────────────────────────────────────────────────

const panelRoute = () => {
  const owner = host.state?.focusedSessionOwner?.get?.()
  return {
    connectionId: owner ? owner.connectionId : host.state?.connectionId?.get?.() || null,
    profile: owner ? (owner.targetProfile || owner.profile || 'default') : (host.state?.profile?.get?.() || 'default'),
    session: host.state?.focusedStoredSessionId?.get?.() || null
  }
}
const routeKey = r => JSON.stringify([r.connectionId || '', r.profile, r.session])

let done = { scope: null, rows: null } // rows null = unknown (older owner, read failed)
const doneSubs = new Set()
let doneTimer = null

export async function refreshDone() {
  const route = panelRoute(), scope = routeKey(route)
  const st = typeof window !== 'undefined' ? window.__sessionTasks : null
  const bridge = typeof window !== 'undefined' ? window.hermesDesktop?.api : null
  if (!route.session || !route.connectionId || !bridge || typeof st?.lineageIds !== 'function') return
  try {
    const ids = await st.lineageIds(route.session, route.connectionId, route.profile)
    if (ids?.incomplete) return
    const r = await bridge({ path: `/api/plugins/user-steps/session-cards?ids=${encodeURIComponent([...ids].join(','))}`, connectionId: route.connectionId, profile: route.profile, timeoutMs: 15_000 })
    if (routeKey(panelRoute()) !== scope) return
    // Only an answer that says it lists finished cards counts; anything else leaves done unknown.
    done = { scope, rows: Array.isArray(r?.tasks) && Array.isArray(r.includes) && r.includes.includes('done') ? doneRows(r.tasks) : null }
  } catch (e) {
    log('session-cards read failed', e?.message || e)
    if (done.scope !== scope) done = { scope, rows: null }
  }
  doneSubs.forEach(fn => fn(done))
}

function subscribeDone(fn) {
  doneSubs.add(fn)
  if (!doneTimer) doneTimer = setInterval(() => { if (typeof document === 'undefined' || !document.hidden) void refreshDone() }, DONE_POLL_MS)
  return () => { doneSubs.delete(fn); if (!doneSubs.size && doneTimer) { clearInterval(doneTimer); doneTimer = null } }
}

const subMemory = new Map()

function readCollapsed() {
  try { const v = JSON.parse(window.localStorage.getItem(COLLAPSED_KEY) || 'null'); return Array.isArray(v) ? v : null } catch { return null }
}
function writeCollapsed(keys) {
  try { if (keys) window.localStorage.setItem(COLLAPSED_KEY, JSON.stringify(keys)); else window.localStorage.removeItem(COLLAPSED_KEY) } catch {}
}

// ── UI ──────────────────────────────────────────────────────────────────────

const C = {
  done: 'var(--success, #3fb950)', running: 'var(--primary, #3b82f6)', needsYou: 'var(--warning, #d29922)',
  stuck: 'var(--destructive, #f85149)', waiting: 'var(--muted-foreground, #8b949e)', queued: 'color-mix(in srgb, var(--muted-foreground, #8b949e) 45%, transparent)'
}
const LABEL = { done: 'done', running: 'running', needsYou: 'needs you', stuck: 'stuck', waiting: 'waiting', queued: 'not started' }
const muted = { color: 'var(--muted-foreground)', fontSize: 12 }
const heading = color => ({ margin: '12px 0 6px', fontSize: 12, fontWeight: 600, color })
const btn = { minHeight: 32, padding: '4px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'transparent', color: 'var(--foreground)', font: 'inherit', fontSize: 12.5, cursor: 'pointer' }

function Bar({ n }) {
  const total = Object.values(n).reduce((a, b) => a + b, 0)
  if (!total) return null
  const order = ['done', 'running', 'needsYou', 'stuck', 'waiting', 'queued']
  return jsxs('div', { children: [
    jsx('div', { 'data-progress-bar': '', role: 'img', 'aria-label': order.filter(k => n[k]).map(k => `${n[k]} ${LABEL[k]}`).join(', '), style: { display: 'flex', height: 6, borderRadius: 3, overflow: 'hidden', margin: '8px 0 6px', background: 'color-mix(in srgb, var(--border) 60%, transparent)' },
      children: order.filter(k => n[k]).map(k => jsx('div', { 'data-segment': k, style: { flex: n[k], background: C[k] } }, k)) }),
    jsx('div', { 'data-progress-legend': '', style: { display: 'flex', flexWrap: 'wrap', columnGap: 12, rowGap: 2, ...muted }, children: order.filter(k => n[k]).map(k =>
      jsxs('span', { 'data-count': k, children: [jsx('span', { 'aria-hidden': true, style: { color: C[k] }, children: '● ' }), `${n[k]} ${LABEL[k]}`] }, k)) })
  ] })
}

function Row({ i, color }) {
  return jsxs('div', { 'data-panel-row': i.key, style: { display: 'grid', gridTemplateColumns: '12px minmax(0,1fr)', columnGap: 6, padding: '3px 0', fontSize: 13 }, children: [
    jsx('span', { 'aria-hidden': true, style: { color, fontSize: 10, lineHeight: '19px' }, children: '●' }),
    jsxs('div', { style: { minWidth: 0 }, children: [
      jsx('div', { title: str(i.chip?.title) || i.label, style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: i.label }),
      i.detail && jsx('div', { style: { ...muted, overflowWrap: 'anywhere' }, children: i.detail })
    ] })
  ] })
}

function StuckCard({ i, session, now }) {
  const st = typeof window !== 'undefined' ? window.__sessionTasks : null
  const canOpen = i.kind === 'kanban' && !!i.chip.connectionId && !!i.chip.profile && typeof st?.openTaskChip === 'function'
  const nudge = () => { void host.composer?.setDraft?.(session, nudgeText(i, now)); host.composer?.focus?.(session) }
  return jsxs('div', { 'data-stuck': i.key, style: { border: `1px solid color-mix(in srgb, ${C.stuck} 45%, transparent)`, borderRadius: 6, padding: '8px 10px', marginBottom: 6 }, children: [
    jsx('div', { style: { fontWeight: 600, fontSize: 13, overflowWrap: 'anywhere' }, children: i.label }),
    jsx('div', { style: { ...muted, marginBottom: 4, overflowWrap: 'anywhere' }, children: i.detail }),
    jsx('ol', { 'data-unblock-steps': '', style: { margin: '0 0 8px', paddingLeft: 18, fontSize: 12.5, lineHeight: 1.5 }, children: unblockSteps(i, now).map((s, n) => jsx('li', { children: s }, n)) }),
    jsxs('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 6 }, children: [
      jsx('button', { type: 'button', 'data-nudge': '', style: btn, title: 'Drafts a message to the agent in the composer; nothing is sent until you send it', onClick: nudge, children: 'Nudge agent' }),
      canOpen && jsx('button', { type: 'button', 'data-open-card': '', style: { ...btn, borderColor: 'transparent', color: 'var(--muted-foreground)' }, onClick: () => st.openTaskChip(i.chip), children: 'Open card' })
    ] })
  ] })
}

function Fold({ label, items, color }) {
  if (!items.length) return null
  return jsxs('details', { 'data-fold': label, children: [
    jsx('summary', { style: { cursor: 'pointer', minHeight: 28, display: 'flex', alignItems: 'center', ...muted }, children: label }),
    ...items.map(i => jsx(Row, { i, color }, i.key))
  ] })
}

export function Panel() {
  const [tasks, setTasks] = useState(() => (typeof window !== 'undefined' ? window.__sessionTasks?.snapshot : null) || null)
  const [doneState, setDone] = useState(done)
  const [, setTick] = useState(0)
  const [collapsedKeys, setCollapsedState] = useState(readCollapsed)
  const focused = useValue(host.state.focusedSessionId)
  const stored = useValue(host.state.focusedStoredSessionId)
  const busy = useValue(host.state.busy)
  useValue(host.state.focusedSessionOwner || host.state.connectionId)
  useValue(host.state.connectionId)
  useValue(host.state.profile || host.state.connectionId)
  const route = panelRoute(), scope = routeKey(route)
  const US = typeof window !== 'undefined' ? window.__userSteps : null
  useEffect(() => {
    const st = window.__sessionTasks
    const offTasks = typeof st?.subscribe === 'function' ? st.subscribe(setTasks) : null
    const offAsks = typeof US?.subscribe === 'function' ? US.subscribe(() => setTick(t => t + 1)) : null
    const offDone = subscribeDone(setDone)
    return () => { offTasks?.(); offAsks?.(); offDone() }
  }, [])
  useEffect(() => { void refreshDone(); void window.__sessionTasks?.refresh?.() }, [scope])
  if (!route.session) return null
  const now = Date.now()
  const chips = tasks && tasks.runtime === focused && tasks.stored === stored ? tasks.chips || [] : []
  const silence = trackSubagents(chips, now, subMemory)
  const isAsked = c => !!US?.requestForSource?.({ gatewayId: c.gatewayId || c.gateway, profile: c.profile, type: 'kanban', board: c.board, id: c.id })
  const sorted = classify(chips, { nowMs: now, silence, isAsked })
  const asks = typeof US?.openAskCount === 'function' ? US.openAskCount() : 0
  const finished = doneState.scope === scope ? doneState.rows : null
  const n = counts(sorted, { asks, done: finished ? finished.length : null })
  const legacy = US?.Strip ? jsx(US.Strip, {}) : null
  const anything = asks || sorted.unasked.length || chips.length || finished?.length || US?.snapshot?.ledger?.steps?.length || US?.snapshot?.issues?.length
  if (!anything) return null
  const attention = attentionKeys(asks, sorted)
  const collapsed = !!collapsedKeys && attention.every(k => collapsedKeys.includes(k))
  const toggle = () => { const next = collapsed ? null : attention; writeCollapsed(next); setCollapsedState(next) }
  const status = busy ? 'Agent working now' : sorted.lastActivity ? `Idle · last activity ${ago(sorted.lastActivity, now)}` : 'Idle'
  const summary = Object.keys(LABEL).filter(k => n[k]).map(k => `${n[k]} ${LABEL[k]}`).join(' · ')
  return jsxs('section', { 'data-session-panel': '', 'aria-label': 'Session status', style: { width: '100%', minWidth: 0, boxSizing: 'border-box', padding: '10px 12px', border: '1px solid var(--border)', borderRadius: 8, background: 'var(--card)', color: 'var(--foreground)', userSelect: 'text', WebkitUserSelect: 'text' }, children: [
    jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }, children: [
      jsx('h2', { style: { margin: 0, fontSize: 13.5, fontWeight: 600, flex: 'none' }, children: 'This session' }),
      jsxs('span', { 'data-session-status': '', style: { ...muted, display: 'flex', alignItems: 'center', gap: 5, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: [busy && jsx('span', { 'aria-hidden': true, style: { color: C.running }, children: '●' }), status] }),
      collapsed && summary && jsx('span', { 'data-collapsed-summary': '', style: { ...muted, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: `· ${summary}` }),
      jsx('button', { type: 'button', 'data-panel-toggle': '', 'aria-expanded': !collapsed, 'aria-label': collapsed ? 'Expand session status' : 'Collapse session status', title: collapsed ? 'Expand' : 'Collapse (reopens when something new needs you or gets stuck)', onClick: toggle, style: { ...btn, marginLeft: 'auto', flex: 'none', minHeight: 28, minWidth: 28, padding: '2px 6px' }, children: jsx('i', { 'aria-hidden': true, className: `codicon ${collapsed ? 'codicon-chevron-up' : 'codicon-chevron-down'}` }) })
    ] }),
    !collapsed && jsx(Bar, { n }),
    !collapsed && jsxs('div', { 'data-panel-body': '', style: { maxHeight: '55vh', overflowY: 'auto', minWidth: 0 }, children: [
      (asks > 0 || sorted.unasked.length > 0) && jsx('h3', { 'data-section': 'needs-you', style: heading(C.needsYou), children: `Needs you · ${asks + sorted.unasked.length}` }),
      US?.ActionQueue && jsx(US.ActionQueue, { embedded: true }),
      ...sorted.unasked.map(i => jsx(Row, { i: { ...i, detail: `${i.detail} · answer in chat or open the card` }, color: C.needsYou }, i.key)),
      legacy,
      sorted.stuck.length > 0 && jsx('h3', { 'data-section': 'stuck', style: heading(C.stuck), children: `Stuck · ${sorted.stuck.length}` }),
      ...sorted.stuck.map(i => jsx(StuckCard, { i, session: route.session, now }, i.key)),
      sorted.active.length > 0 && jsx('h3', { 'data-section': 'in-progress', style: heading('var(--muted-foreground)'), children: `In progress · ${sorted.active.length}` }),
      ...sorted.active.map(i => jsx(Row, { i, color: C.running }, i.key)),
      sorted.waiting.length > 0 && jsx('h3', { 'data-section': 'waiting', style: heading('var(--muted-foreground)'), children: `Waiting · ${sorted.waiting.length}` }),
      ...sorted.waiting.map(i => jsx(Row, { i, color: C.waiting }, i.key)),
      (finished?.length || sorted.queued.length) ? jsxs('div', { style: { borderTop: '1px solid color-mix(in srgb, var(--border) 60%, transparent)', marginTop: 10, paddingTop: 4 }, children: [
        jsx(Fold, { label: `Not started · ${sorted.queued.length}`, items: sorted.queued, color: C.queued }),
        jsx(Fold, { label: `Done · ${finished?.length || 0}`, items: (finished || []).map(d => ({ key: d.key, label: d.label, detail: d.at ? `finished ${ago(d.at, now)}` : '' })), color: C.done })
      ] }) : null
    ] })
  ] })
}

export default {
  id: ID,
  name: 'Session Panel',
  register(ctx) {
    if (typeof window !== 'undefined') window.__sessionPanel = { active: true, version: VERSION, refreshDone }
    ctx.register({ id: 'panel', area: COMPOSER_AREAS.top, order: -30, render: () => jsx(Panel, {}) })
    return () => {
      if (typeof window !== 'undefined' && window.__sessionPanel?.version === VERSION) delete window.__sessionPanel
      if (doneTimer) clearInterval(doneTimer)
      doneTimer = null
    }
  }
}

/**
 * Session Panel — one panel above the composer that answers, for the focused chat:
 *   is work moving, how much is left, what is waiting on me, and what is stuck.
 *
 *   header       agent replying in this chat / idle since …, then one small box per kanban card and the counts:
 *                needs you · stuck · running · waiting · not started · done
 *   Needs you    user-steps' decision cards (window.__userSteps.ActionQueue, embedded) and its earlier asks; a card
 *                no ask covers shows its own human_request as "Do this / Why you / Next" (askView), never its
 *                agent-written title or block reason; without a request it says so and offers Ask agent
 *   Stuck        a running card with no event for 2 h, a parked card whose agent-owned wait stalled
 *                (backend `wait.wait_state === 'stalled'`), or a sub-agent whose tool count has not moved
 *                for 30 min; each with numbered steps and Nudge agent / Open card. A stale chip (session-tasks'
 *                last-known copy after a failed read) is never stuck or needs-you: it lists under Waiting as
 *                "status unknown", since the card may have been answered or archived since
 *   In progress  running and in-review cards, sub-agents, background processes, rented machines
 *   Waiting      parked cards and what they wait on
 *   footer       finished and not-yet-started cards, folded
 *   collapsed    the header line alone (caret + counts, needs-you in amber) plus one line per waiting card (up to 3);
 *                the header is the composer status stack's own disclosure row (caret, icon, label), so this panel
 *                folds the same way the task list above it does
 *   hidden       when nothing is open (no ask, open card, sub-agent, process, step or issue): finished cards alone
 *                never show the panel
 *
 * Data: session-tasks' snapshot (window.__sessionTasks: cards, sub-agents, processes, machines; it keeps polling),
 * user-steps' asks (window.__userSteps), and finished cards from user-steps' /session-cards (every 30 s; core's
 * list has no finished cards). While this plugin is loaded, session-tasks and user-steps hide their own strips
 * (they check window.__sessionPanel.active). Nudge only drafts a message in the composer; the user sends it.
 */
import { Codicon, COMPOSER_AREAS, DisclosureCaret, host, useValue } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'session-panel'
const VERSION = 'session-panel-v2'
export const STUCK_CARD_S = 2 * 3600 // running card with no event this long
export const STUCK_SUB_MS = 30 * 60_000 // sub-agent with no new tool call this long
const DONE_POLL_MS = 30_000
const WAIT_LINES_COLLAPSED = 3 // waiting cards named while collapsed; the rest count as "+N more"
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
const TERMINAL = new Set(['done', 'archived', 'cancelled', 'canceled', 'resolved', 'withdrawn'])
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
    // Progress = the newest event, else the current run's start (session-tasks `progressAt`); 0 = unknown. Only a
    // known progress time can make a card Stuck: `since` (first claim / creation) is display only, or a hub card
    // claimed a minute ago read "no progress since <a week ago>" (kanban-f241d1f60b).
    const progress = Number(c.activityAt) || Number(c.progressAt) || 0
    const last = progress || Number(c.since) || 0
    const on = c.waitingOn ? `waiting on ${c.waitingOn}${c.waitingSince ? ` since ${clock(c.waitingSince, nowMs)}` : ''}` : ''
    // A stale chip is session-tasks' last-known copy after a failed read: the card may have been answered,
    // re-blocked or archived since, so it is never stuck (no nudge) or needs-you, only "status unknown".
    if (c.stale) { out.waiting.push({ ...item(c), detail: `status unknown · last known: ${c.look === 'needs-you' ? 'needs you' : c.state || 'unknown'}${last ? ` (${ago(last, nowMs)})` : ''}; the card could not be re-read` }); continue }
    if (c.look === 'needs-you') {
      // A finished, archived or cancelled card asks nothing, whatever wait it last reported.
      if (!TERMINAL.has(str(c.state)) && !isAsked(c)) out.unasked.push({ ...item(c), detail: on || 'needs you' })
      continue
    }
    // Finished cards come from the Done list (/session-cards); a terminal chip here is not waiting on anything.
    if (TERMINAL.has(str(c.state))) continue
    if (c.look === 'stalled') { out.stuck.push({ ...item(c), reason: 'stalled', detail: `stalled: ${on || 'agent-owned wait over 2 h'}` }); continue }
    if (c.state === 'running') {
      if (progress && now - progress >= STUCK_CARD_S) out.stuck.push({ ...item(c), reason: 'no-progress', lastAt: progress, detail: `running, no progress since ${clock(progress, nowMs)} (${ago(progress, nowMs)})${c.activity ? ` · last event: ${c.activity}` : ''}` })
      else out.active.push({ ...item(c), detail: `running${c.assignee ? ` · ${c.assignee}` : ''}${progress ? ` · ${ago(progress, nowMs)}` : ' · last event unknown'}` })
      continue
    }
    if (c.state === 'review') { out.active.push({ ...item(c), detail: `in review${last ? ` · ${ago(last, nowMs)}` : ''}` }); continue }
    if (['todo', 'ready'].includes(c.state)) { out.queued.push({ ...item(c), detail: c.state === 'ready' ? 'ready to start' : 'not started' }); continue }
    out.waiting.push({ ...item(c), detail: on || (c.state === 'scheduled' ? 'scheduled' : c.state === 'unknown' ? 'status unknown' : c.state) })
  }
  return out
}

/** True when the chat has anything not finished: an ask, a card/sub-agent/process that is open, an open ledger
 *  step or an agent issue. Done/archived cards (and a done list) never count: all green = no panel. */
export function hasOpenWork(sorted, { asks = 0, steps = [], issues = [] } = {}) {
  const open = list => (list || []).some(i => !(i.kind === 'kanban' && TERMINAL.has(str(i.chip?.state))))
  return !!(asks || sorted.unasked.length || open(sorted.active) || open(sorted.stuck) || open(sorted.waiting) || open(sorted.queued) ||
    (Array.isArray(steps) && steps.some(s => s?.id && (s.status || 'open') === 'open')) || (Array.isArray(issues) && issues.length))
}

function item(c) {
  return { key: c.key, kind: c.kind, label: str(c.label) || str(c.id) || 'task', id: c.id || null, chip: c }
}

export const ASK_MAX = 400 // characters of a step shown inline; the full text stays in the tooltip
const clip = (s, n = ASK_MAX) => { const t = str(s).replace(/\s+/g, ' ').trim(); return t.length > n ? `${t.slice(0, n - 1).trimEnd()}…` : t }
const clause = (text, word) => {
  const m = new RegExp(`\\b${word}\\s*(?:\\([^)]*\\))?\\s*=\\s*([^;]+?)\\s*(?:;|$)`, 'i').exec(text)
  return m ? m[1].replace(/[.\s]+$/, '') : ''
}

/** What a card that needs the user shows, in plain words: the step to take (with where, as the card states it),
 *  why only the user can do it, and what happens next. Built only from the card's structured `human_request`;
 *  the card title and block reason are agent bookkeeping and are never shown as the ask. Without a request the
 *  row says the step isn't shown here and offers the agent a clarifying question, never an invented action. */
export function askView(c) {
  const r = c?.humanRequest
  const action = typeof r?.action === 'string' ? r.action.trim() : ''
  if (!action) {
    return { known: false, step: 'The exact step isn’t shown here.', full: '', why: '',
      next: 'Open the card to read what it needs, or press Ask agent to have it spell out the step.' }
  }
  // Producers append what each answer does ("Unblock = …; archive = …"): that is the next step, not the ask.
  const cut = action.search(/\b(?:unblock|yes)\s*(?:\([^)]*\))?\s*=/i)
  const step = (cut > 0 ? action.slice(0, cut) : action).trim()
  const tail = cut > 0 ? action.slice(cut) : ''
  const yes = clause(tail, '(?:unblock|yes)'), no = clause(tail, '(?:archive|no)')
  const next = yes || no
    ? `When done, open the card and press Unblock${yes ? ` (${clip(yes, 200)})` : ''}${no ? `, or Archive if you don’t want it (${clip(no, 200)})` : ''}. You can also reply in chat.`
    : 'When done, open the card and press Unblock, or reply in chat. Archive the card if you don’t want it.'
  return { known: true, step: clip(step), full: step, why: typeof r.why === 'string' ? clip(r.why, 300) : '', next }
}

/** Composer draft for Ask agent: a plain question about one card, sent only when the user presses send. */
export const askAgentText = i => `Card ${i.id}: what exactly do you need me to do, and where? Please answer in one or two plain sentences.\n`

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

// One small box per card, in the header beside the status: a full-width bar spent a whole row to say "1 waiting".
// Shape carries state as well as colour: open work is filled, waiting is an outline, not started is dashed.
const PIP_ORDER = ['needsYou', 'stuck', 'running', 'waiting', 'queued', 'done']
export const PIPS_MAX = 24 // boxes drawn before the rest fold into "+N"
// A hub-scheduled card's box is a little larger and carries an "H" (running and waiting work alike).
const pipStyle = (k, hub = false) => {
  const base = { width: 9, height: 9, borderRadius: 2, boxSizing: 'border-box', flex: 'none' }
  const h = hub ? { width: 12, height: 12, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 8.5, fontWeight: 700, lineHeight: 1 } : {}
  if (k === 'waiting') return { ...base, ...h, border: `1.5px solid ${C.waiting}`, color: C.waiting }
  if (k === 'queued') return { ...base, ...h, border: `1.5px dashed ${C.waiting}`, opacity: 0.7, color: C.waiting }
  return { ...base, ...h, background: C[k], color: 'var(--card, #fff)' }
}

/** Per pip state, how many of its open cards are scheduled on the hub (session-tasks `runMachine`). */
export function hubCounts(sorted) {
  const on = list => (list || []).filter(i => i.kind === 'kanban' && i.chip?.runMachine === 'hub').length
  return { running: on(sorted.active), stuck: on(sorted.stuck), waiting: on(sorted.waiting), queued: on(sorted.queued) }
}

export function Pips({ n, hub = {} }) {
  const order = PIP_ORDER.filter(k => n[k])
  if (!order.length) return null
  const all = order.flatMap(k => Array.from({ length: n[k] }, (_, i) => ({ k, hub: i < (hub[k] || 0) })))
  const shown = all.slice(0, PIPS_MAX), more = all.length - shown.length
  const words = order.map(k => `${n[k]} ${LABEL[k]}${hub[k] ? ` (${Math.min(hub[k], n[k])} on hub)` : ''}`)
  return jsxs('span', { 'data-progress-pips': '', role: 'img', 'aria-label': words.join(', '), title: words.join('\n'), style: { display: 'inline-flex', alignItems: 'center', gap: 3, flex: 'none' }, children: [
    ...shown.map((p, idx) => jsx('span', { 'data-pip': p.k, ...(p.hub ? { 'data-pip-hub': '' } : {}), 'aria-hidden': true, style: pipStyle(p.k, p.hub), children: p.hub ? 'H' : undefined }, idx)),
    more > 0 && jsx('span', { 'data-pip-more': '', 'aria-hidden': true, style: { ...muted, marginLeft: 2 }, children: `+${more}` })
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

function AskRow({ i, session }) {
  const st = typeof window !== 'undefined' ? window.__sessionTasks : null
  const canOpen = i.kind === 'kanban' && !!i.chip.connectionId && !!i.chip.profile && typeof st?.openTaskChip === 'function'
  const v = askView(i.chip)
  const ask = () => { void host.composer?.setDraft?.(session, askAgentText(i)); host.composer?.focus?.(session) }
  const line = { fontSize: 12.5, lineHeight: 1.45, overflowWrap: 'anywhere', minWidth: 0 }
  return jsxs('div', { 'data-panel-row': i.key, 'data-ask-row': v.known ? 'known' : 'unknown', style: { border: `1px solid color-mix(in srgb, ${C.needsYou} 45%, transparent)`, borderRadius: 6, padding: '8px 10px', marginBottom: 6, minWidth: 0 }, children: [
    jsx('div', { 'data-ask-step': '', title: v.full || undefined, style: { fontWeight: 600, fontSize: 13, lineHeight: 1.45, overflowWrap: 'anywhere', minWidth: 0 }, children: v.known ? `Do this: ${v.step}` : v.step }),
    v.why && jsx('div', { 'data-ask-why': '', style: { ...line, color: 'var(--muted-foreground)' }, children: `Why you: ${v.why}` }),
    jsx('div', { 'data-ask-next': '', style: line, children: `Next: ${v.next}` }),
    jsxs('div', { style: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginTop: 6 }, children: [
      canOpen && jsx('button', { type: 'button', 'data-open-card': '', style: btn, onClick: () => st.openTaskChip(i.chip), children: 'Open card' }),
      !v.known && jsx('button', { type: 'button', 'data-ask-agent': '', style: btn, title: 'Drafts a question to the agent in the composer; nothing is sent until you send it', onClick: ask, children: 'Ask agent' }),
      jsx('span', { 'data-ask-card': '', title: str(i.chip?.title) || i.label, style: { ...muted, marginLeft: 'auto', fontSize: 11 }, children: i.id ? `card ${i.id}` : '' })
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
  // A legacy card listed above as an unasked row is the same exact qualified card the strip's "Earlier asks"
  // would show again: hand those identities to the strip so each card renders once. Exact identity only.
  const legacy = US?.Strip ? jsx(US.Strip, { hideCards: sorted.unasked.map(i => i.chip) }, 'legacy') : null
  // Finished cards alone are no reason to take space above the composer: an all-green chat shows nothing.
  if (!hasOpenWork(sorted, { asks, steps: US?.snapshot?.ledger?.steps, issues: US?.snapshot?.issues })) return null
  const attention = attentionKeys(asks, sorted)
  const collapsed = !!collapsedKeys && attention.every(k => collapsedKeys.includes(k))
  const toggle = () => { const next = collapsed ? null : attention; writeCollapsed(next); setCollapsedState(next) }
  // `busy` is this chat's own turn (the agent writing a reply), not card work: say so, or "working" next to
  // "2 waiting" reads as a card running when none is.
  const status = busy ? 'Agent replying in this chat' : sorted.lastActivity ? `Idle · last activity ${ago(sorted.lastActivity, now)}` : 'Idle'
  // Collapsed, a bare "2 waiting" left the user asking "on what?": keep one short line per waiting card.
  const waitLines = sorted.waiting.slice(0, WAIT_LINES_COLLAPSED)
  const waitMore = sorted.waiting.length - waitLines.length
  const needsYou = [
    (asks > 0 || sorted.unasked.length > 0) && jsx('h3', { 'data-section': 'needs-you', style: heading(C.needsYou), children: `Needs you · ${asks + sorted.unasked.length}` }, 'h-needs-you'),
    US?.ActionQueue && jsx(US.ActionQueue, { embedded: true }, 'queue'),
    ...sorted.unasked.map(i => jsx(AskRow, { i, session: route.session }, i.key)),
    legacy
  ].filter(Boolean)
  const summaryParts = PIP_ORDER.filter(k => n[k]).map(k => ({ k, text: `${n[k]} ${LABEL[k]}` }))
  // Same chrome as the composer status stack's sections (StatusSection): a full-width disclosure row with a caret,
  // a glyph and a muted label. The caret is the minimize toggle; folded, only this row (and waiting one-liners)
  // stays. A folded ask is never muted: its count keeps the needs-you colour, and a new ask reopens the panel.
  const trigger = 'status-section-trigger flex min-w-0 flex-1 items-center gap-1.5 px-2 py-1 text-left text-xs font-normal text-muted-foreground/92 transition-colors hover:text-foreground/90'
  return jsxs('section', { 'data-session-panel': '', 'data-slot': 'status-section', 'aria-label': 'Session status', style: { width: '100%', minWidth: 0, boxSizing: 'border-box', paddingBottom: collapsed ? 2 : 6, borderBottom: '1px solid color-mix(in srgb, var(--border) 60%, transparent)', color: 'var(--foreground)', userSelect: 'text', WebkitUserSelect: 'text' }, children: [
    jsx('div', { className: 'status-section-header flex items-center gap-1 pr-1', children: jsxs('button', { type: 'button', 'data-panel-toggle': '', className: trigger, 'aria-expanded': !collapsed, 'aria-label': collapsed ? 'Expand session status' : 'Collapse session status', title: collapsed ? 'Expand' : 'Collapse (reopens when something new needs you or gets stuck)', onClick: toggle, style: { background: 'transparent', border: 0, cursor: 'pointer', minWidth: 0 }, children: [
      jsx(DisclosureCaret, { className: 'shrink-0', open: !collapsed, size: '1em' }),
      jsx('span', { className: 'status-section-icon flex shrink-0 items-center', children: jsx(Codicon, { className: 'text-muted-foreground/70', name: 'pulse', size: '0.8rem' }) }),
      jsx('span', { 'data-panel-title': '', className: 'shrink-0', children: 'This session' }),
      jsxs('span', { 'data-session-status': '', className: 'min-w-0 truncate', style: { display: 'flex', alignItems: 'center', gap: 5, opacity: 0.8 }, children: [busy && jsx('span', { 'aria-hidden': true, style: { color: C.running }, children: '●' }), status] }),
      jsx(Pips, { n, hub: hubCounts(sorted) }),
      summaryParts.length > 0 && jsx('span', { 'data-status-summary': '', ...(collapsed ? { 'data-collapsed-summary': '' } : {}), className: 'min-w-0 truncate', children: summaryParts.flatMap((p, idx) => [idx > 0 && ' · ', jsx('span', { style: p.k === 'needsYou' || p.k === 'stuck' ? { color: C[p.k], fontWeight: 600 } : undefined, children: p.text }, p.k)].filter(Boolean)) })
    ] }) }),
    // Collapsed, a bare "2 waiting" left the user asking "on what?": keep one short line per waiting card.
    collapsed && waitLines.length > 0 && jsxs('div', { 'data-collapsed-waiting': '', style: { ...muted, padding: '0 8px 0 30px', minWidth: 0 }, children: [
      ...waitLines.map(i => jsx('div', { 'data-waiting-line': i.key, title: `${str(i.chip?.title) || i.label}${i.detail ? `\n${i.detail}` : ''}`, style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: `${i.label} · ${i.detail || 'waiting'}` }, i.key)),
      waitMore > 0 && jsx('div', { children: `+${waitMore} more waiting` }, 'more')
    ] }),
    !collapsed && jsxs('div', { 'data-panel-body': '', className: 'status-section-body', style: { maxHeight: '55vh', overflowY: 'auto', minWidth: 0, padding: '0 10px 0 12px' }, children: [
      ...needsYou,
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

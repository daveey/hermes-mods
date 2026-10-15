/**
 * Session Tasks — a right-aligned strip of compact chips above the composer input,
 * one per thing the focused session is running right now:
 *
 *   ⟳ bg   background processes   (gateway RPC `process.list`, status != exited)
 *   ⟳ sub  delegate_task subagents (gateway RPC `subagent.list`, non-terminal status)
 *   ⟳ t_…  kanban tasks this session created or shepherds (tui subscription),
 *          across every board, in todo / ready / running / scheduled / blocked / review
 *          — the same cards the sidebar row's kanban counts cover
 *          (`/api/session-kanban-tasks?ids=<lineage>`; older backends: a scan of the
 *          current `/api/plugins/kanban/board` for cards the lineage created)
 *
 * Hidden when nothing is running. Polls every 5 s while the window is visible.
 * Clicking a kanban chip opens the Task Mentions popup when that plugin is
 * loaded, else the Kanban page. Hover any chip for the full title.
 */
import { COMPOSER_AREAS, host, useValue } from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'session-tasks'
const POLL_MS = 5_000
const KANBAN_POLL_MS = 10_000
const LINEAGE_TTL_MS = 5 * 60_000
const MAX_LABEL = 38
const KANBAN_ACTIVE = new Set(['todo', 'ready', 'running', 'scheduled', 'blocked', 'review'])
const SUBAGENT_DONE = new Set(['completed', 'failed', 'interrupted', 'cancelled', 'timeout', 'error'])

const STATE_COLOR = {
  running: 'var(--accent, #3b82f6)',
  ready: '#8b949e',
  todo: '#8b949e',
  scheduled: '#8b949e',
  blocked: '#f85149',
  review: '#db61a2'
}

const str = v => (typeof v === 'string' ? v : v == null ? '' : String(v))
const truncate = (s, n = MAX_LABEL) => (s.length > n ? `${s.slice(0, n - 1)}…` : s)
const log = (...a) => console.info(`[${ID}]`, ...a)

export function elapsed(sinceSec, nowMs = Date.now()) {
  if (!sinceSec) return ''
  const s = Math.max(0, Math.floor(nowMs / 1000 - sinceSec))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}`
  return `${Math.floor(s / 86400)}d`
}

// ── pure projections (exported for tests) ───────────────────────────────────

export function processChips(processes) {
  return (Array.isArray(processes) ? processes : [])
    .filter(p => p && str(p.status) !== 'exited')
    .map(p => {
      const cmd = str(p.command).split('\n')[0].trim() || 'background process'
      const up = Number(p.uptime_seconds)
      const since = up >= 0 && Number.isFinite(up) && p.uptime_seconds != null
        ? Date.now() / 1000 - up
        : Number(p.started_at) || (Date.parse(str(p.started_at)) || 0) / 1000
      return { key: `bg:${str(p.session_id) || cmd}`, kind: 'bg', state: 'running', label: cmd, title: str(p.command) || cmd, since }
    })
}

export function subagentChips(subagents) {
  return (Array.isArray(subagents) ? subagents : [])
    .filter(s => s && !SUBAGENT_DONE.has(str(s.status).toLowerCase()))
    .map(s => {
      const goal = str(s.goal).split('\n')[0].trim() || 'subagent'
      return {
        key: `sub:${str(s.subagent_id) || `${str(s.delegation_id)}:${str(s.task_index)}:${goal}`}`,
        kind: 'sub',
        state: 'running',
        label: goal,
        title: str(s.goal) || goal,
        since: Number(s.started_at) || 0
      }
    })
}

const KANBAN_RANK = { running: 0, blocked: 1, review: 2, ready: 3, scheduled: 4, todo: 5 }

function taskChip(t, status) {
  const watching = t.via === 'subscribed'
  return {
    key: `kb:${t.id}`,
    kind: 'kanban',
    id: str(t.id),
    state: status,
    label: str(t.title) || t.id,
    title: `${t.id} · ${status}${t.assignee ? ` · ${t.assignee}` : ''}${watching ? ' · shepherding (created by another chat)' : ''}${t.board && t.board !== 'default' ? ` · board ${t.board}` : ''}\n${str(t.title)}`,
    since: Number(t.current_run_started_at || t.started_at || t.created_at) || 0
  }
}

const byRank = chips => chips.sort((a, b) => (KANBAN_RANK[a.state] ?? 9) - (KANBAN_RANK[b.state] ?? 9))

/** `/api/session-kanban-tasks` rows (already filtered to the lineage by the backend). */
export function sessionTaskChips(tasks) {
  const seen = new Set()
  const out = []
  for (const t of Array.isArray(tasks) ? tasks : []) {
    const status = str(t?.status)
    if (!t?.id || !KANBAN_ACTIVE.has(status) || seen.has(t.id)) continue
    seen.add(t.id)
    out.push(taskChip(t, status))
  }
  return byRank(out)
}

/** Fallback for backends without that route: scan the current board. */
export function kanbanChips(board, sessionIds) {
  const ids = sessionIds instanceof Set ? sessionIds : new Set(sessionIds || [])
  const out = []
  for (const col of board?.columns || []) {
    for (const t of col?.tasks || []) {
      const status = str(t?.status || col?.name)
      if (!KANBAN_ACTIVE.has(status) || !ids.has(str(t?.session_id))) continue
      out.push(taskChip(t, status))
    }
  }
  return byRank(out)
}

// ── data ────────────────────────────────────────────────────────────────────

function activeConnection() {
  try {
    return host.activeConnectionId?.() || host.state?.connectionId?.get?.() || undefined
  } catch {
    return undefined
  }
}

function api(path) {
  const bridge = typeof window !== 'undefined' ? window.hermesDesktop?.api : null
  if (!bridge) return Promise.reject(new Error('Hermes desktop bridge unavailable'))
  const connectionId = activeConnection()
  return bridge({ path, ...(connectionId ? { connectionId } : {}), timeoutMs: 15_000 })
}

// stored id → { at, ids:Set } — the session plus its parent chain (compression
// rotates the stored id; kanban tasks keep the id they were created under).
const lineageCache = new Map()

async function lineageIds(storedId) {
  const hit = lineageCache.get(storedId)
  if (hit && Date.now() - hit.at < LINEAGE_TTL_MS) return hit.ids
  const ids = new Set([storedId])
  let cur = storedId
  for (let hop = 0; hop < 12 && cur; hop++) {
    try {
      const row = await api(`/api/sessions/${encodeURIComponent(cur)}`)
      if (row?.id) ids.add(str(row.id))
      cur = str(row?.parent_session_id)
      if (cur) ids.add(cur)
    } catch {
      break
    }
  }
  lineageCache.set(storedId, { at: Date.now(), ids })
  return ids
}

async function safe(fn, fallback) {
  try {
    return await fn()
  } catch {
    return fallback
  }
}

// One shared poller for every mounted strip (tiles mount one composer each).
let snapshot = { runtime: null, stored: null, chips: [] }
const subs = new Set()
let timer = null
let lastKanbanAt = 0
let kanbanCache = []
let running = false
let rerun = false

const emit = () => subs.forEach(fn => fn(snapshot))

async function loadKanban(ids) {
  const list = await safe(
    () => api(`/api/session-kanban-tasks?ids=${encodeURIComponent([...ids].join(','))}`),
    null
  )
  if (list && Array.isArray(list.tasks)) return sessionTaskChips(list.tasks)
  const board = await safe(() => api('/api/plugins/kanban/board'), null)
  return board ? kanbanChips(board, ids) : null
}

async function refresh() {
  // A focus change during an in-flight round must not be dropped (that left the
  // strip empty for a whole poll interval after opening a chat): run again after.
  if (running) {
    rerun = true
    return
  }
  if (typeof document !== 'undefined' && document.hidden) return
  running = true
  try {
    const runtime = host.state.focusedSessionId.get()
    const stored = host.state.focusedStoredSessionId.get()
    if (!runtime && !stored) {
      snapshot = { runtime, stored, chips: [] }
      return emit()
    }
    const sameSession = stored === snapshot.stored
    const [procs, subagents] = await Promise.all([
      runtime ? safe(() => host.request('process.list', { session_id: runtime }), null) : null,
      runtime ? safe(() => host.request('subagent.list', { session_id: runtime }), null) : null
    ])
    if (stored && (!sameSession || Date.now() - lastKanbanAt >= KANBAN_POLL_MS)) {
      lastKanbanAt = Date.now()
      const ids = await lineageIds(stored)
      const chips = await loadKanban(ids)
      if (chips) kanbanCache = chips
      else if (!sameSession) kanbanCache = [] // never show the previous chat's cards
    } else if (!stored) {
      kanbanCache = []
    }
    // Focus moved while we were fetching: drop this round, the next tick reloads.
    if (host.state.focusedSessionId.get() !== runtime) return
    snapshot = {
      runtime,
      stored,
      chips: [...kanbanCache, ...subagentChips(subagents?.subagents), ...processChips(procs?.processes)]
    }
    emit()
  } catch (e) {
    log('refresh failed', e)
  } finally {
    running = false
    if (rerun) {
      rerun = false
      void refresh()
    }
  }
}

function subscribe(fn) {
  subs.add(fn)
  if (!timer) {
    timer = setInterval(refresh, POLL_MS)
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

// ── UI ──────────────────────────────────────────────────────────────────────

const KIND_GLYPH = { bg: 'codicon-server-process', sub: 'codicon-agent', kanban: 'codicon-tasklist' }

function Chip({ chip, now }) {
  const color = STATE_COLOR[chip.state] || STATE_COLOR.running
  const clickable = chip.kind === 'kanban'
  const onClick = clickable
    ? () => {
        if (typeof window.__taskMentionsOpen === 'function') window.__taskMentionsOpen(chip.id)
        else host.navigate?.('/kanban')
      }
    : undefined
  const age = elapsed(chip.since, now)
  return jsxs(clickable ? 'button' : 'span', {
    type: clickable ? 'button' : undefined,
    onClick,
    title: chip.title,
    'data-session-task': chip.kind,
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      gap: 5,
      maxWidth: 280,
      padding: '1px 8px',
      borderRadius: 999,
      border: '1px solid color-mix(in srgb, var(--border, #8884) 80%, transparent)',
      background: 'color-mix(in srgb, var(--card, transparent) 60%, transparent)',
      color: 'var(--muted-foreground, inherit)',
      fontSize: 11,
      lineHeight: '18px',
      whiteSpace: 'nowrap',
      cursor: clickable ? 'pointer' : 'default',
      fontFamily: 'inherit'
    },
    children: [
      jsx('span', {
        'aria-hidden': true,
        className: chip.state === 'running' ? 'session-tasks-pulse' : undefined,
        style: { width: 6, height: 6, borderRadius: 999, background: color, flex: 'none' }
      }),
      jsx('i', { 'aria-hidden': true, className: `codicon ${KIND_GLYPH[chip.kind]}`, style: { fontSize: 12, flex: 'none', opacity: 0.8 } }),
      chip.kind === 'kanban' && jsx('span', { style: { opacity: 0.7, fontFamily: 'var(--font-mono, ui-monospace, monospace)', fontSize: 10.5 }, children: chip.id }),
      jsx('span', { style: { overflow: 'hidden', textOverflow: 'ellipsis', fontSize: 11 }, children: truncate(chip.label) }),
      chip.state !== 'running' && jsx('span', { style: { color, fontSize: 10.5 }, children: chip.state }),
      age && jsx('span', { style: { opacity: 0.6, fontSize: 10.5, fontVariantNumeric: 'tabular-nums' }, children: age })
    ]
  })
}

function Strip() {
  const [snap, setSnap] = useState(snapshot)
  const [now, setNow] = useState(Date.now())
  const focused = useValue(host.state.focusedSessionId)
  useEffect(() => subscribe(next => (setSnap(next), setNow(Date.now()))), [])
  useEffect(() => void refresh(), [focused])
  const chips = snap.runtime === focused ? snap.chips : []
  if (!chips.length) return null
  return jsx('div', {
    'data-slot': 'session-tasks',
    style: { display: 'flex', flexWrap: 'wrap', justifyContent: 'flex-end', gap: 4, width: '100%', marginBottom: 2 },
    children: chips.map(chip => jsx(Chip, { chip, now }, chip.key))
  })
}

const STYLE = `@keyframes session-tasks-pulse{0%,100%{opacity:1}50%{opacity:.35}}
.session-tasks-pulse{animation:session-tasks-pulse 1.6s ease-in-out infinite}
@media (prefers-reduced-motion: reduce){.session-tasks-pulse{animation:none}}`

export default {
  id: ID,
  name: 'Session Tasks',
  register(ctx) {
    if (typeof document !== 'undefined' && !document.getElementById(`${ID}-style`)) {
      const el = document.createElement('style')
      el.id = `${ID}-style`
      el.textContent = STYLE
      document.head.appendChild(el)
    }
    ctx.register({ id: 'strip', area: COMPOSER_AREAS.top, order: -10, render: () => jsx(Strip, {}) })
    const onVis = () => document.hidden || void refresh()
    document.addEventListener('visibilitychange', onVis)
    window.__sessionTasks = { get snapshot() { return snapshot }, refresh }
    return () => {
      document.removeEventListener('visibilitychange', onVis)
      document.getElementById(`${ID}-style`)?.remove()
      delete window.__sessionTasks
      if (timer) clearInterval(timer)
      timer = null
    }
  }
}

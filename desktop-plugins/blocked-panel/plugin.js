/**
 * Blocked Panel — when the focused session is blocked, an amber panel above the
 * composer input says exactly what YOU need to do to unblock it.
 *
 * A session is blocked when:
 *   - one of its Kanban cards (created or shepherded by any id in its compression
 *     lineage, any board) sits in `blocked` waiting on a human. Each card shows the
 *     explicit human_request.action from the latest `blocked` event as a NUMBERED
 *     checklist. Source order: the `blocked-panel` agent plugin's model rewrite
 *     (POST /api/plugins/blocked-panel/steps, disk-cached) → the note's own numbering →
 *     one step per sentence. The list always ends with how to hand the card back; the raw
 *     note stays one click away ("Agent's note"). Buttons: Open card · Done → unblock
 *     · Reply & unblock (posts your note as a card comment, then unblocks, so the
 *     resumed worker reads it).
 *     Only complete human_request evidence is actionable. Metadata is presentation,
 *     not permission or completion. Typed dependency/time waits are not human asks.
 *   - Operational failures, transient pauses, missing details and unclassified blockers
 *     are agent issues, not asks: snapshot.issues, owner, "No action required from you", Open card only.
 *   - you parked it with ⌘B (core's `hermes.desktop.blockedSessionIds`). With no
 *     blocked cards that shows one line: parked by you, ⌘B to unblock.
 *
 * Data: `/api/session-kanban-tasks?ids=<lineage>` (every board) + per blocked card
 * `/api/plugins/kanban/tasks/<id>?board=<slug>` (events → reason/kind/human_request). Polls every
 * 10 s while visible; card details are cached 60 s or until the blocked set changes.
 * Only the FOCUSED session is known to a composer plugin, so every tile's composer
 * renders the focused chat's panel only when that tile is the focused one.
 *
 * Retired as a composer panel when user-steps is loaded (window.__userSteps.ownsComposer): its "For you"
 * strip shows these cards via window.__blockedPanel.{snapshot,subscribe,unblockCard,openCard,checklist}.
 */
import { COMPOSER_AREAS, host, useValue } from '@hermes/plugin-sdk'
import * as sdk from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'blocked-panel'
const POLL_MS = 10_000
const DETAIL_TTL_MS = 60_000
const LINEAGE_TTL_MS = 5 * 60_000
const PARKED_KEY = 'hermes.desktop.blockedSessionIds'
const COLLAPSED_LINES = 4

const str = v => (typeof v === 'string' ? v : v == null ? '' : String(v))
// Some cards store literal "\u2014" escapes in their title/reason; show the character.
export const unescapeText = v => str(v).replace(/\\u([0-9a-fA-F]{4})/g, (_, h) => String.fromCharCode(parseInt(h, 16)))
const log = (...a) => console.info(`[${ID}]`, ...a)

// ── pure helpers (exported for tests) ───────────────────────────────────────

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

/** core's persisted ⌘B list (JSON string array) → string[]; junk → []. */
export function parseParkedIds(raw) {
  try {
    const parsed = JSON.parse(str(raw) || '[]')
    return Array.isArray(parsed) ? parsed.filter(x => typeof x === 'string' && x) : []
  } catch {
    return []
  }
}

export function isParked(lineage, parkedIds) {
  const parked = new Set(parkedIds || [])
  for (const id of lineage || []) if (parked.has(id)) return true
  return false
}

const payloadOf = e => {
  const p = e?.payload
  if (p && typeof p === 'object') return p
  try {
    return JSON.parse(str(p) || '{}') || {}
  } catch {
    return {}
  }
}

/** Latest blocked event after unblock. initial_status itself is not instructions;
 *  any explicit human_request is propagated independently for validation. */
export function latestBlock(events) {
  const list = (Array.isArray(events) ? events : []).slice().sort((a, b) => (Number(a?.id) || 0) - (Number(b?.id) || 0))
  let block = null
  for (const e of list) {
    if (e?.kind === 'unblocked') block = null
    else if (['blocked', 'block_loop_detected'].includes(e?.kind)) {
      const p = payloadOf(e)
      const reason = str(p.reason).trim()
      block = {
        reason: reason === 'initial_status' ? '' : reason,
        kind: str(p.kind) || null,
        human_request: p.human_request,
        at: Number(e.created_at) || 0
      }
    }
  }
  return block
}


/** Classification is not authorization. Only a complete, explicit interaction is a human ask. */
export function needsHuman(block) {
  if (!block || ['transient', 'dependency', 'time'].includes(block.kind)) return false
  const request = block.human_request
  if (!request || typeof request !== 'object' || Array.isArray(request)) return false
  if (!['approval', 'reply', 'user_terminal', 'physical', 'external'].includes(request.interaction)) return false
  return ['action', 'why_human', 'scope', 'evidence'].every(key => typeof request[key] === 'string' && request[key].trim())
}

const FAILURE_KINDS = new Set(['gave_up', 'crashed', 'timed_out', 'spawn_failed'])
const REFUSAL_RE = /safety refusal|declined to respond|safety filter/i

const ANSI_RE = /\u001b\[[0-9;?]*[ -/]*[@-~]|\u001b\][^\u0007]*\u0007|[\u0000-\u0008\u000b-\u001f\u007f]/g
const oneLine = (v, max = 220) => {
  const t = unescapeText(v).replace(ANSI_RE, ' ').replace(/\s+/g, ' ').trim()
  return t.length > max ? `${t.slice(0, max - 1)}\u2026` : t
}

/** Plain, fixed-wording cause of a dispatcher/worker failure event. Built only from structured fields
 *  (kind, attempt count, exit code); the raw `error` / worker transcript is never shown (it can hold
 *  paths, tokens and terminal escapes). A model refusal is the one cause recognised from the text. */
export function failureSummary(kind, payload) {
  const p = payload && typeof payload === 'object' ? payload : {}
  const raw = `${str(p.error)} ${str(p.worker_output)} ${str(p.reason)}`
  if (REFUSAL_RE.test(raw)) return 'The model declined to respond (safety refusal).'
  const code = p.exit_code != null && p.exit_code !== '' ? ` (exit code ${oneLine(str(p.exit_code), 12)})` : ''
  const n = Number(p.failures) > 0 ? ` after ${Number(p.failures)} failed attempt${Number(p.failures) === 1 ? '' : 's'}` : ''
  switch (kind) {
    case 'gave_up':
      return `The dispatcher gave up${n}${code}.`
    case 'timed_out':
      return `The worker timed out${code}.`
    case 'spawn_failed':
      return `The worker could not be started.`
    default:
      return `The worker crashed${code}.`
  }
}

/** Typed dependency/time waits are benign; unknown asks stay visible and agent-owned. */
function pauseFailure(block) {
  if (['dependency', 'time'].includes(block.kind)) return null
  if (block.kind !== 'transient') return { kind: 'unclassified', reason: 'No actionable human request was recorded. The assigned agent must classify this blocker.', refusal: false, at: block.at }
  const reason = oneLine(block.reason, 300)
  return { kind: 'paused', reason: reason || 'Paused automatically; no reason was recorded.', refusal: /refus|safety filter|declined/i.test(reason), at: block.at }
}

/** Latest signal since unblock wins. Only explicit, complete human_request metadata
 *  produces a human item; initial status, prose and legacy missing events do not.
 *  This projection never changes execution permission, task status or ownership. */
export function blockState(events) {
  const list = (Array.isArray(events) ? events : []).slice().sort((a, b) => (Number(a?.id) || 0) - (Number(b?.id) || 0))
  let state = { origin: 'unknown', block: null, failure: null }
  for (const e of list) {
    if (e?.kind === 'unblocked') state = { origin: 'unknown', block: null, failure: null }
    else if (['blocked', 'block_loop_detected'].includes(e?.kind)) {
      const p = payloadOf(e)
      const reason = str(p.reason).trim()
      const initial = reason === 'initial_status'
      const block = { reason: initial ? '' : reason, kind: str(p.kind) || null, human_request: p.human_request, at: Number(e.created_at) || 0, source: initial ? 'initial' : reason ? 'recorded' : 'unrecorded' }
      if (needsHuman(block)) state = { origin: 'human', failure: null, block }
      else state = { origin: 'agent', block: null, failure: pauseFailure(block) }
    } else if (FAILURE_KINDS.has(e?.kind)) {
      const p = payloadOf(e)
      state = {
        origin: 'agent',
        block: null,
        failure: { kind: e.kind, reason: failureSummary(e.kind, p), refusal: REFUSAL_RE.test(`${str(p.error)} ${str(p.worker_output)}`), at: Number(e.created_at) || 0 }
      }
    }
  }
  return state
}

/** Split instructions into { intro, steps[] }. Steps come from line-leading
 *  markers ("1) ", "2. ", "- ") or, on one line, from inline "1) … 2) …" runs
 *  that count up from 0 or 1. Anything else → intro only. */
export function stepsOf(text) {
  const t = str(text).replace(/\r\n/g, '\n').trim()
  if (!t) return { intro: '', steps: [], start: 1 }
  const lines = t.split('\n').map(l => l.trim()).filter(Boolean)
  const marker = /^(?:\d{1,2}[).]|[-*•])\s+/
  if (lines.filter(l => marker.test(l)).length >= 2) {
    const intro = []
    const steps = []
    for (const l of lines) {
      if (marker.test(l)) steps.push(l.replace(marker, ''))
      else if (steps.length) steps[steps.length - 1] += ` ${l}`
      else intro.push(l)
    }
    return { intro: intro.join(' '), steps, start: 1 }
  }
  const flat = lines.join(' ')
  const re = /(?:^|[\s:;.])(\d{1,2})\)\s+/g
  const hits = []
  let m
  while ((m = re.exec(flat))) hits.push({ n: Number(m[1]), start: m.index + m[0].length, at: m.index + (m[0].length - m[0].trimStart().length) })
  // keep the first run that counts up by one from 0 or 1
  const run = []
  for (const h of hits) {
    if (!run.length) {
      if (h.n === 0 || h.n === 1) run.push(h)
    } else if (h.n === run[run.length - 1].n + 1) run.push(h)
  }
  if (run.length < 2) return { intro: flat, steps: [], start: 1 }
  const intro = flat.slice(0, run[0].at).trim().replace(/[:;,]\s*$/, ':')
  const steps = run.map((h, i) => flat.slice(h.start, i + 1 < run.length ? run[i + 1].at : undefined).trim().replace(/[;,]$/, ''))
  return { intro, steps, start: run[0].n }
}

const KIND_LABEL = { needs_input: 'Needs your input', capability: 'Needs access you have' }

// Modern owners expose canonical evidence on the task. Explicit null retires
// old event evidence; events remain a compatibility fallback for older owners.
function detailState(d) {
  if (!d) return { origin: 'unknown', block: null, failure: null }
  if (Object.prototype.hasOwnProperty.call(d.task || {}, 'human_request')) {
    const block = { kind: d.task.block_kind, human_request: d.task.human_request, at: 0, source: 'recorded' }
    return needsHuman(block) ? { origin: 'human', block, failure: null }
      : { origin: 'agent', block: null, failure: pauseFailure(block) }
  }
  return blockState(d.events)
}

/** Only validated requests enter the human strip. Missing details remain operational
 *  in agentIssues(); task descriptions are never inferred to be instructions. */
export function blockedItems(tasks, details) {
  const out = []
  const seen = new Set()
  for (const t of Array.isArray(tasks) ? tasks : []) {
    if (!t?.id || !['blocked', 'triage'].includes(str(t.status)) || seen.has(detailKey(t))) continue
    seen.add(detailKey(t))
    const d = details?.get ? details.get(detailKey(t)) : details?.[detailKey(t)]
    const st = detailState(d)
    if (st.origin === 'agent') continue
    const block = st.block
    if (!needsHuman(block)) continue
    const fromBody = false
    const text = unescapeText(block.human_request.action)
    out.push({
      id: str(t.id),
      connectionId: t.connectionId,
      gateway: t.gateway,
      board: str(t.board) || 'default',
      title: unescapeText(t.title) || str(t.id),
      kind: block?.kind || null,
      human_request: { ...block.human_request },
      kindLabel: KIND_LABEL[block?.kind] || 'Waiting on you',
      text,
      fromBody,
      // Source is provenance only: instructions always come from human_request.action.
      reasonSource: !d ? 'loading' : block?.source || 'legacy',
      loaded: Boolean(d),
      at: block?.at || 0,
      ...stepsOf(text)
    })
  }
  return out.sort((a, b) => b.at - a.at)
}

/** Operational failures and unclassified blockers stay visible with their owner,
 *  never as something you must do. Classification does not unblock them. */
export function agentIssues(tasks, details) {
  const out = []
  const seen = new Set()
  for (const t of Array.isArray(tasks) ? tasks : []) {
    if (!t?.id || !['blocked', 'triage'].includes(str(t.status)) || seen.has(detailKey(t))) continue
    seen.add(detailKey(t))
    const d = details?.get ? details.get(detailKey(t)) : details?.[detailKey(t)]
    const state = detailState(d)
    const f = state.origin === 'unknown' ? pauseFailure({ kind: null, at: 0 }) : state.failure
    if (!f) continue
    out.push({
      id: str(t.id),
      connectionId: t.connectionId,
      gateway: t.gateway,
      board: str(t.board) || 'default',
      title: unescapeText(t.title) || str(t.id),
      failureKind: f.kind,
      owner: str(d?.task?.assignee || t.assignee) || 'unassigned',
      refusal: f.refusal,
      reason: f.reason,
      noAction: true,
      at: f.at
    })
  }
  return out.sort((a, b) => b.at - a.at)
}

/** One-line headline for a validated request; retain the legacy fallback defensively. */
export function askSummary(item, rewritten) {
  if (!item?.loaded) return 'Loading what this card needs\u2026'
  if (item.fromBody && item.reasonSource !== 'initial') return 'No block reason was recorded. Open the card to see what it needs.'
  return checklist(item, rewritten).steps[0]
}

export const detailKey = t => `${t?.connectionId || t?.gateway ? `${str(t.connectionId || t.gateway)}:` : ''}${str(t?.board) || 'default'}:${str(t?.id)}`

export const HANDBACK = 'When you\u2019re done, click Done \u2192 unblock (or Reply & unblock to send the agent an answer).'
const HANDBACK_RE = /\bunblock\b/i

/** Prose → one step per sentence (fallback when the note isn't enumerated and no rewrite exists). */
export function sentenceSteps(text) {
  const t = str(text).replace(/\s+/g, ' ').trim()
  if (!t) return []
  return t
    .split(/(?<=[.!?;])\s+(?=[A-Z0-9"'\u201c(])/)
    .map(s => s.trim().replace(/;$/, '.'))
    .filter(s => s.length > 1)
}

/** The numbered checklist the panel shows: model rewrite → the note's own numbering → one step per
 *  sentence. Always ends with how to hand the card back. */
export function checklist(item, rewritten) {
  let steps
  let start = 1
  let intro = ''
  let source
  if (Array.isArray(rewritten) && rewritten.length) {
    steps = rewritten.slice()
    source = 'rewritten'
  } else if (item?.steps?.length >= 2) {
    steps = item.steps.slice()
    start = item.start ?? 1
    intro = item.intro || ''
    source = 'parsed'
  } else {
    steps = sentenceSteps(item?.text)
    source = 'sentences'
  }
  if (!steps.length) steps = ['Open the card to see what it needs.']
  if (!HANDBACK_RE.test(steps[steps.length - 1])) steps.push(HANDBACK)
  return { steps, start, intro, source }
}

export const PARKED_STEPS = ['Do what you parked this chat for (see its last messages above).', 'Press \u2318B to unblock it.']

export const rewriteKey = item => `${detailKey(item)}|${item.text.length}|${item.text.slice(0, 64)}|${item.text.slice(-64)}`

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
  return bridge({ path, timeoutMs: 15_000, ...opts, ...(connectionId ? { connectionId } : {}) })
}

const boardQs = board => (board && board !== 'default' ? `?board=${encodeURIComponent(board)}` : '')
const taskPath = (id, board, sub = '') => `/api/plugins/kanban/tasks/${encodeURIComponent(id)}${sub}${boardQs(board)}`

/** Live lists on every registered gateway, plus hub session links for cross-host
 * subscriptions and offline owners. Replica rows discover cards; only the owner
 * detail response makes them actionable. Never guess a connection from a name. */
export async function sessionTasks(ids, call = api, registry = () => host.connections?.(), resolve = name => sdk.connectionForHubGateway?.(name)) {
  const active = activeConnection() || 'local'
  let connections = []
  try { connections = await registry() || [] } catch { /* active still works */ }
  const owners = [...new Set([active, ...connections.map(c => c.id).filter(Boolean)])]
  const query = encodeURIComponent([...ids].join(','))
  const rows = new Map()
  let answered = false
  const live = await Promise.all(owners.map(async connectionId => {
    try {
      await sdk.learnHubGatewayName?.(connectionId)
      const r = await call(`/api/session-kanban-tasks?ids=${query}`, { connectionId })
      if (!Array.isArray(r?.tasks)) return []
      answered = true
      return r.tasks.map(t => ({ ...t, connectionId }))
    } catch { return [] }
  }))
  try {
    const r = await call(`/api/plugins/hub-sync/session-links/${encodeURIComponent([...ids][0] || '')}?ids=${query}`, { connectionId: active })
    if (Array.isArray(r?.tasks)) {
      answered = true
      for (const t of r.tasks) {
        const connectionId = t.local ? active : resolve(t.gateway)
        const row = { ...t, connectionId }
        rows.set(detailKey(row), row)
      }
    }
  } catch { /* hub plugin is optional */ }
  // Live records win over replicas, including cards which have since unblocked.
  for (const t of live.flat()) rows.set(detailKey(t), t)
  return answered ? [...rows.values()] : null
}

/** Done → unblock, optionally posting `note` first. `call` is injectable for tests. */
export async function unblockCard(item, note = '', call = api) {
  if (item.gateway && !item.connectionId) throw new Error('The owning gateway is unavailable; reconnect before unblocking.')
  const route = item.connectionId ? { connectionId: item.connectionId } : {}
  const body = str(note).trim()
  if (body) {
    await call(taskPath(item.id, item.board, '/comments'), {
      ...route,
      method: 'POST',
      body: { body, author: 'user' }
    })
  }
  return call(taskPath(item.id, item.board), { ...route, method: 'PATCH', body: { status: 'ready' } })
}

/** Open the card's drawer: core hook → task-mentions popup → Kanban page. */
export function openCard(item, win = typeof window !== 'undefined' ? window : {}, nav = p => host.navigate?.(p)) {
  if (item.gateway && !item.connectionId) { notify('error', 'The owning gateway is unavailable.'); return 'unavailable' }
  if (typeof win.__hermesKanbanOpenTask === 'function' && win.__hermesKanbanOpenTask(item.id, item.board, item.connectionId)) return 'drawer'
  // Legacy popup/page hooks cannot route to a different owner.
  if (item.connectionId && item.connectionId !== activeConnection()) return 'unavailable'
  if (typeof win.__taskMentionsOpen === 'function') {
    win.__taskMentionsOpen(item.id)
    return 'popup'
  }
  nav('/kanban')
  return 'page'
}

const lineageCache = new Map()

async function lineageIds(storedId) {
  const owner = host.state.focusedSessionOwner?.get?.()
  if (host.state.focusedSessionOwner && !owner) return new Set([storedId])
  const conn = owner?.connectionId || activeConnection() || ''
  const profile = owner?.profile || ''
  const key = `${conn}\u0000${profile}\u0000${storedId}`
  const hit = lineageCache.get(key)
  if (hit && Date.now() - hit.at < LINEAGE_TTL_MS) return hit.ids
  const ids = new Set([storedId])
  let cur = storedId
  for (let hop = 0; hop < 12 && cur; hop++) {
    try {
      const row = await api(`/api/sessions/${encodeURIComponent(cur)}${profile ? `?profile=${encodeURIComponent(profile)}` : ''}`, { connectionId: conn, timeoutMs: 5_000 })
      if (row?.id) ids.add(str(row.id))
      cur = str(row?.parent_session_id)
      if (cur) ids.add(cur)
    } catch {
      break
    }
  }
  lineageCache.set(key, { at: Date.now(), ids })
  return ids
}

function readParked() {
  try {
    return parseParkedIds(window.localStorage.getItem(PARKED_KEY))
  } catch {
    return []
  }
}

// One shared poller for every mounted composer.
let snapshot = { stored: null, items: [], issues: [], parked: false }
const subs = new Set()
const details = new Map() // detailKey → { at, data }
let timer = null
let running = false
let rerun = false
const rewrites = new Map() // rewriteKey → { status: 'pending'|'done'|'failed', steps, at }
let backend = { ok: null, at: 0 } // blocked-panel agent plugin present?
const REWRITE_RETRY_MS = 5 * 60_000

const withRewrites = items =>
  items.map(it => {
    const r = rewrites.get(rewriteKey(it))
    return { ...it, rewritten: r?.status === 'done' ? r.steps : null, rewriting: r?.status === 'pending' }
  })

async function backendReady() {
  if (backend.ok !== null && Date.now() - backend.at < REWRITE_RETRY_MS) return backend.ok
  try {
    const r = await api('/api/plugins/blocked-panel/ping', { timeoutMs: 5_000 })
    backend = { ok: Boolean(r?.ok), at: Date.now() }
  } catch {
    backend = { ok: false, at: Date.now() }
  }
  return backend.ok
}

/** Ask the backend (aux model, disk-cached) to rewrite each card's note as steps; re-emit as they land. */
async function requestRewrites(items, detailMap) {
  const todo = items.filter(it => {
    if (!it.loaded || !it.text) return false
    const r = rewrites.get(rewriteKey(it))
    return !r || (r.status === 'failed' && Date.now() - r.at > REWRITE_RETRY_MS)
  })
  if (!todo.length || !(await backendReady())) return
  for (const it of todo) rewrites.set(rewriteKey(it), { status: 'pending', at: Date.now() })
  snapshot = { ...snapshot, items: withRewrites(snapshot.items) }
  emit()
  for (const it of todo) {
    const k = rewriteKey(it)
    const d = detailMap.get(detailKey(it))
    const comments = (Array.isArray(d?.comments) ? d.comments : []).slice(-3).map(c => str(c?.body)).filter(Boolean)
    api('/api/plugins/blocked-panel/steps', {
      connectionId: it.connectionId,
      method: 'POST',
      timeoutMs: 60_000,
      body: { title: it.title, reason: it.fromBody ? '' : it.text, body: str(d?.task?.body), comments }
    })
      .then(r => {
        const steps = Array.isArray(r?.steps) ? r.steps.map(str).filter(Boolean) : []
        rewrites.set(k, steps.length ? { status: 'done', steps, at: Date.now() } : { status: 'failed', at: Date.now() })
      })
      .catch(e => {
        log('rewrite failed', it.id, e?.message || e)
        rewrites.set(k, { status: 'failed', at: Date.now() })
      })
      .finally(() => {
        if (snapshot.items.some(x => rewriteKey(x) === k)) {
          snapshot = { ...snapshot, items: withRewrites(snapshot.items) }
          emit()
        }
      })
  }
}

const emit = () => subs.forEach(fn => fn(snapshot))

async function loadDetails(tasks) {
  const now = Date.now()
  await Promise.all(
    tasks
      .filter(t => ['blocked', 'triage'].includes(str(t.status)))
      .map(async t => {
        const k = detailKey(t)
        const hit = details.get(k)
        if (hit && now - hit.at < DETAIL_TTL_MS) {
          if (hit.data?.task?.status) t.status = hit.data.task.status
          return
        }
        try {
          if (t.gateway && !t.connectionId) return
          const data = await api(taskPath(t.id, t.board), { connectionId: t.connectionId, timeoutMs: 8_000 })
          if (data?.task?.status) t.status = data.task.status
          details.set(k, { at: now, data })
        } catch (e) {
          details.delete(k) // expired evidence is not an actionable offline cache
          log('detail failed', t.id, e?.message || e)
        }
      })
  )
}

export async function refresh(force = false) {
  if (running) {
    rerun = true
    return
  }
  if (typeof document !== 'undefined' && document.hidden && !force) return
  running = true
  try {
    const stored = host.state.focusedStoredSessionId.get()
    if (!stored) {
      snapshot = { stored, items: [], issues: [], parked: false }
      return emit()
    }
    if (force) details.clear()
    const ids = await lineageIds(stored)
    const tasks = await sessionTasks(ids)
    if (tasks) await loadDetails(tasks)
    if (host.state.focusedStoredSessionId.get() !== stored) return
    const flat = new Map([...details].map(([k, v]) => [k, v.data]))
    const same = snapshot.stored === stored
    const items = tasks ? blockedItems(tasks, flat) : same ? snapshot.items : []
    snapshot = {
      stored,
      // fetch failed: keep this chat's last list, never another chat's
      items: withRewrites(items),
      issues: tasks ? agentIssues(tasks, flat) : same ? snapshot.issues || [] : [],
      parked: isParked(ids, readParked())
    }
    emit()
    void requestRewrites(items, flat)
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

const AMBER = 'var(--warning, #d29922)'
const btn = (primary = false) => ({
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

function notify(kind, message) {
  try {
    host.notify?.({ kind, message })
  } catch {
    /* older hosts */
  }
}

let osApi = null // ctx.os, set in register()
let previewApi = null // ctx.openPreview (fork core), set in register()

/** Keep ctx's OS + preview doors for link clicks (exported for tests). */
export function bindLinkApis(ctx) {
  osApi = ctx?.os || null
  previewApi = typeof ctx?.openPreview === 'function' ? (t => ctx.openPreview(t)) : null
}

/** Markdown files open rendered in the app's preview pane instead of the OS handler. */
export const isMarkdownPath = p => /\.(md|markdown|mdx)$/i.test(String(p || ''))

export async function openLink(value, kind) {
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
  notify('error', `Couldn't open ${value}`)
}

/** Text with its URLs and local paths as clickable links (paths open with the OS handler). */
function Linked({ text }) {
  const segs = linkSegments(text)
  if (!segs.some(x => x.kind !== 'text')) return str(text)
  return segs.map((x, i) =>
    x.kind === 'text'
      ? x.value
      : jsx('a', {
          href: linkHref(x),
          'data-blocked-link': x.kind,
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

function Item({ item }) {
  const [open, setOpen] = useState(false)
  const [replying, setReplying] = useState(false)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [showNote, setShowNote] = useState(false)
  const list = checklist(item, item.rewritten)
  const long = list.steps.length > COLLAPSED_LINES + 1

  const act = async withNote => {
    setBusy(true)
    try {
      await unblockCard(item, withNote ? note : '')
      notify('success', `Unblocked ${item.id}${withNote && note.trim() ? ' with your note' : ''}`)
      setReplying(false)
      setNote('')
      void refresh(true)
    } catch (e) {
      notify('error', `Couldn't unblock ${item.id}: ${e?.message || e}`)
    } finally {
      setBusy(false)
    }
  }

  const steps = open || !long ? list.steps : list.steps.slice(0, COLLAPSED_LINES)

  return jsxs('div', {
    'data-blocked-card': item.id,
    style: { display: 'flex', flexDirection: 'column', gap: 4, padding: '6px 0', borderTop: '1px solid color-mix(in srgb, var(--border, #8884) 60%, transparent)' },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'baseline', gap: 6, minWidth: 0 },
        children: [
          jsx('span', { style: { fontFamily: 'var(--font-mono, ui-monospace, monospace)', fontSize: 10.5, opacity: 0.65, flex: 'none' }, children: item.id }),
          jsx('span', { title: item.title, style: { fontWeight: 600, fontSize: 12.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }, children: item.title }),
          jsx('span', { style: { marginLeft: 'auto', flex: 'none', fontSize: 10.5, color: AMBER }, children: item.kindLabel })
        ]
      }),
      jsxs('div', {
        style: { fontSize: 12.5, lineHeight: 1.45, color: 'var(--foreground, inherit)', userSelect: 'text' },
        children: [
          !item.loaded && jsx('span', { style: { opacity: 0.6 }, children: 'Loading instructions…' }),
          item.loaded &&
            jsx('div', {
              style: { fontSize: 10.5, opacity: 0.6, marginBottom: 2 },
              children: item.rewriting && list.source !== 'rewritten'
                ? 'Writing clearer steps…'
                : !item.text
                  ? 'No instructions were recorded.'
                  : item.fromBody && item.reasonSource === 'initial'
                    ? 'From the card description. To unblock:'
                  : item.fromBody
                    ? 'No block reason recorded; this is the card description, which may not be what it is waiting for:'
                    : 'To unblock:'
            }),
          item.loaded && list.intro && jsx('div', { style: { whiteSpace: 'pre-wrap' }, children: jsx(Linked, { text: list.intro }) }),
          item.loaded &&
            jsx('ol', {
              'data-blocked-steps': list.source,
              start: list.start,
              style: { margin: '2px 0 0', paddingLeft: 20, display: 'flex', flexDirection: 'column', gap: 3 },
              children: steps.map((s, i) => jsx('li', { style: { paddingLeft: 2 }, children: jsx(Linked, { text: s }) }, i))
            }),
          showNote &&
            jsx('div', {
              style: { marginTop: 4, padding: '4px 8px', borderLeft: `2px solid color-mix(in srgb, ${AMBER} 50%, transparent)`, fontSize: 11.5, opacity: 0.8, whiteSpace: 'pre-wrap' },
              children: jsx(Linked, { text: item.text })
            })
        ]
      }),
      jsxs('div', {
        style: { display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' },
        children: [
          long && jsx('button', { type: 'button', style: { ...btn(), border: 'none', padding: '2px 4px', opacity: 0.75 }, onClick: () => setOpen(!open), children: open ? 'Show less' : `Show all ${list.steps.length} steps` }),
          item.text && list.source !== 'parsed' && jsx('button', { type: 'button', style: { ...btn(), border: 'none', padding: '2px 4px', opacity: 0.75 }, onClick: () => setShowNote(!showNote), children: showNote ? 'Hide agent\u2019s note' : 'Agent\u2019s note' }),
          jsx('span', { style: { flex: 1 } }),
          jsx('button', { type: 'button', style: btn(), onClick: () => openCard(item), children: 'Open card' }),
          item.loaded && jsx('button', { type: 'button', style: btn(), disabled: busy, onClick: () => setReplying(!replying), children: 'Reply & unblock' }),
          item.loaded && jsx('button', { type: 'button', style: btn(true), disabled: busy, onClick: () => act(false), title: 'I did it — move the card back to ready so the worker resumes', children: busy ? 'Unblocking…' : 'Done → unblock' })
        ]
      }),
      replying &&
        jsxs('div', {
          style: { display: 'flex', gap: 6, alignItems: 'flex-end' },
          children: [
            jsx('textarea', {
              value: note,
              autoFocus: true,
              rows: 2,
              placeholder: 'Answer or note for the worker (posted as a card comment)…',
              onChange: e => setNote(e.target.value),
              onKeyDown: e => {
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && note.trim()) {
                  e.preventDefault()
                  void act(true)
                } else if (e.key === 'Escape') setReplying(false)
                e.stopPropagation()
              },
              style: {
                flex: 1,
                resize: 'vertical',
                fontSize: 12.5,
                fontFamily: 'inherit',
                padding: '4px 6px',
                borderRadius: 6,
                border: '1px solid var(--border, #8884)',
                background: 'var(--card, transparent)',
                color: 'var(--foreground, inherit)'
              }
            }),
            jsx('button', { type: 'button', style: btn(true), disabled: busy || !note.trim(), onClick: () => act(true), title: '⌘↩', children: 'Send & unblock' })
          ]
        })
    ]
  })
}

/** An operational failure parked this card. Nothing is asked of you, so no unblock buttons. */
function IssueItem({ issue }) {
  return jsxs('div', {
    'data-blocked-issue': issue.id,
    style: { display: 'flex', flexDirection: 'column', gap: 4, padding: '6px 0', borderTop: '1px solid color-mix(in srgb, var(--border, #8884) 60%, transparent)' },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'baseline', gap: 6, minWidth: 0 },
        children: [
          jsx('span', { style: { fontFamily: 'var(--font-mono, ui-monospace, monospace)', fontSize: 10.5, opacity: 0.65, flex: 'none' }, children: issue.id }),
          jsx('span', { title: issue.title, style: { fontWeight: 600, fontSize: 12.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }, children: issue.title }),
          jsx('span', { style: { marginLeft: 'auto', flex: 'none', fontSize: 10.5, opacity: 0.7 }, children: 'Agent issue' })
        ]
      }),
      jsx('div', { style: { fontSize: 12.5, lineHeight: 1.45, userSelect: 'text' }, children: issue.reason }),
      jsx('div', { 'data-blocked-owner': true, style: { fontSize: 11.5 }, children: `Owner: ${issue.owner || 'unassigned'}` }),
      jsxs('div', {
        style: { display: 'flex', gap: 6, alignItems: 'center' },
        children: [
          jsx('span', { style: { fontSize: 11.5, opacity: 0.75 }, children: 'No action required from you.' }),
          jsx('span', { style: { flex: 1 } }),
          jsx('button', { type: 'button', style: btn(), onClick: () => openCard(issue), children: 'Open card' })
        ]
      })
    ]
  })
}
export { IssueItem }

function Panel() {
  const [snap, setSnap] = useState(snapshot)
  const [collapsed, setCollapsed] = useState(false)
  const focused = useValue(host.state.focusedStoredSessionId)
  useEffect(() => subscribe(setSnap), [])
  useEffect(() => void refresh(), [focused])
  useEffect(() => {
    // ⌘B writes localStorage in this window; re-read on the next tick.
    const onKey = e => (e.metaKey || e.ctrlKey) && str(e.key).toLowerCase() === 'b' && setTimeout(() => void refresh(), 50)
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [])

  if (snap.stored !== focused) return null
  // user-steps' "For you" strip lists these cards (with the same Done → unblock / Reply & unblock buttons);
  // this panel only renders when that plugin is absent.
  if (typeof window !== 'undefined' && window.__userSteps?.ownsComposer) return null
  const { items, parked } = snap
  const issues = snap.issues || []
  if (!items.length && !parked && !issues.length) return null

  const header = items.length
    ? `Blocked: waiting on you${items.length > 1 ? ` (${items.length} cards)` : ''}`
    : parked
      ? 'Blocked: parked by you'
      : `Agent issue${issues.length > 1 ? `s (${issues.length} cards)` : ''}: no action required from you`

  return jsxs('div', {
    'data-slot': 'blocked-panel',
    role: 'status',
    style: {
      width: '100%',
      boxSizing: 'border-box',
      marginBottom: 6,
      padding: '6px 10px',
      borderRadius: 8,
      border: `1px solid color-mix(in srgb, ${AMBER} 55%, transparent)`,
      background: `color-mix(in srgb, ${AMBER} 9%, var(--card, transparent))`,
      color: 'var(--foreground, inherit)',
      maxHeight: '45vh',
      overflowY: 'auto'
    },
    children: [
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, paddingBottom: items.length && !collapsed ? 4 : 0 },
        children: [
          jsx('i', { 'aria-hidden': true, className: 'codicon codicon-debug-pause', style: { color: AMBER, fontSize: 13 } }),
          jsx('span', { style: { fontWeight: 600 }, children: header }),
          !items.length && parked && jsx('span', { style: { opacity: 0.6, fontSize: 11 }, children: 'To unblock:' }),
          parked && items.length > 0 && jsx('span', { style: { opacity: 0.6, fontSize: 11 }, children: '· also parked with ⌘B' }),
          items.length > 0 &&
            jsx('button', {
              type: 'button',
              style: { ...btn(), marginLeft: 'auto', border: 'none', padding: '0 4px' },
              onClick: () => setCollapsed(!collapsed),
              'aria-label': collapsed ? 'Expand' : 'Collapse',
              children: jsx('i', { className: `codicon codicon-chevron-${collapsed ? 'down' : 'up'}` })
            })
        ]
      }),
      !items.length && parked &&
        jsx('ol', {
          'data-blocked-steps': 'parked',
          style: { margin: '2px 0 0', paddingLeft: 20, fontSize: 12.5, lineHeight: 1.45, display: 'flex', flexDirection: 'column', gap: 3 },
          children: PARKED_STEPS.map((s, i) => jsx('li', { children: jsx(Linked, { text: s }) }, i))
        }),
      !collapsed && items.map(item => jsx(Item, { item }, detailKey(item))),
      !collapsed && issues.map(issue => jsx(IssueItem, { issue }, detailKey(issue)))
    ]
  })
}

export default {
  id: ID,
  name: 'Blocked Panel',
  register(ctx) {
    bindLinkApis(ctx)
    ctx.register({ id: 'panel', area: COMPOSER_AREAS.top, order: -20, render: () => jsx(Panel, {}) })
    const onVis = () => document.hidden || void refresh()
    document.addEventListener('visibilitychange', onVis)
    // version 6: explicit non-human pauses (transient) surface as agent issues; failure reasons are plain (no raw error)
    // version 4: subscribe/unblockCard/openCard for user-steps' "For you" strip (it reuses this poller + actions)
    window.__blockedPanel = { version: 6, askSummary, get snapshot() { return snapshot }, refresh, checklist, subscribe, unblockCard, openCard }
    return () => {
      document.removeEventListener('visibilitychange', onVis)
      delete window.__blockedPanel
      if (timer) clearInterval(timer)
      timer = null
    }
  }
}


/**
 * Session Tasks — a right-aligned strip of compact chips above the composer input,
 * one per thing the focused session is running right now:
 *
 *   ⟳ bg   background processes   (gateway RPC `process.list`, status != exited)
 *   ⟳ sub  delegate_task subagents (gateway RPC `subagent.list`, non-terminal status)
 *   ⟳ t_…  kanban tasks this session created or shepherds (tui subscription),
 *          across every board, in todo / ready / running / scheduled / blocked / review / triage
 *          — the same cards the sidebar row's kanban counts cover
 *          (`/api/session-kanban-tasks?ids=<lineage>`; older backends: a scan of the
 *          current `/api/plugins/kanban/board` for cards the lineage created),
 *          plus linked cards on OTHER gateways from this gateway's hermes-hub
 *          replica (`/api/plugins/hub-sync/session-links/<id>`; marked with the
 *          owner gateway + sync age; absent plugin or hub down → none)
 *   ▣ box  rented machines linked to this session (or an ancestor in its lineage)
 *          by the compute-rentals plugin's local registry
 *          (`/api/plugins/compute-rentals/inventory`, polled every 30 s; absent → no chips)
 *
 * Parked kanban cards say what they wait on and since when ("waiting on card t_x on hub since 20:43"), from the
 * backend's per-card `wait` (core kanban_wait_info: the park's producer or its wake check). A wait that only an
 * agent can end and that has lasted over 2 h is amber "stalled"; other parked waits are grey "waiting"; a human
 * ask stays red. [daveey]
 *
 * Hidden when nothing is running. Polls every 5 s while the window is visible.
 * Known-owner kanban chips open the qualified Kanban drawer (never ambient
 * task mentions); unresolved owners are non-clickable. A machine chip opens the
 * Compute Rentals status-bar popover. Hover any chip for the full title.
 */
import { COMPOSER_AREAS, host, useValue } from '@hermes/plugin-sdk'
import * as SDK from '@hermes/plugin-sdk' // namespace: the optional projector exports are absent on older hosts
import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'session-tasks'
const POLL_MS = 5_000
const KANBAN_POLL_MS = 10_000
const MACHINE_POLL_MS = 30_000
const LINEAGE_TTL_MS = 5 * 60_000
const MAX_LABEL = 38
const KANBAN_ACTIVE = new Set(['todo', 'ready', 'running', 'scheduled', 'blocked', 'review', 'triage'])
const SUBAGENT_DONE = new Set(['completed', 'failed', 'interrupted', 'cancelled', 'timeout', 'error'])

const STATE_COLOR = {
  running: 'var(--accent, #3b82f6)',
  stopped: '#8b949e',
  unknown: '#8b949e',
  error: '#f85149',
  ready: '#8b949e',
  todo: '#8b949e',
  scheduled: '#e3b341', // waiting on a timer
  blocked: '#f85149',
  review: '#db61a2',
  triage: '#f85149', // unfinished agent triage, not an implied human ask
  // [daveey] Wait looks (backend `wait.wait_state`), which override the status colour of a parked card:
  waiting: '#8b949e', // parked on a named producer, or not yet stalled: grey
  stalled: '#d29922', // agent-owned wait over 2 h, nothing producing it: amber
  'needs-you': '#f85149' // a visible human ask: red
}

// States whose whole pill (border + fill) takes the state colour, not just the dot/label.
const TINTED_STATES = new Set(['scheduled', 'stalled'])

/** Pill border/background for a chip state (exported for tests). */
export function chipFrame(state) {
  const c = TINTED_STATES.has(state) && STATE_COLOR[state]
  return c
    ? { border: `1px solid color-mix(in srgb, ${c} 60%, transparent)`, background: `color-mix(in srgb, ${c} 14%, transparent)` }
    : {
        border: '1px solid color-mix(in srgb, var(--border, #8884) 80%, transparent)',
        background: 'color-mix(in srgb, var(--card, transparent) 60%, transparent)'
      }
}

export const chipColor = state => STATE_COLOR[state] || STATE_COLOR.running

const str = v => (typeof v === 'string' ? v : v == null ? '' : String(v))
const truncate = (s, n = MAX_LABEL) => (s.length > n ? `${s.slice(0, n - 1)}…` : s)
const log = (...a) => console.info(`[${ID}]`, ...a)

// ── execution observations (consumer of the SDK's optional pure v1 projector) ───────────────────────────────
// Same block in session-tasks and session-panel; test/execution-observations.test.mjs runs both against the shared
// golden fixture so they cannot drift. Producers send raw v1 records (`execution_observations`); every verdict
// (state, expiry, identity fencing, receipts) comes from the SDK projector, called at RENDER time with the clock the
// caller already has, so a cached record expires without any new fetch or timer. Hosts or producers that lack the
// projector or the records show an explicit "unknown", never a zero or an implied running/done. Capacity records
// (kanban.dispatch / kanban.capacity) are diagnostics about WHY work waits: they never become a work state.
const OBS_FNS = ['projectExecutionObservation', 'selectExecutionObservation', 'summarizeExecutionObservations', 'executionIdentityKey']
const CAPACITY_SOURCES = new Set(['kanban.dispatch', 'kanban.capacity'])
const obsOwn = (o, k) => (o && typeof o === 'object' && Object.prototype.hasOwnProperty.call(o, k) ? o[k] : undefined)
const obsNum = v => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const obsText = v => (typeof v === 'string' && v.trim() ? v : '')

/** The SDK projector, or null on a host that predates it (feature-detected, never assumed). */
export const observationSdk = (sdk = SDK) => (OBS_FNS.every(k => typeof sdk?.[k] === 'function') ? sdk : null)

/** Split a producer's `execution_observations` into work records and capacity diagnostics. `present` = the field
 *  was an array at all (an older producer omits it: that is "unknown", not "no work"). */
export function splitObservations(raw) {
  if (!Array.isArray(raw)) return { present: false, work: [], capacity: [] }
  const work = [], capacity = []
  for (const r of raw) (CAPACITY_SOURCES.has(obsOwn(r, 'source')) ? capacity : work).push(r)
  return { present: true, work, capacity }
}

const span = s => (s < 60 ? `${Math.max(0, Math.floor(s))} s` : s < 3600 ? `${Math.floor(s / 60)} min` : s < 86_400 ? `${Math.floor(s / 3600)} h` : `${Math.floor(s / 86_400)} d`)
const qual = o => [obsOwn(o, 'gateway'), obsOwn(o, 'profile')].filter(obsText).join('/')

function identityText(identity) {
  const run = obsOwn(identity, 'run_id') ?? obsOwn(identity, 'generation')
  const head = [qual(identity), obsText(obsOwn(identity, 'board'))].filter(Boolean).join('/')
  const work = `${obsText(obsOwn(identity, 'kind')) || '?'}:${obsText(obsOwn(identity, 'work_id')) || '?'}`
  return `${head ? `${head}/` : ''}${work}${run != null ? `#${run}` : ''}`
}

function waitView(w, nowSec) {
  if (!w || typeof w !== 'object') return null
  const target = obsOwn(w, 'target')
  const since = obsNum(obsOwn(w, 'since')), due = obsNum(obsOwn(w, 'due_at'))
  return {
    kind: obsText(obsOwn(w, 'kind')) || null,
    predicate: obsText(obsOwn(w, 'predicate')) || null,
    target: target && typeof target === 'object' ? [qual(target), obsText(obsOwn(target, 'board')), obsText(obsOwn(target, 'work_id'))].filter(Boolean).join('/') || null : null,
    owner: qual(obsOwn(w, 'owner')) || null,
    sinceAgeSec: since == null ? null : Math.max(0, nowSec - since),
    dueInSec: due == null ? null : due - nowSec
  }
}

/** Display model of one PROJECTED observation (what the SDK returned), nothing re-derived. */
export function observationView(obs, nowSec, sdk = observationSdk(), sourceClass = 'sidebar') {
  const progressAt = obsNum(obsOwn(obs, 'last_progress'))
  const asks = sdk ? obsNum(sdk.summarizeExecutionObservations([obs], nowSec, sourceClass)?.outstanding_human_requests) : null
  const historical = obsOwn(obs, 'historical_delivery_receipts')
  const receipt = obsOwn(obs, 'delivery_receipt')
  const activityAt = obsNum(obsOwn(obs, 'last_activity'))
  return {
    sourced: true,
    state: obsOwn(obs, 'state') || 'unknown',
    freshness: obsOwn(obs, 'freshness') || 'unknown',
    reason: obsText(obsOwn(obs, 'unknown_reason')) || null,
    lastKnown: obsText(obsOwn(obs, 'last_known_state')) || null,
    ageSec: obsNum(obsOwn(obs, 'age_seconds')),
    source: obsText(obsOwn(obs, 'source')) || null,
    id: identityText(obsOwn(obs, 'identity')),
    owner: [qual(obsOwn(obs, 'owner')), obsText(obsOwn(obsOwn(obs, 'owner'), 'session')) || obsText(obsOwn(obsOwn(obs, 'owner'), 'assignee'))].filter(Boolean).join('/') || null,
    progress: progressAt == null ? null : { ageSec: Math.max(0, nowSec - progressAt), kind: obsOwn(obs, 'last_progress_kind') || null, ref: obsOwn(obs, 'last_progress_ref') || null },
    activity: activityAt == null ? null : { ageSec: Math.max(0, nowSec - activityAt), kind: obsText(obsOwn(obs, 'last_activity_kind')) || null },
    wait: waitView(obsOwn(obs, 'wait'), nowSec),
    // Receipts stay historical evidence of an execution even once the CURRENT state is unknown/stale.
    receipts: Array.isArray(historical) ? historical.length : receipt ? 1 : 0,
    asks
  }
}

const missingView = (reason, extra = {}) => ({
  sourced: false, state: 'unknown', freshness: 'unknown', reason, lastKnown: null, ageSec: null, source: null, id: '', owner: null,
  progress: null, activity: null, wait: null, receipts: 0, asks: null, ...extra
})

const classOf = c => c || 'sidebar'

/** No observations array. A hermes-hub replica row (current producer, but a replica is not source evidence) says so
 *  and ages from the card's own last activity (source clock; never `synced_at`, a copy time). Anything else is an
 *  older producer. Both are explicit unknown, never certain running. */
export const REPLICA_REASON = 'hub_replica_not_source_evidence'
export function unobservedView(chip, nowSec) {
  const lastKnown = chip?.kind === 'kanban' ? str(chip.state) || null : null
  if (!chip?.replica) return missingView('producer_sent_no_observations')
  const at = Number(chip.activityAt)
  return missingView(REPLICA_REASON, { lastKnown, ageSec: at > 0 && Number.isFinite(at) ? Math.max(0, nowSec - at) : null })
}

/** One record -> view. */
export function projectView(record, nowSec, sourceClass = 'sidebar', sdk = observationSdk(), expectedIdentity) {
  if (!sdk) return missingView('sdk_projector_unavailable')
  return observationView(sdk.projectExecutionObservation(record, nowSec, classOf(sourceClass), expectedIdentity), nowSec, sdk, classOf(sourceClass))
}

/** Records of ONE exact execution identity -> view (SDK selection: old runs and other owners never overwrite). */
export function selectView(records, identity, nowSec, sourceClass = 'sidebar', sdk = observationSdk()) {
  if (!sdk) return missingView('sdk_projector_unavailable')
  return observationView(sdk.selectExecutionObservation(records, identity, nowSec, classOf(sourceClass)), nowSec, sdk, classOf(sourceClass))
}

/** Counts across records, per the SDK. Never a zero for what could not be read: empty is `uncertain`. */
export function summaryView(records, nowSec, sourceClass = 'sidebar', sdk = observationSdk()) {
  if (!sdk) return { counts: null, uncertain: true, outstanding: null, states: [], reason: 'sdk_projector_unavailable' }
  const s = sdk.summarizeExecutionObservations(records, nowSec, classOf(sourceClass))
  return { counts: s.counts, uncertain: !!s.uncertain, outstanding: s.outstanding_human_requests, states: s.observations.map(o => o.state), reason: null }
}

/** The chip's card evidence at `nowSec`. `chip.observations` = the producer's raw records (null = older producer).
 *  Records are matched by exact card coordinates; two different execution identities stay unknown, never merged. */
export function chipObservation(chip, nowSec, { sdk = observationSdk(), sourceClass = 'sidebar' } = {}) {
  if (!sdk) return missingView('sdk_projector_unavailable')
  const split = splitObservations(chip?.observations)
  if (!split.present) return unobservedView(chip, nowSec)
  const candidates = split.work.filter(r => {
    const id = obsOwn(r, 'identity')
    const kind = chip.kind === 'bg' ? 'process' : chip.kind === 'sub' ? 'subagent' : 'card'
    // A sub-agent's canonical work id is its child session id (never the registry/delegation id).
    if (obsOwn(id, 'kind') !== kind || obsOwn(id, 'work_id') !== (chip.kind === 'sub' ? chip.workId : chip.id)) return false

    for (const [k, mine] of [['board', chip.board], ['profile', chip.profile], ['gateway', chip.gateway]]) {
      const theirs = obsOwn(id, k)
      if (obsText(mine) && theirs !== undefined && theirs !== mine) return false
    }
    return true
  })
  if (!candidates.length) return missingView('no_observation_for_card', { capacityOnly: split.capacity.length > 0 })
  const keys = new Set(candidates.map(r => sdk.executionIdentityKey(obsOwn(r, 'identity')) ?? 'invalid'))
  let view
  if (keys.size > 1) view = missingView('ambiguous_execution_identity', { sourced: true })
  else {
    const identity = obsOwn(candidates[0], 'identity')
    view = keys.has('invalid') ? projectView(candidates[0], nowSec, sourceClass, sdk) : selectView(candidates, identity, nowSec, sourceClass, sdk)
  }
  // The row is session-tasks' cached copy after a failed read: whatever it last said, it is not current now.
  if (chip.stale && view.state !== 'unknown') view = { ...view, lastKnown: view.state, state: 'unknown', freshness: 'stale', reason: 'cached_after_failed_read' }
  return view
}

/** Capacity diagnostics for the chip, shown apart from work state. */
export function chipCapacity(chip) {
  const out = []
  for (const r of splitObservations(chip?.observations).capacity) {
    for (const k of ['reasons', 'diagnostics', 'machine_reasons']) {
      for (const d of Array.isArray(obsOwn(r, k)) ? r[k] : []) if (obsText(obsOwn(d, 'code'))) out.push(d.code)
    }
    if (obsText(obsOwn(r, 'code'))) out.push(r.code)
  }
  return [...new Set(out)].slice(0, 3)
}

/** One plain line for a view (and a diagnostic line, if any). */
export function observationLine(v, nowSec) {
  const parts = [v.state === 'unknown' ? `unknown${v.reason ? ` (${v.reason.replace(/_/g, ' ')})` : ''}` : v.state.replace(/_/g, ' ')]
  if (v.state === 'unknown' && v.lastKnown) parts.push(`last known ${v.lastKnown.replace(/_/g, ' ')}`)
  if (v.ageSec != null) parts.push(`observed ${span(v.ageSec)} ago`)
  if (v.wait) {
    const w = v.wait
    parts.push(`waiting: ${[w.kind, w.predicate].filter(Boolean).join(' ') || 'condition unknown'}${w.target ? ` on ${w.target}` : ''}${w.owner ? ` · owner ${w.owner}` : ''}${w.sinceAgeSec != null ? ` · ${span(w.sinceAgeSec)}` : ''}${w.dueInSec != null ? (w.dueInSec >= 0 ? ` · due in ${span(w.dueInSec)}` : ` · overdue ${span(-w.dueInSec)}`) : ''}`)
  }
  parts.push(v.progress ? `progress ${[v.progress.kind, `${span(v.progress.ageSec)} ago`, v.progress.ref && `(${v.progress.ref})`].filter(Boolean).join(' ')}` : 'progress unknown')
  if (v.receipts) parts.push(v.state === 'done' ? 'verified completion receipt' : `${v.receipts} historical completion receipt${v.receipts === 1 ? '' : 's'}`)
  if (v.id) parts.push(v.id)
  if (v.source) parts.push(`source ${v.source}`)
  if (v.owner) parts.push(`owner ${v.owner}`)
  return parts.join(' · ')
}

export const chipEvidenceLine = (chip, nowSec, opts) => {
  const v = chipObservation(chip, nowSec, opts)
  const cap = chipCapacity(chip)
  return `${observationLine(v, nowSec)}${cap.length ? ` · capacity diagnostic (not work state): ${cap.join(', ')}` : ''}`
}

export function elapsed(sinceSec, nowMs = Date.now()) {
  if (!sinceSec) return ''
  const s = Math.max(0, Math.floor(nowMs / 1000 - sinceSec))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}`
  return `${Math.floor(s / 86400)}d`
}

/** Wall-clock "since" for a wait start (seconds): HH:MM today, else "Mon 26 20:43" (exported for tests). */
export function sinceClock(sec, nowMs = Date.now()) {
  const t = Number(sec)
  if (!t) return ''
  const d = new Date(t * 1000)
  const now = new Date(nowMs)
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (d.toDateString() === now.toDateString()) return hm
  const day = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()]
  return `${day} ${d.getDate()} ${hm}`
}

/** The card's wait look from the backend `wait` object: 'stalled' | 'waiting' | 'needs-you' | null. */
export function waitLook(wait) {
  const state = wait && typeof wait === 'object' ? str(wait.wait_state) : ''
  return state === 'stalled' ? 'stalled' : state === 'human' ? 'needs-you' : state === 'waiting' ? 'waiting' : null
}

/** "waiting on <producer> since <time> (<age> ago)", or '' when the backend sent no wait (exported for tests). */
export function waitLine(chip, nowMs = Date.now()) {
  if (!chip?.waitingOn) return ''
  const at = sinceClock(chip.waitingSince, nowMs)
  const ago = elapsed(chip.waitingSince, nowMs)
  const head = chip.look === 'stalled' ? 'stalled: ' : ''
  return `${head}waiting on ${chip.waitingOn}${at ? ` since ${at}` : ''}${ago ? ` (${ago} ago)` : ''}`
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
      return { key: `bg:${str(p.session_id) || cmd}`, id: str(p.session_id), kind: 'bg', state: 'unknown', observations: Array.isArray(p.execution_observations) ? p.execution_observations : null, label: cmd, title: str(p.command) || cmd, since }
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
        id: str(s.subagent_id),
        workId: str(s.child_session_id) || null, // canonical identity; absent on older cores -> unknown
        observations: Array.isArray(s.execution_observations) ? s.execution_observations : null,
        state: 'unknown',
        label: goal,
        title: str(s.goal) || goal,
        since: Number(s.started_at) || 0,
        // Display-only activity counters; not substantive progress or a stall verdict.
        toolCount: Number(s.tool_count) || 0,
        lastTool: str(s.last_tool) || null
      }
    })
}

const KANBAN_RANK = { running: 0, stalled: 1, blocked: 1, 'needs-you': 1, review: 2, triage: 2, ready: 3, scheduled: 4, waiting: 4, todo: 5 }

/** The card's structured ask (`human_request`) as trimmed plain strings, or null when it names no action. The hub
 *  replica stores it as JSON text, so a string is decoded. Exported for tests. */
export function humanRequestOf(raw) {
  let r = raw
  if (typeof r === 'string') { try { r = JSON.parse(r) } catch { return null } }
  if (!r || typeof r !== 'object' || Array.isArray(r)) return null
  const s = k => (typeof r[k] === 'string' ? r[k].trim() : '')
  const out = { action: s('action'), why: s('why_human'), interaction: s('interaction') }
  return out.action ? out : null
}

function taskChip(t, status) {
  const watching = t.via === 'subscribed'
  const connectionId = t.connectionId || (t.gateway ? connectionForHubGateway(t.gateway) : null)
  const wait = t.wait && typeof t.wait === 'object' ? t.wait : null
  const rawLook = t.stale ? null : waitLook(wait)
  // Grey 'waiting' only recolours a scheduled card: a blocked/triage card keeps its own (red/amber) status colour,
  // so a dependency block or an unvalidated ask never turns grey. The wait line still names what it waits on.
  const look = rawLook === 'waiting' && status !== 'scheduled' ? null : rawLook
  const waitingOn = rawLook ? str(wait.waiting_on) : ''
  const waitingSince = rawLook ? Number(wait.waiting_since) || 0 : 0
  const waitTitle = waitingOn ? `\n${look === 'stalled' ? 'STALLED (agent-owned wait over 2 h): ' : ''}waiting on ${waitingOn}${waitingSince ? ` since ${sinceClock(waitingSince)}` : ''}` : ''
  return {
    look,
    waitingOn,
    waitingSince,
    // What the card asks the user to do, in its own words (Needs you shows this, never the card title).
    humanRequest: humanRequestOf(t.human_request),
    // The producer's raw v1 work records (null = older producer); projected at render time, never here.
    observations: Array.isArray(t.execution_observations) ? t.execution_observations : null,
    key: `kb:${taskKey(t.gateway || connectionId, t.board, t.id, t.profile)}`,
    kind: 'kanban',
    id: str(t.id),
    gateway: str(t.gateway),
    gatewayId: str(t.gatewayId || t.gateway),
    connectionId,
    profile: str(t.profile) || null,
    board: str(t.board) || 'default',
    stale: Boolean(t.stale),
    state: status,
    label: str(t.title) || t.id,
    assignee: str(t.assignee) || null,
    // Where the card runs or will run (backend `run_machine`): 'hub', a worker name, 'worker-pool', or null.
    runMachine: str(t.run_machine) || null,
    note: str(t.note) || null,
    prUrl: /^https:\/\/github\.com\//.test(str(t.pr_url)) ? str(t.pr_url) : null,
    activity: str(t.activity_kind) || null,
    activityAt: Number(t.activity_at) || 0,
    // When the card last made progress: its newest event, else the current run's start; 0 = unknown. Never the
    // first claim or creation (`since`, display only): a hub row without events otherwise read as a week-old stall
    // (kanban-f241d1f60b).
    progressAt: Number(t.activity_at) || Number(t.current_run_started_at) || 0,
    title: `${t.id} · ${status}${t.run_machine ? ` · ${runsOnText(str(t.run_machine))}` : ''}${t.assignee ? ` · ${t.assignee}` : ''}${watching ? ' · shepherding (created by another chat)' : ''}${t.board && t.board !== 'default' ? ` · board ${t.board}` : ''}${t.stale ? ' · stale (last known)' : ''}\n${str(t.title)}${waitTitle}`,
    since: Number(t.current_run_started_at || t.started_at || t.created_at) || 0
  }
}

/** Hub-scheduled cards carry an [H] in their status box (exported for tests). */
export const onHub = chip => chip?.runMachine === 'hub'
export const runsOnText = m => (m === 'worker-pool' ? 'runs on a pool worker' : `runs on ${m}`)

// A parked card ranks by its wait look (stalled/needs-you first, grey waiting with the other parked work).
const rankOf = c => KANBAN_RANK[c.look || c.state] ?? 9
const byRank = chips => chips.sort((a, b) => rankOf(a) - rankOf(b))

/** `/api/session-kanban-tasks` rows (already filtered to the lineage by the backend). */
function withLocalRoute(t, route) {
  // Only untagged serving-profile rows inherit the captured read route.
  if (!t || !route || t.gateway || t.connectionId || t.profile === null || t.ownership_state === 'unknown') return t
  return { ...t, connectionId: route.connectionId || null, profile: t.profile || route.profile || null }
}

export function sessionTaskChips(tasks, route) {
  const seen = new Set()
  const out = []
  for (const row of Array.isArray(tasks) ? tasks : []) {
    const t = withLocalRoute(row, route)
    const reported = str(t?.status)
    const status = t?.unfinished && !KANBAN_ACTIVE.has(reported) ? 'unknown' : reported
    const key = taskKey(t?.gateway || t?.connectionId, t?.board, t?.id, t?.profile)
    if (!t?.id || (!KANBAN_ACTIVE.has(status) && status !== 'unknown') || seen.has(key)) continue
    seen.add(key)
    out.push(taskChip(t, status))
  }
  return byRank(out)
}

function syncedAgo(at) {
  const t = Number(at)
  if (!t) return 'never synced'
  const s = Math.max(0, Date.now() / 1000 - t)
  if (s < 60) return 'synced just now'
  if (s < 3600) return `synced ${Math.floor(s / 60)}m ago`
  if (s < 86_400) return `synced ${Math.floor(s / 3600)}h ago`
  return `synced ${Math.floor(s / 86_400)}d ago`
}

/** hermes-hub replica rows (`/api/plugins/hub-sync/session-links`): cards this
 *  lineage created/shepherds on OTHER gateways. The gateway's own cards come
 *  from the live route, so `local` rows are dropped; everything is keyed by
 *  gateway + profile + board + id (the same id can exist on multiple owners).
 *  `liveKeys`: `${gateway}:${profile}:${board}:${id}` already listed live. */
export const taskKey = (gateway, board, id, profile) => `${str(gateway)}:${str(profile) || '?'}:${str(board) || 'default'}:${str(id)}`

export function hubLinkChips(tasks, liveKeys = new Set()) {
  const seen = new Set()
  const out = []
  for (const t of Array.isArray(tasks) ? tasks : []) {
    const reported = str(t?.status)
    const status = t?.unfinished && !KANBAN_ACTIVE.has(reported) ? 'unknown' : reported
    const gateway = str(t?.gateway)
    const board = str(t?.board) || 'default'
    if (!t?.id || t.local || !gateway || (!KANBAN_ACTIVE.has(status) && status !== 'unknown')) continue
    const k = taskKey(gateway, board, t.id, t.profile)
    if (liveKeys.has(k) || seen.has(k)) continue
    seen.add(k)
    const chip = taskChip(t, status)
    out.push({ ...chip, replica: true, key: `kb:${k}`, gateway, board, title: `${chip.title}\non ${gateway} · ${syncedAgo(t.synced_at)} (hermes-hub replica)` })
  }
  return byRank(out)
}

/** Last-known remote chips, marked stale, for when the hub answer is missing:
 *  never "no tasks" just because the hub (or hub-sync) is down. */
export function staleChips(chips) {
  return (chips || []).map(c => (c.stale ? c : { ...c, stale: true, title: `${c.title}\n(last known — hermes-hub replica unavailable)` }))
}

const HUB_NAMES_KEY = 'hermes.desktop.hubGatewayNames.v1' // connection id -> hub gateway name (core store/hub-sync.ts)

/** Registry connection id of a hub gateway name, from the map core learns. */
export function connectionForHubGateway(gateway, storage = typeof localStorage !== 'undefined' ? localStorage : null) {
  try {
    const map = JSON.parse(storage?.getItem(HUB_NAMES_KEY) || '{}')
    return Object.keys(map).find(id => map[id] === gateway) || null
  } catch {
    return null
  }
}

// A card on another gateway opens in the Kanban drawer on its owner (the board
// falls back to the hub replica when that gateway is unreachable).
function openQualifiedTask(chip) {
  const open = typeof window !== 'undefined' ? window.__hermesKanbanOpenTask : null
  const connection = chip.connectionId
  if (typeof open !== 'function' || !connection || !chip.profile || !open.supportsQualifiedOwners) return false
  return open(chip.id, chip.board, connection, chip.profile) !== false
}

/** Fallback for backends without that route: scan the current board. */
export function kanbanChips(board, sessionIds, route) {
  const ids = sessionIds instanceof Set ? sessionIds : new Set(sessionIds || [])
  const out = []
  for (const col of board?.columns || []) {
    for (const t of col?.tasks || []) {
      const status = str(t?.status || col?.name)
      if (!KANBAN_ACTIVE.has(status) || !ids.has(str(t?.session_id))) continue
      out.push(taskChip(withLocalRoute({ ...t, board: t.board || board.board || board.slug || 'default' }, route), status))
    }
  }
  return byRank(out)
}

// Rented machines (compute-rentals inventory rows). A machine belongs to the
// session when one of its ownership bindings names this gateway + profile and
// any id in the session's lineage (bindings keep the id they were made under).
const MACHINE_RANK = { running: 0, error: 1 }

function rateLabel(m) {
  if (m?.billing_class === 'internal_prepaid') return ''
  const rate = Number(m?.hourly_rate)
  if (!m?.currency || m?.hourly_rate == null || !Number.isFinite(rate)) return ''
  const amount = rate === 0 ? '0' : rate >= 10 ? rate.toFixed(0) : rate.toFixed(2)
  return m.currency === 'USD' ? `$${amount}/h` : `${amount} ${m.currency}/h`
}

export function machineChips(machines, { gatewayId, profile, ids } = {}) {
  const lineage = ids instanceof Set ? ids : new Set(ids || [])
  if (!gatewayId || !profile || !lineage.size) return []
  const seen = new Set()
  const out = []
  for (const m of Array.isArray(machines) ? machines : []) {
    if (!m?.key || seen.has(m.key)) continue
    const mine = (m.ownership || []).some(
      o => o && o.gateway_id === gatewayId && o.profile === profile && lineage.has(str(o.session_id))
    )
    if (!mine) continue
    seen.add(m.key)
    const status = str(m.status).toLowerCase() || 'unknown'
    const rate = rateLabel(m)
    const name = str(m.display_name) || str(m.provider_id) || 'machine'
    out.push({
      key: `box:${m.key}`,
      kind: 'machine',
      id: str(m.key),
      state: status,
      label: name,
      extra: rate,
      title: [
        `${name} · ${str(m.provider)}${m.region ? ` ${m.region}` : ''} · ${status}${m.stale ? ' (stale)' : ''}`,
        str(m.hardware),
        rate || (m.billing_class === 'internal_prepaid' ? 'internal prepaid' : 'rate unknown'),
        m.ssh ? `ssh: ${m.ssh}` : ''
      ].filter(Boolean).join('\n'),
      since: status === 'running' ? (Date.parse(str(m.started_at)) || 0) / 1000 : 0
    })
  }
  return out.sort((a, b) => (MACHINE_RANK[a.state] ?? 9) - (MACHINE_RANK[b.state] ?? 9))
}

// Archive guard line: the machines this chat still holds, so archiving it
// (which only hides the chat) doesn't leave them running unnoticed.
export function machineArchiveLines(chips, max = 3) {
  const machines = (chips || []).filter(c => c?.kind === 'machine')
  if (!machines.length) return []
  const part = c => `${c.label} (${[c.state, c.extra].filter(Boolean).join(', ')})`
  const shown = machines.slice(0, max).map(part).join(', ')
  const more = machines.length > max ? ` +${machines.length - max} more` : ''
  return [{ label: machines.length === 1 ? 'Rented machine' : `Rented machines (${machines.length})`, value: shown + more }]
}

// ── data ────────────────────────────────────────────────────────────────────

function activeConnection() {
  try {
    return host.activeConnectionId?.() || host.state?.connectionId?.get?.() || undefined
  } catch {
    return undefined
  }
}

function focusedRoute() {
  const owner = host.state?.focusedSessionOwner?.get?.()
  return {
    // An owner with no connection is unresolved (including primary), not active.
    connectionId: owner ? owner.connectionId : activeConnection(),
    profile: owner ? (owner.targetProfile || owner.profile || 'default') : (host.state?.profile?.get?.() || 'default')
  }
}

function api(path, connection, timeoutMs = 15_000, profile) {
  const bridge = typeof window !== 'undefined' ? window.hermesDesktop?.api : null
  if (!bridge) return Promise.reject(new Error('Hermes desktop bridge unavailable'))
  // Only an explicitly ambient caller may resolve the active connection.
  // An unresolved captured owner must never be interpreted by the bridge.
  const connectionId = arguments.length === 1 ? activeConnection() : connection
  if (!connectionId) return Promise.reject(new Error('Session owner connection unavailable'))
  return bridge({ path, connectionId, ...(profile !== undefined ? { profile } : {}), timeoutMs })
}

// stored id → { at, ids:Set } — the session plus its parent chain (compression
// rotates the stored id; kanban tasks keep the id they were created under).
const lineageCache = new Map()

// ── last-known cards per chat (instant paint on switch) ─────────────────────
// Switching chats paints that chat's last-known kanban + machine chips at once
// from this cache (memory, persisted to localStorage), then the network refresh
// replaces them. Bounded to the newest CACHE_MAX chats.
const CACHE_KEY = 'hermes.sessionTasks.cache.v1'
const CACHE_MAX = 200
const chipCache = new Map()
let cacheLoaded = false
let cacheSaveTimer = null

function storage() {
  try {
    return typeof localStorage !== 'undefined' ? localStorage : null
  } catch {
    return null
  }
}

function loadCache() {
  if (cacheLoaded) return
  cacheLoaded = true
  try {
    const raw = JSON.parse(storage()?.getItem(CACHE_KEY) || '{}')
    for (const [scope, v] of Object.entries(raw)) {
      if (v && Array.isArray(v.kanban) && Array.isArray(v.machines)) chipCache.set(scope, v)
    }
  } catch {
    // corrupt or unavailable: start empty
  }
}

function saveCacheSoon() {
  if (cacheSaveTimer) return
  cacheSaveTimer = setTimeout(() => {
    cacheSaveTimer = null
    try {
      const newest = [...chipCache.entries()].sort((a, b) => b[1].at - a[1].at).slice(0, CACHE_MAX)
      chipCache.clear()
      for (const [k, v] of newest) chipCache.set(k, v)
      storage()?.setItem(CACHE_KEY, JSON.stringify(Object.fromEntries(newest)))
    } catch {
      // quota or unavailable: the memory copy still serves this window
    }
  }, 1000)
}

/** Last-known chips for a chat scope (exported for tests). */
export function cachedChips(scope) {
  loadCache()
  return chipCache.get(scope) || null
}

/** Remember a chat's chips; warning chips (kind kanban-status) are never cached. */
export function rememberChips(scope, kanban, machines) {
  loadCache()
  const keep = chips => (chips || []).filter(c => c && c.kind !== 'kanban-status')
  const prev = chipCache.get(scope)
  chipCache.set(scope, { at: Date.now(), kanban: keep(kanban ?? prev?.kanban), machines: keep(machines ?? prev?.machines) })
  saveCacheSoon()
}

// A complete parent chain never changes (compression adds a new id on top; old ids keep their parent), so it
// is kept across launches: a revisited chat skips the one-request-per-hop ancestry walk.
const LINEAGE_KEY = 'hermes.sessionTasks.lineage.v1'
const LINEAGE_MAX = 500

function savedLineage(cacheKey) {
  try {
    const ids = JSON.parse(storage()?.getItem(LINEAGE_KEY) || '{}')[cacheKey]
    return Array.isArray(ids) && ids.length ? new Set(ids) : null
  } catch {
    return null
  }
}

function saveLineage(cacheKey, ids) {
  try {
    const all = JSON.parse(storage()?.getItem(LINEAGE_KEY) || '{}')
    delete all[cacheKey]
    all[cacheKey] = [...ids]
    const keys = Object.keys(all)
    for (const k of keys.slice(0, Math.max(0, keys.length - LINEAGE_MAX))) delete all[k]
    storage()?.setItem(LINEAGE_KEY, JSON.stringify(all))
  } catch {
    // unavailable: the in-memory cache still serves this window
  }
}

async function lineageIds(storedId, connection, profile = 'default') {
  const cacheKey = JSON.stringify([connection || '', profile, storedId])
  const hit = lineageCache.get(cacheKey)
  if (hit && Date.now() - hit.at < LINEAGE_TTL_MS) return hit.ids
  const saved = savedLineage(cacheKey)
  if (saved) {
    lineageCache.set(cacheKey, { at: Date.now(), ids: saved })
    return saved
  }
  const ids = new Set([storedId])
  let cur = storedId
  for (let hop = 0; hop < 12 && cur; hop++) {
    try {
      const row = await api(`/api/sessions/${encodeURIComponent(cur)}`, connection, 5_000, profile)
      if (!row || row.id !== cur || (row.parent_session_id != null && typeof row.parent_session_id !== 'string')) {
        ids.incomplete = `malformed row from GET /api/sessions/${cur}`
        return ids
      }
      ids.add(row.id)
      cur = str(row.parent_session_id)
      if (cur) ids.add(cur)
    } catch (error) {
      const message = String(error?.message ?? error)
      ids.incomplete = `GET /api/sessions/${cur} failed: ${message.slice(0, 200)}`
      // The focused id itself has no row yet: a new chat before its first turn is stored.
      if (hop === 0 && /^404\b/.test(message)) ids.unstored = true
      return ids
    }
  }
  if (cur) {
    ids.incomplete = 'ancestry deeper than 12 hops'
    return ids
  }
  lineageCache.set(cacheKey, { at: Date.now(), ids })
  saveLineage(cacheKey, ids)
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
let lastMachinesAt = 0
let machineCache = []
let inflight = null // the round now running: { scope }; a round for another chat never waits on it
let rerun = false

const emit = () => subs.forEach(fn => fn(snapshot))

// Last good hub answer for the focused lineage (shown stale while the hub is unreachable).
let hubLinkCache = { session: null, chips: [] }
const taskReadCache = new Map()

export async function loadKanban(ids, route = focusedRoute()) {
  route = { ...route }
  const cacheScope = JSON.stringify([route.connectionId || '', route.profile, [...ids].sort()])
  const read = (path, timeoutMs = 15_000) => api(path, route.connectionId, timeoutMs, route.profile)
  const list = await safe(
    () => read(`/api/session-kanban-tasks?ids=${encodeURIComponent([...ids].join(','))}`),
    null
  )
  let live = null
  if (list && Array.isArray(list.tasks)) {
    live = sessionTaskChips(list.tasks, route)
    taskReadCache.set(cacheScope, live)
    // Authoritative projections include remote obligations and releases.
    // Replica inventory must not add back released or unrelated tasks.
    hubLinkCache = { session: cacheScope, chips: [] }
    return live
  } else {
    const board = await safe(() => read('/api/plugins/kanban/board'), null)
    const known = taskReadCache.has(cacheScope)
    const previous = known ? staleChips(taskReadCache.get(cacheScope)) : []
    const fallback = !known && board ? kanbanChips(board, ids, route) : []
    const seen = new Set(previous.map(c => c.key))
    // A readable legacy board cannot establish that the origin ledger is empty.
    live = [...previous, ...fallback.filter(c => !seen.has(c.key)), {
      key: `kb-status:${cacheScope}`, kind: 'kanban-status', state: 'unknown', stale: true,
      label: 'Task status unavailable', title: 'Could not read authoritative session tasks; work may still be unfinished.'
    }]
    // Preserve the last authoritative set, including absence after release.
    // An outage adds uncertainty, not replica resurrection of released work.
    if (known) return live
  }
  // Linked cards on other gateways, from this gateway's hermes-hub replica
  // (hub-sync plugin). Absent plugin / hub down → no extra chips.
  const [first, ...rest] = [...ids]
  const hub = first
    ? await safe(
        () => read(`/api/plugins/hub-sync/session-links/${encodeURIComponent(first)}?ids=${encodeURIComponent(rest.join(','))}&origin_profile=${encodeURIComponent(route.profile || '')}`, 4_000),
        null
      )
    : null
  let remote
  if (hub && hub.available !== false && Array.isArray(hub.tasks)) {
    // Older plugins do not qualify the origin join. Keep their discovery rows
    // visible as unknown, but never manufacture a clickable owner from them.
    const rows = hub.qualified_origins === true && hub.origin_profile === route.profile
      ? hub.tasks : hub.tasks.map(t => ({ ...t, profile: null, connectionId: null, status: 'unknown' }))
    remote = hubLinkChips(rows)
    hubLinkCache = { session: cacheScope, chips: remote }
  } else {
    remote = first && hubLinkCache.session === cacheScope ? staleChips(hubLinkCache.chips) : []
  }
  if (!live && !remote.length) return null
  const seen = new Set()
  return [...(live || []), ...remote].filter(chip => {
    if (seen.has(chip.key)) return false
    seen.add(chip.key)
    return true
  })
}

function focusedProfile() {
  try {
    const owner = host.state.focusedSessionOwner?.get?.()
    const conn = activeConnection()
    // The inventory is read through the active connection; a session owned by
    // another connection/profile would be matched against the wrong registry.
    if (owner?.connectionId && conn && owner.connectionId !== conn) return null
    return owner?.profile || host.state.profile?.get?.() || 'default'
  } catch {
    return null
  }
}

async function loadMachines(ids) {
  const profile = focusedProfile()
  if (!profile) return []
  const inv = await safe(() => api('/api/plugins/compute-rentals/inventory?session_id=&profile=&gateway_id='), null)
  if (!inv || !Array.isArray(inv.machines)) return null
  return machineChips(inv.machines, { gatewayId: str(inv.gateway_id), profile, ids })
}

// ctx.registerArchiveGuard: look up the machines bound to the chat being
// archived on ITS gateway (connectionId from the row), across its lineage.
export async function archiveGuard({ sessionId, ids, profile, connectionId } = {}) {
  if (!sessionId) return []
  // ArchiveGuardContext uses an omitted connection for the registry primary.
  // Resolve that contract here, not in api(): other callers' missing owners
  // remain unknown, and the active gateway is never a substitute for primary.
  if (connectionId === undefined) {
    const connections = await safe(() => host.connections(), [])
    connectionId = connections?.find(c => c.primary)?.id
  }
  const lineage = new Set(ids || [])
  const resolved = await lineageIds(sessionId, connectionId, profile || 'default')
  if (resolved.incomplete) {
    // The dialog line cannot say which owner was asked or why it failed, and
    // hermes:api never logs an expected 404; error level reaches desktop.log.
    console.error(`[session-tasks archive-guard] ancestry unavailable for ${sessionId} via ${connectionId || '(no owner)'}/${profile || 'default'}: ${resolved.incomplete}`)
    return [{ label: 'Unverified work', value: 'Session ancestry unavailable; linked work or rented machines may still be active.' }]
  }
  for (const id of resolved) lineage.add(id)
  const inv = await api('/api/plugins/compute-rentals/inventory?session_id=&profile=&gateway_id=', connectionId, 2_000, profile || 'default')
  if (!inv || !Array.isArray(inv.machines)) return []
  const chips = machineChips(inv.machines, { gatewayId: str(inv.gateway_id), profile: profile || 'default', ids: lineage })
  return machineArchiveLines(chips)
}

/** Background warm for a chat that is NOT focused: read its linked cards and remember them, so switching to it
 *  paints at once. Touches only the per-chat cache, never the focused snapshot. Remembers only an authoritative
 *  answer (a failed read leaves the last-known copy alone). Returns true when it refreshed the cache. */
export async function warm({ connectionId, profile = 'default', session } = {}) {
  if (!connectionId || !session) return false
  const ids = await lineageIds(session, connectionId, profile)
  if (ids.incomplete) return false
  const keepHub = hubLinkCache // the focused chat's last good hub answer must survive a warm of another chat
  try {
    const chips = await loadKanban(ids, { connectionId, profile })
    if (!chips || chips.some(c => c.kind === 'kanban-status' || c.stale)) return false
    rememberChips(JSON.stringify([connectionId, profile, session]), chips, undefined)
    return true
  } finally { hubLinkCache = keepHub }
}

function focusScope() {
  const route = focusedRoute()
  return { route, scope: JSON.stringify([route.connectionId || '', route.profile, host.state.focusedStoredSessionId.get()]) }
}

/** Paint the focused chat's last-known cards now (before any network), when focus moved to another chat. */
function paintCached() {
  const runtime = host.state.focusedSessionId.get()
  const stored = host.state.focusedStoredSessionId.get()
  const { route, scope } = focusScope()
  if (!stored || !route.connectionId || scope === snapshot.scope) return false
  const cached = cachedChips(scope)
  if (!cached) {
    // Nothing known yet: never let the previous chat's cards ride along in this chat's first publish.
    kanbanCache = []
    machineCache = []
    return false
  }
  kanbanCache = cached.kanban
  machineCache = cached.machines
  lastKanbanAt = 0
  lastMachinesAt = 0
  snapshot = { runtime, stored, scope, chips: [...kanbanCache, ...machineCache] }
  emit()
  return true
}

export async function refresh() {
  // A refresh for the chat a round is already reading (poll tick, runtime id landing after resume) runs again
  // after it. A switch to another chat starts its own round at once: waiting for the old chat's 2-5 s reads
  // left the new chat's panel empty or last-known that long. The old round's answers are dropped (scope check).
  const pending = focusScope().scope
  if (inflight && inflight.scope === pending) {
    rerun = true
    return
  }
  if (typeof document !== 'undefined' && document.hidden) return
  const me = { scope: pending }
  inflight = me
  try {
    const runtime = host.state.focusedSessionId.get()
    const stored = host.state.focusedStoredSessionId.get()
    const { route, scope } = focusScope()
    // Kanban and machine answers depend on the stored chat only, so a runtime id that lands mid-round (resume)
    // must not throw them away; only process/sub-agent chips belong to one runtime session.
    const stillFocused = () => focusScope().scope === scope
    if (!runtime && !stored) {
      snapshot = { runtime, stored, chips: [] }
      return emit()
    }
    if (!route.connectionId) {
      lastKanbanAt = 0
      lastMachinesAt = 0
      snapshot = {
        runtime, stored, scope,
        chips: [{
          key: `owner-status:${scope}`, kind: 'kanban-status', state: 'unknown', stale: true,
          label: 'Session owner unavailable', title: 'Could not resolve the session owner; work may still be unfinished.'
        }]
      }
      return emit()
    }
    paintCached() // a switch paints the cached cards before anything below waits on the network
    const sameSession = scope === snapshot.scope && snapshot.fresh === true
    let procs = null
    let subagents = null
    // Each source publishes as soon as it lands: kanban never waits on the process/subagent RPCs or the
    // machine inventory, and vice versa.
    const publish = () => {
      if (!stillFocused()) return
      const sameRuntime = host.state.focusedSessionId.get() === runtime
      snapshot = {
        runtime: host.state.focusedSessionId.get(), stored, scope, fresh: true,
        chips: [...kanbanCache, ...machineCache, ...(sameRuntime ? [...subagentChips(subagents?.subagents), ...processChips(procs?.processes),
          ...(!procs && runtime ? [{ key: 'process-status', kind: 'kanban-status', state: 'unknown', label: 'Process status unknown', title: 'Process source unavailable or not read yet.' }] : []),
          ...(!subagents && runtime ? [{ key: 'subagent-status', kind: 'kanban-status', state: 'unknown', label: 'Subagent status unknown', title: 'Subagent source unavailable or not read yet.' }] : [])] : [])]
      }
      emit()
    }
    const rpc = runtime
      ? Promise.all([
          safe(() => host.request('process.list', { session_id: runtime }), null),
          safe(() => host.request('subagent.list', { session_id: runtime }), null)
        ]).then(([p, sa]) => {
          procs = p
          subagents = sa
          publish()
        })
      : Promise.resolve()
    const kanbanDue = stored && (!sameSession || Date.now() - lastKanbanAt >= KANBAN_POLL_MS)
    const machinesDue = stored && (!sameSession || Date.now() - lastMachinesAt >= MACHINE_POLL_MS)
    if (kanbanDue || machinesDue) {
      const ids = await lineageIds(stored, route.connectionId, route.profile)
      if (ids.unstored) {
        // Nothing can be linked to a chat that isn't stored yet: show no chips (no flashing
        // "lineage unavailable" panel on every new chat) and re-read ancestry next poll.
        await rpc
        if (!stillFocused()) return
        lastKanbanAt = 0
        lastMachinesAt = 0
        kanbanCache = []
        machineCache = []
        return publish()
      }
      if (ids.incomplete) {
        await rpc
        if (!stillFocused()) return
        // Never turn a partial ancestry read into an authoritative empty answer.
        // Keep only this owner's previous chips and retry ancestry next poll.
        lastKanbanAt = 0
        lastMachinesAt = 0
        snapshot = {
          runtime, stored, scope,
          chips: [...staleChips(snapshot.scope === scope ? snapshot.chips : []).filter(c => c.key !== `lineage-status:${scope}`), {
            key: `lineage-status:${scope}`, kind: 'kanban-status', state: 'unknown', stale: true,
            label: 'Session lineage unavailable', title: 'Could not read full session ancestry; work may still be unfinished.'
          }]
        }
        emit()
        return
      }
      const kanban = kanbanDue
        ? ((lastKanbanAt = Date.now()), loadKanban(ids, route)).then(kChips => {
            if (!stillFocused()) return
            // null = fetch failed: keep this chat's last good list (cached or live), never the previous chat's.
            if (kChips) {
              kanbanCache = kChips
              rememberChips(scope, kChips, undefined)
            }
            publish()
          })
        : null
      const machines = machinesDue
        ? ((lastMachinesAt = Date.now()), loadMachines(ids)).then(mChips => {
            if (!stillFocused()) return
            if (mChips) {
              machineCache = mChips
              rememberChips(scope, undefined, mChips)
            }
            publish()
          })
        : null
      await Promise.all([kanban, machines, rpc])
    } else {
      await rpc
    }
    if (!stored && stillFocused()) {
      kanbanCache = []
      machineCache = []
    }
    publish()
  } catch (e) {
    log('refresh failed', e)
  } finally {
    if (inflight === me) {
      inflight = null
      if (rerun) {
        rerun = false
        void refresh()
      }
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

const KIND_GLYPH = { bg: 'codicon-server-process', sub: 'codicon-agent', kanban: 'codicon-tasklist', 'kanban-status': 'codicon-warning', machine: 'codicon-vm' }

export function openTaskChip(chip) {
  return openQualifiedTask(chip)
}

// An older producer (no observations array at all) cannot prove live or queued card work: such a card renders
// explicit unknown with its last known state, never the running pulse. Its waits and asks keep their look.
const LEGACY_LIVE = new Set(['running', 'review', 'ready', 'todo'])
const legacyLiveCard = c => c.kind === 'kanban' && !Array.isArray(c.observations) && c.look !== 'needs-you' && LEGACY_LIVE.has(str(c.state))

export function Chip({ chip: rawChip, now }) {
  const ev = ['bg', 'sub'].includes(rawChip.kind) ? chipObservation(rawChip, now / 1000) : null
  const chip = ev
    ? { ...rawChip, state: ev.state, title: `${rawChip.title}\n${observationLine(ev, now / 1000)}` }
    : legacyLiveCard(rawChip)
      ? { ...rawChip, state: 'unknown', title: `${rawChip.title}\n${rawChip.replica ? `status ${observationLine(unobservedView(rawChip, now / 1000), now / 1000)}` : `status unknown (producer sent no observations) · last known: ${rawChip.state}`}` }
      : rawChip
  const color = chipColor(chip.look || chip.state)
  const showMachine = chip.kind === 'machine' && typeof window.__computeRentalsShow === 'function'
  const taskClickable = chip.kind === 'kanban' && !!chip.connectionId && !!chip.profile
  const clickable = taskClickable || showMachine
  const onClick = taskClickable
    ? () => openTaskChip(chip)
    : showMachine
      ? () => window.__computeRentalsShow(chip.id)
      : undefined
  const age = elapsed(chip.since, now)
  return jsxs(clickable ? 'button' : 'span', {
    type: clickable ? 'button' : undefined,
    onClick,
    title: chip.title,
    'data-session-task': chip.kind,
    style: {
      ...(chip.stale || chip.gateway ? { opacity: chip.stale ? 0.5 : 0.75 } : {}),
      display: 'inline-flex',
      alignItems: 'center',
      gap: 5,
      maxWidth: 280,
      minHeight: clickable ? 44 : undefined,
      padding: '1px 8px',
      borderRadius: 999,
      ...chipFrame(chip.look || chip.state),
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
      onHub(chip) && jsx('span', { 'data-on-hub': '', style: { color, fontSize: 10, fontWeight: 600, flex: 'none' }, children: '[H]' }),
      jsx('i', { 'aria-hidden': true, className: `codicon ${KIND_GLYPH[chip.kind]}`, style: { fontSize: 12, flex: 'none', opacity: 0.8 } }),
      chip.kind === 'kanban' && jsx('span', { style: { opacity: 0.7, fontFamily: 'var(--font-mono, ui-monospace, monospace)', fontSize: 10.5 }, children: chip.id }),
      jsx('span', { style: { overflow: 'hidden', textOverflow: 'ellipsis', fontSize: 11 }, children: truncate(chip.label) }),
      chip.extra && jsx('span', { style: { opacity: 0.7, fontSize: 10.5, fontVariantNumeric: 'tabular-nums' }, children: chip.extra }),
      chip.state !== 'running' && jsx('span', { style: { color, fontSize: 10.5 }, children: chip.look === 'stalled' ? 'stalled' : chip.state }),
      age && jsx('span', { style: { opacity: 0.6, fontSize: 10.5, fontVariantNumeric: 'tabular-nums' }, children: age })
    ]
  })
}

export function subscribeDecisionChanges(fn, target = window) {
  // Subscribe to the stable window, not a replaceable plugin incarnation.
  target.addEventListener('hermes:user-steps-changed', fn)
  return () => target.removeEventListener('hermes:user-steps-changed', fn)
}

const ACTIVITY_LABEL = {
  claimed: 'claimed', completed: 'completed', blocked: 'blocked', unblocked: 'unblocked', review_requested: 'review requested',
  commented: 'comment', scheduled: 'scheduled', continued: 'checkpointed', created: 'created', assigned: 'assigned',
  reclaimed: 'reclaimed', crashed: 'crashed', timed_out: 'timed out', heartbeat: 'working'
}

/** One linked card as a readable row (exported for tests): status, full title, who, what happened last, PR. */
export function cardRowModel(chip, now = Date.now()) {
  const activity = chip.activity ? (ACTIVITY_LABEL[chip.activity] || chip.activity.replace(/_/g, ' ')) : ''
  const age = elapsed(chip.activityAt || chip.since, now)
  const pr = chip.prUrl ? chip.prUrl.match(/\/pull\/(\d+)$/) : null
  return {
    status: chip.look === 'stalled' ? 'stalled' : legacyLiveCard(chip) ? 'unknown' : chip.state,
    onHub: onHub(chip),
    look: chip.look || null,
    wait: waitLine(chip, now),
    title: chip.label,
    meta: [chip.assignee, chip.gateway ? `on ${chip.gateway}` : '', activity && age ? `${activity} ${age} ago` : activity || (age ? `${age}` : '')]
      .filter(Boolean).join(' · '),
    note: chip.note || '',
    evidence: chipEvidenceLine(chip, now / 1000),
    prLabel: pr ? `PR #${pr[1]}` : chip.prUrl ? 'PR' : '',
    prUrl: chip.prUrl || null
  }
}

function CardRow({ chip, now }) {
  const m = cardRowModel(chip, now)
  const color = chipColor(chip.look || (m.status === 'unknown' ? 'unknown' : chip.state))
  const clickable = !!chip.connectionId && !!chip.profile
  return jsxs(clickable ? 'button' : 'div', {
    type: clickable ? 'button' : undefined,
    onClick: clickable ? () => openTaskChip(chip) : undefined,
    title: chip.title,
    'data-session-task': 'kanban-row',
    'data-wait-look': chip.look || undefined,
    style: {
      display: 'grid', gridTemplateColumns: 'auto 1fr auto', columnGap: 8, alignItems: 'start', width: '100%',
      textAlign: 'left', padding: '5px 8px', borderRadius: 8, border: '1px solid color-mix(in srgb, var(--border, #8884) 70%, transparent)',
      background: 'color-mix(in srgb, var(--card, transparent) 70%, transparent)', color: 'var(--foreground, inherit)',
      cursor: clickable ? 'pointer' : 'default', opacity: chip.stale ? 0.6 : 1, fontFamily: 'inherit'
    },
    children: [
      jsx('span', {
        className: chip.state === 'running' && m.status !== 'unknown' ? 'session-tasks-pulse' : undefined,
        style: {
          fontSize: 10.5, lineHeight: '16px', padding: '0 6px', borderRadius: 999, whiteSpace: 'nowrap', marginTop: 1,
          color, border: `1px solid color-mix(in srgb, ${color} 55%, transparent)`, background: `color-mix(in srgb, ${color} 12%, transparent)`
        },
        'data-on-hub': m.onHub ? '' : undefined,
        children: m.onHub ? `[H] ${m.status}` : m.status
      }),
      jsxs('span', {
        style: { minWidth: 0, display: 'flex', flexDirection: 'column', gap: 1 },
        children: [
          jsx('span', {
            style: { fontSize: 12, lineHeight: '16px', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' },
            children: m.title
          }),
          m.wait && jsx('span', {
            'data-session-task-wait': chip.look || 'waiting',
            style: { fontSize: 11, lineHeight: '15px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', color: chip.look === 'stalled' ? color : undefined, opacity: chip.look === 'stalled' ? 1 : 0.7 },
            children: m.wait
          }),
          jsx('span', {
            'data-session-task-evidence': '',
            style: { fontSize: 11, lineHeight: '15px', opacity: 0.7, overflowWrap: 'anywhere' },
            children: `evidence: ${m.evidence}`
          }),
          (m.meta || m.note) && jsx('span', {
            style: { fontSize: 11, lineHeight: '15px', opacity: 0.7, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
            children: [m.meta, m.note].filter(Boolean).join(' — ')
          })
        ]
      }),
      m.prUrl
        ? jsx('a', {
            href: m.prUrl, target: '_blank', rel: 'noreferrer', onClick: e => e.stopPropagation(),
            style: { fontSize: 11, lineHeight: '16px', whiteSpace: 'nowrap', color: 'var(--accent, #3b82f6)' },
            children: m.prLabel
          })
        : jsx('span', {})
    ]
  })
}

function Strip() {
  const [snap, setSnap] = useState(snapshot)
  const [now, setNow] = useState(Date.now())
  const [, setQueueTick] = useState(0)
  useEffect(() => subscribeDecisionChanges(() => setQueueTick(t => t + 1)), [])
  const focused = useValue(host.state.focusedSessionId)
  const stored = useValue(host.state.focusedStoredSessionId)
  // Subscribe to owner and ambient fallback changes, not just the runtime id.
  // Older hosts have no owner atom; their connection atom is a stable fallback.
  useValue(host.state.focusedSessionOwner || host.state.connectionId)
  useValue(host.state.connectionId)
  useValue(host.state.profile || host.state.connectionId)
  const route = focusedRoute()
  const scope = JSON.stringify([route.connectionId || '', route.profile, stored])
  useEffect(() => subscribe(next => (setSnap(next), setNow(Date.now()))), [])
  useEffect(() => void refresh(), [focused, scope])
  const chips = snap.runtime === focused && snap.scope === scope ? progressChips(snap.chips) : []
  // The session panel shows this work in its own sections; the strip stays subscribed so polling keeps running.
  if (!chips.length || (typeof window !== 'undefined' && window.__sessionPanel?.active)) return null
  const cards = chips.filter(c => c.kind === 'kanban')
  const others = chips.filter(c => c.kind !== 'kanban')
  return jsxs('details', {
    'data-slot': 'session-tasks',
    style: { width: '100%', minWidth: 0, marginBottom: 2 },
    children: [
      jsx('summary', { style: { minHeight: 44, cursor: 'pointer' }, children: progressSummary(chips) }),
      jsx('div', {
        style: { display: 'flex', flexWrap: 'wrap', gap: 4 },
        children: [
          ...others.map(chip => jsx(Chip, { chip, now }, chip.key)),
          ...cards.map(chip => jsx(CardRow, { chip, now }, chip.key)) // full-width rows below the chips
        ]
      })
    ]
  })
}

/** The collapsed strip's one line: the count plus the most urgent card, so cards are readable without opening it. */
export function progressSummary(chips) {
  const base = `Agent progress (${chips.length}) — no decision required`
  const top = (chips || []).find(c => c.kind === 'kanban')
  const stalled = (chips || []).filter(c => c.kind === 'kanban' && c.look === 'stalled').length
  const head = stalled ? `${base} · ${stalled} stalled` : base
  return top ? `${head} · ${top.look === 'stalled' ? 'stalled' : top.state}: ${top.label}` : head
}

export function progressChips(chips, lookup = source => window.__userSteps?.requestForSource?.(source)) {
  return (chips || []).filter(chip => {
    if (chip.kind !== 'kanban') return true
    const request = lookup({ gatewayId: chip.gatewayId || chip.gateway, profile: chip.profile, type: 'kanban', board: chip.board, id: chip.id })
    // Retain genuine execution after authorization, not a second blocked decision chip.
    return !request || ['running','ready','scheduled','review'].includes(chip.state)
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
    // Hosts with the fork's archive confirmation ask before archiving a chat
    // that still holds rented machines (Kanban tasks are core's own check).
    ctx.registerArchiveGuard?.(archiveGuard)
    const onVis = () => document.hidden || void refresh()
    document.addEventListener('visibilitychange', onVis)
    window.__sessionTasks = { version: 'wait-visible-v1', get snapshot() { return snapshot }, refresh, subscribe, openTaskChip, lineageIds, focusedRoute, warm }
    return () => {
      document.removeEventListener('visibilitychange', onVis)
      document.getElementById(`${ID}-style`)?.remove()
      delete window.__sessionTasks
      if (timer) clearInterval(timer)
      timer = null
    }
  }
}

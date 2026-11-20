/**
 * Session Panel — one panel above the composer that answers, for the focused chat:
 *   is work moving, how much is left, what is waiting on me, and what is stuck.
 *
 *   header       agent replying in this chat / idle since …, then one small box per kanban card and the counts:
 *                needs you · stuck · running · ready · waiting · not started · done
 *                colours: red = needs you, green = ready, yellow = not ready (waiting / not started), grey = done,
 *                blue = running, orange = stuck
 *   Needs you    user-steps' decision cards (window.__userSteps.ActionQueue, embedded) and its earlier asks; a card
 *                no ask covers renders its own human_request (askView/AskRow): label + plain title, the
 *                generate-unblock-card Situation (2 lines, More), one tile per option (letter, name, detail; the
 *                recommended one highlighted with its reason) whose Choose button drafts the answer in the composer,
 *                "Only you can: …", and everything else folded under Details. A card without options shows its step
 *                with I did it / Skip it drafts; without a request it says so and offers Ask agent
 *   Stuck        a running card with no event for 2 h, a parked card whose agent-owned wait stalled
 *                (backend `wait.wait_state === 'stalled'`), or a sub-agent whose tool count has not moved
 *                for 30 min; each with numbered steps and Nudge agent / Open card. A stale chip (session-tasks'
 *                last-known copy after a failed read) is never stuck or needs-you: it lists under Waiting as
 *                "status unknown", since the card may have been answered or archived since
 *   In progress  running and in-review cards, sub-agents, background processes, rented machines
 *   Waiting      parked cards and what they wait on; folded by default to counts (on parent / on schedule /
 *                unknown), opened per chat
 *   footer       open ready/not-yet-started cards, then a per-chat finished-history disclosure
 * collapsed    the header line alone (caret + counts, needs-you in red) plus one line per waiting card (up to 3);
 *                the header is the composer status stack's own disclosure row (caret, icon, label), so this panel
 *                folds the same way the task list above it does
 *   hidden       when there is neither open work nor finished history
 *
 * Data: session-tasks' snapshot (window.__sessionTasks: cards, sub-agents, processes, machines; it keeps polling),
 * user-steps' asks (window.__userSteps), and finished cards from user-steps' /session-cards (every 30 s; core's
 * list has no finished cards). While this plugin is loaded, session-tasks and user-steps hide their own strips
 * (they check window.__sessionPanel.active). Nudge only drafts a message in the composer; the user sends it.
 */
import { Codicon, COMPOSER_AREAS, DisclosureCaret, host, useValue } from '@hermes/plugin-sdk'
import * as SDK from '@hermes/plugin-sdk' // namespace: the optional projector exports are absent on older hosts
import { useEffect, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'session-panel'
const VERSION = 'session-panel-v12-owner-wake'
export const STUCK_CARD_S = 2 * 3600 // running card with no event this long
export const STUCK_SUB_MS = 30 * 60_000 // sub-agent with no new tool call this long
const DONE_POLL_MS = 30_000
const WAIT_LINES_COLLAPSED = 3 // waiting cards named while collapsed; the rest count as "+N more"
const COLLAPSED_KEY = 'hermes.sessionPanel.collapsed.v1'

const str = v => (typeof v === 'string' ? v : v == null ? '' : String(v))
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

/** No observations array: a hermes-hub replica row (not source evidence; ages from the card's own last activity,
 *  never `synced_at`) or an older producer. Mirrors session-tasks' `unobservedView`. */
const REPLICA_REASON = 'hub_replica_not_source_evidence'
function unobservedView(chip, nowSec) {
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
const LEGACY_LIVE = new Set(['running', 'review', 'ready', 'todo'])
const NO_OBSERVATIONS = 'producer_sent_no_observations'
const legacyLive = (c, ev) => (ev?.reason === NO_OBSERVATIONS || ev?.reason === REPLICA_REASON) && c?.kind === 'kanban' && c.look !== 'needs-you' && LEGACY_LIVE.has(str(c.state))
export function classify(chips, { nowMs = Date.now(), silence = new Map(), isAsked = () => false, presentationFor = () => null, evidence = () => null } = {}) {
  const now = nowMs / 1000
  const out = { active: [], stuck: [], blockedUpstream: [], waiting: [], ready: [], queued: [], unasked: [], reported: [], lastActivity: 0 }
  const seen = at => { if (Number(at) > out.lastActivity) out.lastActivity = Number(at) }
  for (const c of chips || []) {
    if (!c || !c.key) continue
    if (c.kind === 'bg' || c.kind === 'sub') {
      const ev = evidence(c) || chipObservation(c, now)
      const row = { ...item(c), detail: observationLine(ev, now) }
      if (ev.state === 'running') out.active.push(row)
      else out.waiting.push(row)
      if (ev.activity) seen(now - ev.activity.ageSec)
      continue
    }
    if (c.kind === 'machine') { out.active.push({ ...item(c), detail: 'rented machine' }); continue }
    if (c.kind === 'kanban-status') { out.waiting.push({ ...item(c), detail: str(c.title) || 'status unknown' }); continue }
    if (c.kind !== 'kanban') continue
    seen(c.activityAt)
    // Progress = the newest event, else the current run's start (session-tasks `progressAt`); 0 = unknown. Only a
    // known progress time can make a card Stuck: `since` (first claim / creation) is display only, or a hub card
    // claimed a minute ago read "no progress since <a week ago>" (kanban-f241d1f60b).
    const progress = Number(c.activityAt) || Number(c.progressAt) || 0
    const last = progress || Number(c.since) || 0
    const on = waitPhrase(c, nowMs)
    // A stale chip is session-tasks' last-known copy after a failed read: the card may have been answered,
    // re-blocked or archived since, so it is never stuck (no nudge) or needs-you, only "status unknown".
    if (c.stale) { out.waiting.push({ ...item(c), detail: `status unknown · last known: ${c.look === 'needs-you' ? 'needs you' : c.state || 'unknown'}${last ? ` (${ago(last, nowMs)})` : ''}; the card could not be re-read` }); continue }
    // A closed card on another gateway that its owner never confirmed to this chat (session-tasks `reported`): say
    // what the owner reported, in its own group. It is not waiting on anything and asks nothing, and it is never
    // counted as done. Chat 20261110_185031_4f5f4b listed 198 such cards of retired workers under Waiting as
    // "status unknown (missing qualified identity)" four days after they finished.
    if (c.state === 'unknown' && (c.reported === 'done' || c.reported === 'archived')) {
      out.reported.push({ ...item(c), detail: `reported ${c.reported} by ${c.gateway || c.gatewayId || 'another gateway'} · not confirmed by its owner` })
      continue
    }
    if (c.look === 'needs-you') {
      // A finished, archived or cancelled card asks nothing, whatever wait it last reported.
      if (!TERMINAL.has(str(c.state))) {
        if (!isAsked(c)) out.unasked.push({ ...item(c), detail: on || 'needs you' })
        else {
          const presentation = presentationFor(c)
          // Retiring an ask does not settle its still-blocked owner. Keep status discoverable without
          // reviving a decision or inferring authorization; older bridges retain their existing dedup.
          if (c.state === 'blocked' && presentation?.representedInQueue === false) {
            out.waiting.push({ ...item(c), cardStatus: true,
              detail: `${c.gatewayId || c.gateway || '?'} / ${c.profile || '?'} / ${c.board || 'default'} / ${c.id} · blocked · request ${presentation.dismissed ? 'dismissed' : presentation.state}` })
          }
        }
      }
      continue
    }
    // Finished cards come from the Done list (/session-cards); a terminal chip here is not waiting on anything.
    if (TERMINAL.has(str(c.state))) continue
    // Source evidence that exists but is expired/incomplete/mismatched makes the card's status unknown, whatever the
    // legacy fields still say (a cached running card must not read as running). An older producer (no
    // observations array at all) cannot prove live or queued work either: its running/review/ready/todo card is
    // explicit unknown with its last known state; its waits and asks keep their sections.
    if (c.blockedUpstream && c.look !== 'stalled') {
      const bu = c.blockedUpstream, auth = bu.contained?.authority
      const detail = bu.contained
        ? `waiting on contained card ${bu.id} · release by ${auth?.id ? `card ${auth.id}` : 'unknown'} (${auth?.status || 'unknown'})`
        : `waiting on blocked parent card ${bu.id} (${bu.status})`
      out.blockedUpstream.push({ ...item(c), blockedUpstreamRow: true, detail })
      continue
    }
    const ev = evidence(c)
    // A parked card has a board lifecycle but need not have an execution generation.
    // Keep the independent execution verdict below (including unknown progress); never
    // turn a recent list read into a heartbeat or claim a running worker is alive.
    const board = c.boardStatus
    // The upstream leaf only when the wait text does not already name that card.
    const leaf = c.upstream?.id && !new RegExp(`\\b${String(c.upstream.id).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`).test(on) ? ` → ${c.upstream.id} ${c.upstream.status || 'unknown'}` : ''
    // A stalled agent-owned wait is the board's own verdict (`wait.wait_state`), not execution evidence: it is Stuck
    // whatever the run evidence says (a fresh parked read used to take it to Waiting first, so it never showed).
    if (c.look === 'stalled') { out.stuck.push({ ...item(c), reason: 'stalled', detail: `stalled: ${on || 'agent-owned wait over 2 h'}` }); continue }
    if ((!ev || ev.state === 'unknown') && board && board.state === c.state && ['scheduled', 'blocked', 'triage', 'review', 'ready', 'todo'].includes(c.state) &&
        Number.isFinite(board.readAt) && nowMs >= board.readAt && nowMs - board.readAt <= 60_000) {
      // A parked card has no run to observe: its fresh board lifecycle is the answer, so its unknown execution
      // evidence is not counted (`parked`). The row leads with what it waits on; the source comes last.
      const source = board.source === 'replica' ? 'reported board status' : 'board status'
      if (c.state === 'ready') { out.ready.push({ ...item(c), parked: true, detail: `ready · waiting for a worker to claim it · ${source}` }); continue }
      if (c.state === 'todo' && !c.waitingOn && !c.upstream?.id) { out.queued.push({ ...item(c), parked: true, detail: `not started · ${source}` }); continue }
      // The upstream leaf follows a published wait only: without one, parkedWaitText already names the upstream.
      const text = parkedWaitText(c, on, nowMs)
      out.waiting.push({ ...item(c), parked: true, detail: `${text}${on && text === on ? leaf : ''} · ${source}` })
      continue
    }
    if (board && (!ev || ev.state === 'unknown') || ev?.sourced && ev.state === 'unknown' || legacyLive(c, ev)) {
      out.waiting.push({ ...item(c), detail: `status unknown${ev?.reason ? ` (${ev.reason.replace(/_/g, ' ')})` : ''} · last known: ${ev?.lastKnown || c.state || 'unknown'}${ev?.ageSec != null ? ` (observed ${span(ev.ageSec)} ago)` : ''}` })
      continue
    }
    if (c.state === 'running') {
      if (progress && now - progress >= STUCK_CARD_S) out.stuck.push({ ...item(c), reason: 'no-progress', lastAt: progress, detail: `running, no progress since ${clock(progress, nowMs)} (${ago(progress, nowMs)})${c.activity ? ` · last event: ${c.activity}` : ''}` })
      else out.active.push({ ...item(c), detail: `running${c.assignee ? ` · ${c.assignee}` : ''}${progress ? ` · ${ago(progress, nowMs)}` : ' · last event unknown'}` })
      continue
    }
    if (c.state === 'review') { out.active.push({ ...item(c), detail: `in review${last ? ` · ${ago(last, nowMs)}` : ''}` }); continue }
    if (c.state === 'ready') { out.ready.push({ ...item(c), detail: 'ready to start' }); continue }
    if (c.state === 'todo') { out.queued.push({ ...item(c), detail: 'not started' }); continue }
    out.waiting.push({ ...item(c), detail: (on || (c.state === 'scheduled' ? 'scheduled' : c.state === 'unknown' ? 'status unknown' : c.state)) + leaf })
  }
  return out
}

// ── wait wording ───────────────────────────────────────────────────────────
// [daveey 2026-11-20] "waiting on card "Title Of Card" (t_xxx)", click to open; "waiting for 10m (until
// 2026-11-23 00:00 PST)", always Pacific time. Durations are short (10m, 3h 20m, 2d 4h).
export function shortDur(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60), h = Math.floor(m / 60), d = Math.floor(h / 24)
  if (d) return h % 24 ? `${d}d ${h % 24}h` : `${d}d`
  if (h) return m % 60 ? `${h}h ${m % 60}m` : `${h}h`
  return `${m}m`
}

const PT = typeof Intl !== 'undefined' ? new Intl.DateTimeFormat('en-US', { timeZone: 'America/Los_Angeles', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZoneName: 'short' }) : null
/** `2026-11-23 00:00 PST` (Pacific wall clock; PDT during daylight time). */
export function pacific(sec) {
  if (!PT || !Number.isFinite(Number(sec))) return ''
  const p = Object.fromEntries(PT.formatToParts(new Date(Number(sec) * 1000)).map(x => [x.type, x.value]))
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute} ${p.timeZoneName}`
}

/** A wake this late is flagged: past dispatcher latency, something that should have woken it did not. */
export const OVERDUE_FLAG_S = 30 * 60

/** A card's published wait in plain words: how long it has waited, how long is left, and how late it is.
 *  Core's clock wait ("the clock until 2026-11-23 08:00 UTC (why)") reads
 *  `waiting 2h · 8h 10m left (until 2026-11-23 00:00 PST)`, past due `waiting 1d 2h · overdue by 5m (was due …)`,
 *  and from OVERDUE_FLAG_S late `waiting 1d 2h · ⚠ overdue by 3h (was due …)`; any other wait
 *  `waiting on <what> for 10m`. '' = none. */
export function waitPhrase(c, nowMs = Date.now()) {
  const w = str(c?.waitingOn).trim()
  if (!w) return ''
  const now = nowMs / 1000, since = Number(c?.waitingSince)
  const waited = since > 0 && since <= now ? `waiting ${shortDur(now - since)} · ` : ''
  const m = /^the clock until (\d{4}-\d\d-\d\d) (\d\d:\d\d) UTC\b/.exec(w)
  if (m) {
    const at = Date.parse(`${m[1]}T${m[2]}:00Z`) / 1000
    if (Number.isFinite(at)) {
      if (at >= now) return `${waited}${shortDur(at - now)} left (until ${pacific(at)})`
      return `${waited}${now - at >= OVERDUE_FLAG_S ? '⚠ ' : ''}overdue by ${shortDur(now - at)} (was due ${pacific(at)})`
    }
  }
  return `waiting on ${w}${since > 0 ? ` for ${shortDur(now - since)}` : ''}`
}

// Card titles for ids named in wait text: this chat's own cards first, else one read of the card on the waiting
// card's owner route, or the chat's own route when the card has none (a replica row of another gateway: that
// gateway's board replica answers the read). The waiting card's board first, then `default` (a wait may name a
// card on another board). Cached per route; a failed read is retried after TITLE_RETRY_MS. Resolved titles
// re-render the panel.
const TITLE_RETRY_MS = 5 * 60_000
const TITLE_PENDING_MS = 30_000 // a read that never answered (bridge ignored its timeout) may be retried
const TITLE_FRESH_MS = 30 * 60_000 // a fetched title is re-read after this (cards get renamed)
const TITLE_MAX = 500
const titles = new Map()
const titleSubs = new Set()
/** Where a card named in `chip`'s wait text is read and opened: the chip's owner route, else the chat's. */
export function refRoute(chip, fallback = () => panelRoute()) {
  if (chip?.connectionId) return { connectionId: chip.connectionId, profile: str(chip.profile) || 'default', board: str(chip.board) || 'default' }
  const r = (() => { try { return fallback() } catch { return null } })()
  return { connectionId: r?.connectionId || null, profile: str(r?.profile) || str(chip?.profile) || 'default', board: str(chip?.board) || 'default' }
}
const titleKey = (r, id) => [str(r.connectionId), r.profile, id].join('|')
export function noteTitles(chips) {
  for (const c of chips || []) {
    if (c?.kind !== 'kanban' || !c.id || !c.connectionId || !str(c.label) || c.label === c.id) continue
    const k = titleKey(refRoute(c), c.id)
    titles.delete(k) // re-insert: Map order is recency, so eviction drops the least recently seen
    titles.set(k, { title: str(c.label), board: str(c.board) || 'default', state: str(c.state), at: Date.now() })
  }
  while (titles.size > TITLE_MAX) titles.delete(titles.keys().next().value)
}
export const subscribeTitles = fn => (titleSubs.add(fn), () => titleSubs.delete(fn))
/** One title read: `{ title, board }`, title '' when no board on that route has the card. */
// [daveey 2026-11-20] "no wake published - not acceptable". A parked card whose list row carries no wait still has a
// real wake on its owner: the newest park event (`continued` / `scheduled`: wake_at, declared awaiting) and an armed
// wake check. When the row has none, the panel reads the card once from its owner (the same read as titles) and shows
// that wake. Cached per card: a park is re-read after PARK_FRESH_MS, a failed read after PARK_RETRY_MS.
const PARK_FRESH_MS = 60_000, PARK_RETRY_MS = 2 * 60_000, PARK_PENDING_MS = 30_000
const PARK_EVENTS = new Set(['continued', 'scheduled'])
const parks = new Map()
// Core's own constants (hermes_cli/kanban_continuation.py, kanban_timed_park.py); the panel reads the park the way
// park_state / implied_wake / blocks_for_human do, so it never shows a wake the dispatcher does not hold.
const DEFAULT_WAKE_S = 24 * 3600, MIN_WAKE_S = 60, MAX_WAKE_S = 14 * 24 * 3600, SUPERSEDED_GRACE_S = 3600
const HUMAN_BLOCK_KINDS = new Set(['needs_input', 'capability'])
const evId = e => Number(e?.id ?? e?.created_at) || 0
const payloadOf = e => (e?.payload && typeof e.payload === 'object' ? e.payload : (() => { try { return JSON.parse(e?.payload || '{}') || {} } catch { return {} } })())
/** implied_wake: the payload's wake_at, else its reason's SCHEDULED_UNTIL= marker clamped to [park+60 s, park+14 d],
 *  else 24 h after the park. */
export function impliedWake(p, parkedAt) {
  if (Number(p?.wake_at) > 0) return Number(p.wake_at)
  const raw = /SCHEDULED_UNTIL=(\S+)/.exec(str(p?.reason))?.[1]?.replace(/[.,;:)\]}'"]+$/, '')
  // An offset is honoured; a naive stamp is read as local time, as core's datetime.fromisoformat().timestamp() does.
  // A date-only stamp is local midnight too (Date.parse alone would read it as UTC).
  const at = raw ? Date.parse(/^\d{4}-\d\d-\d\d$/.test(raw) ? `${raw}T00:00` : raw) / 1000 : NaN
  if (Number.isFinite(at)) return Math.max(parkedAt + MIN_WAKE_S, Math.min(Math.floor(at), parkedAt + MAX_WAKE_S))
  return parkedAt + DEFAULT_WAKE_S
}
/** The park in a card read (`/api/plugins/kanban/tasks/<id>`), read like core's park_state, or null when the card is
 *  not `scheduled` (core only wakes scheduled cards; a leftover probe on a blocked card wakes nothing).
 *  `{ wakeAt, awaiting, check, nextProbeAt, continued, parkedAt, superseded, forHuman }`:
 *  - the newest `continued`/`scheduled` event, unless spent (a later `continuation_woken`, or a later
 *    `claimed`/`spawned` of another run): then `superseded`, its wake a backstop one hour after the last event;
 *  - `wakeAt` = implied_wake (wake_at, else the SCHEDULED_UNTIL marker, else 24 h after the park);
 *  - `forHuman` when the park has no timed wake and a human block kind (the dispatcher blocks it for a person). */
export function parkFromDetail(detail) {
  if (str(detail?.task?.status) !== 'scheduled') return null
  const events = (Array.isArray(detail?.events) ? detail.events : []).slice().sort((a, b) => evId(a) - evId(b))
  let ev = null
  for (const e of events) if (PARK_EVENTS.has(e?.kind)) ev = e
  const wc = detail?.task?.wake_check
  const check = wc && wc.purpose !== 'ask_resolved' ? str(wc.cmd) : ''
  const lastAt = Math.max(0, ...events.map(e => Number(e?.created_at) || 0))
  const spent = !!ev && events.some(e => evId(e) > evId(ev) && (e.kind === 'continuation_woken' ||
    (['claimed', 'spawned'].includes(e.kind) && (e.run_id == null || ev.run_id == null || e.run_id !== ev.run_id))))
  if (!ev || spent) {
    if (!check && !ev && !lastAt) return null
    const parkedAt = lastAt || Math.floor(Date.now() / 1000)
    return { wakeAt: ev ? parkedAt + SUPERSEDED_GRACE_S : parkedAt + DEFAULT_WAKE_S, awaiting: null, check, nextProbeAt: Number(wc?.next_at) > 0 ? Number(wc.next_at) : null,
      // No park event at all (core: untimed, not superseded): a person block kind is blocked for a person on the next tick.
      continued: false, parkedAt, superseded: !!ev, forHuman: !ev && HUMAN_BLOCK_KINDS.has(str(detail?.task?.block_kind)) }
  }
  const p = payloadOf(ev)
  const parkedAt = Number(ev.created_at) || lastAt
  const aw = p.awaiting && typeof p.awaiting === 'object' ? p.awaiting : null
  const timed = Number(p.wake_at) > 0
  return {
    wakeAt: impliedWake(p, parkedAt),
    awaiting: aw ? { kind: str(aw.kind), ref: str(aw.ref), why: str(aw.why), ...(aw.inferred ? { inferred: true } : {}) } : null,
    check, nextProbeAt: Number(wc?.next_at) > 0 ? Number(wc.next_at) : null,
    continued: ev.kind === 'continued', parkedAt, superseded: false,
    forHuman: !timed && HUMAN_BLOCK_KINDS.has(str(detail?.task?.block_kind))
  }
}
async function readPark(route, id) {
  const bridge = typeof window !== 'undefined' ? window.hermesDesktop?.api : null
  if (!bridge || !route.connectionId) return { failed: true }
  try {
    const r = await bridge({ path: `/api/plugins/kanban/tasks/${encodeURIComponent(id)}?board=${encodeURIComponent(route.board)}`, connectionId: route.connectionId, profile: route.profile, timeoutMs: 8_000 })
    return { park: parkFromDetail(r) }
  } catch { return { failed: true } }
}
/** `{ state: 'loading' | 'ok' | 'failed', park }` for a parked card; starts at most one read. */
export function parkFor(chip, nowMs = Date.now(), read = readPark, fallback) {
  if (!chip?.id) return { state: 'failed', park: null }
  const route = refRoute(chip, fallback)
  if (!route.connectionId) return { state: 'failed', park: null }
  const k = titleKey(route, chip.id), hit = parks.get(k)
  const age = hit ? nowMs - hit.at : Infinity
  if (hit && (hit.pending ? age < PARK_PENDING_MS : age < (hit.failed ? PARK_RETRY_MS : PARK_FRESH_MS))) {
    return { state: hit.pending && !hit.park ? 'loading' : hit.failed && !hit.park ? 'failed' : hit.stale ? 'stale' : 'ok', park: hit.park || null }
  }
  if (parks.size >= TITLE_MAX) parks.delete(parks.keys().next().value)
  parks.set(k, { ...(hit || {}), at: nowMs, pending: true })
  Promise.resolve(read(route, chip.id)).catch(() => ({ failed: true })).then(r => {
    parks.set(k, { park: r?.failed ? hit?.park || null : r?.park || null, failed: !!r?.failed, stale: !!r?.failed && !!hit?.park, at: Date.now() })
    titleSubs.forEach(fn => fn())
  })
  return { state: hit?.park ? (hit.stale ? 'stale' : 'ok') : 'loading', park: hit?.park || null }
}
/** True when a waiting row's own wait names nothing, so its owner's park is worth reading. */
export const needsPark = i => i?.kind === 'kanban' && !i.cardStatus && str(i.chip?.state) === 'scheduled' &&
  (!str(i.chip?.waitingOn) || /did not publish/i.test(str(i.chip?.waitingOn))) && !i.chip?.upstream?.id && !i.chip?.stale

async function readTitle(route, id) {
  const bridge = typeof window !== 'undefined' ? window.hermesDesktop?.api : null
  if (!bridge || !route.connectionId) return { title: '', board: route.board }
  for (const board of [...new Set([route.board, 'default'])]) {
    try {
      const r = await bridge({ path: `/api/plugins/kanban/tasks/${encodeURIComponent(id)}?board=${encodeURIComponent(board)}`, connectionId: route.connectionId, profile: route.profile, timeoutMs: 8_000 })
      if (str(r?.task?.title)) return { title: str(r.task.title), board }
    } catch {}
  }
  return { title: '', board: route.board }
}
/** `{ title, board }` for card `id` named by `chip`; '' title until (or unless) a read answers. */
export function cardRef(chip, id, nowMs = Date.now(), read = readTitle, fallback) {
  const route = refRoute(chip, fallback)
  if (!route.connectionId) return { route, title: '', board: route.board }
  const k = titleKey(route, id), hit = titles.get(k)
  const age = hit ? nowMs - hit.at : Infinity
  if (hit && (hit.pending ? age < TITLE_PENDING_MS : age < (hit.title ? TITLE_FRESH_MS : TITLE_RETRY_MS))) return { route, title: hit.title, board: hit.board || route.board, state: hit.state || '' }
  if (titles.size >= TITLE_MAX) titles.delete(titles.keys().next().value)
  titles.set(k, { title: hit?.title || '', board: hit?.board || route.board, at: nowMs, pending: true })
  Promise.resolve(read(route, id)).catch(() => ({ title: '' })).then(r => {
    const title = str(r?.title) || hit?.title || ''
    titles.set(k, { title, board: str(r?.board) || hit?.board || route.board, at: Date.now() })
    if (str(r?.title) && r.title !== hit?.title) titleSubs.forEach(fn => fn())
  })
  return { route, title: hit?.title || '', board: hit?.board || route.board }
}

const CARD_ID = /\bt_[0-9a-f]{6,}\b/g
/** Wait text split into plain text and card references (`{ id, title, linkable, open }`, `open` = the qualified
 *  card to open). Without any route (no owner, no chat route) a reference keeps its bare id, unlinked. */
export function waitSegments(text, chip, nowMs = Date.now(), read = readTitle, fallback) {
  const t = str(text), out = []
  let last = 0
  for (const m of t.matchAll(CARD_ID)) {
    if (m.index > last) out.push({ text: t.slice(last, m.index) })
    const ref = cardRef(chip, m[0], nowMs, read, fallback)
    let end = m.index + m[0].length
    // A status right after the id ("t_x (running)") joins the id's own parens: `"T" (t_x, running)`.
    const st = /^ \((triage|todo|ready|running|review|blocked|scheduled|done|archived|cancelled|status unknown|unknown)\)/.exec(t.slice(end))
    if (st) end += st[0].length
    // A card on another gateway ("t_x on worker-a1") or a replica row (no owner connection) is not on a route this
    // Desktop can open it from, as session-tasks also leaves such cards unclickable: titled, not linked.
    const gw = /^ on ([\w.-]+)/.exec(t.slice(end))?.[1]
    const own = [str(chip?.gateway), str(chip?.gatewayId)].filter(g => g && g !== 'local')
    // A local card (gateway '' / 'local') cannot tell its own name from another's: only a known, different name is foreign.
    const foreign = !!gw && own.length > 0 && !['its', 'the', 'hub-sync', ...own].includes(gw)
    const linkable = !!chip?.connectionId && !foreign
    out.push({ id: m[0], title: ref.title, state: ref.state || '', status: st ? st[1] : '', linkable, open: linkable ? { kind: 'kanban', id: m[0], board: ref.board, connectionId: ref.route.connectionId, profile: ref.route.profile } : null })
    last = end
  }
  if (last < t.length) out.push({ text: t.slice(last) })
  return out
}
/** The same text with known titles filled in: `card "Title" (t_x)`. Tooltips use this form. */
const refText = s => (s.title ? `"${s.title}" (${s.id}${s.status ? `, ${s.status}` : ''})` : `${s.id}${s.status ? ` (${s.status})` : ''}`)
export const waitLabel = (text, chip, nowMs, read, fallback) => waitSegments(text, chip, nowMs, read, fallback).map(s => (s.id ? refText(s) : s.text)).join('')

const linkBtn = { padding: 0, border: 0, background: 'transparent', color: 'inherit', font: 'inherit', textDecoration: 'underline', textUnderlineOffset: 2, cursor: 'pointer' }
const OVERDUE_TEXT = /(⚠ overdue by [^(·]+)/
/** A flagged overdue wake stands out (amber, bold); everything else stays muted. */
function overdueSpans(text, idx) {
  if (!OVERDUE_TEXT.test(text)) return text
  return text.split(OVERDUE_TEXT).map((part, j) => (OVERDUE_TEXT.test(part)
    ? jsx('span', { 'data-overdue': '', style: { color: C.stuck, fontWeight: 600 }, children: part.trimEnd() + ' ' }, `o${idx}:${j}`)
    : part))
}
function WaitText({ text, chip, plain }) {
  if (plain || chip?.kind !== 'kanban') return text
  const st = typeof window !== 'undefined' ? window.__sessionTasks : null
  return waitSegments(text, chip).map((s, idx) => {
    if (!s.id) return overdueSpans(s.text, idx)
    const label = refText(s.title && s.title.length > 60 ? { ...s, title: `${s.title.slice(0, 59).trimEnd()}…` } : s)
    if (!s.linkable || typeof st?.openTaskChip !== 'function') return label
    return jsx('button', { type: 'button', 'data-wait-card': s.id, title: `Open card ${s.id}${s.title ? `: ${s.title}` : ''}`, style: linkBtn,
      onClick: e => { e.stopPropagation(); st.openTaskChip(s.open) },
      children: label }, `${s.id}:${idx}`)
  })
}

/** A card in `review` with no running review: say that no reviewer is on it, and what last happened (the newest
 *  event, e.g. `review route deferred 12m ago` when the review provider is walled). The review is a run on the card
 *  itself (core `review_required`), not a separate card. */
export function reviewWaitText(c, nowMs = Date.now()) {
  // Same event words as session-tasks' task rows (`continued` -> `checkpointed`); plain spacing on an older bridge.
  const label = typeof window !== 'undefined' ? window.__sessionTasks?.activityLabel : null
  const kind = (typeof label === 'function' ? str(label(c?.activity)) : str(c?.activity).replace(/_/g, ' ')).trim()
  const at = Number(c?.activityAt)
  return `waiting for a reviewer (none running)${kind ? ` · last: ${kind}${at > 0 ? ` ${shortDur(nowMs / 1000 - at)} ago` : ''}` : ''}`
}

/** What a parked card (fresh board status, no run) waits on, in plain words. `on` is its published wait
 *  ("waiting on …"); without one the row says what its lifecycle state means and that no reason was published,
 *  never a bare state name. A producer's placeholder ("a producer the owner did not publish") is said plainly. */
export function parkedWaitText(c, on, nowMs = Date.now()) {
  const w = str(c?.waitingOn), up = c?.upstream?.id ? `card ${c.upstream.id} (${c.upstream.status || 'status unknown'})` : ''
  const unpublished = !w || /did not publish/i.test(w)
  switch (c?.state) {
    case 'triage': return unpublished || w === 'triage' ? 'in triage · waiting to be specified and promoted' : on
    case 'review': return unpublished ? reviewWaitText(c, nowMs) : on
    case 'todo': return !unpublished ? on : up ? `waiting on ${up}` : 'waiting on its parent cards'
    case 'scheduled': return !unpublished ? on : up ? `scheduled · waiting on ${up}` : 'scheduled · its owner has not published what wakes it'
    case 'blocked': return !unpublished ? on : up ? `blocked · waiting on ${up}` : 'blocked · no reason published'
    default: return on || str(c?.state) || 'status unknown'
  }
}

/** What a Waiting row waits on, for the folded section's counts: `parent` (another card must finish first),
 *  `schedule` (a timer, CI run, deploy or other scheduled wake), `contained`, `triage` (to be specified),
 *  `review`, `blocked` (including a retired ask's still-blocked owner), `unknown` (its status could not be read).
 *  There is no catch-all bucket: "1 other" told the user nothing. A status-unknown row is never guessed into
 *  parent/schedule; any other lifecycle state is counted under its own name. */
export const WAIT_KINDS = ['parent', 'schedule', 'contained', 'triage', 'review', 'blocked', 'unknown']
const WAIT_LABEL = { parent: 'on parent', schedule: 'on schedule', contained: 'contained', triage: 'in triage', review: 'in review', blocked: 'blocked', unknown: 'unknown' }
export function waitKind(i) {
  const c = i?.chip || {}
  if (c.stale || /^(status )?unknown\b/.test(str(i?.detail)) || /\bunknown \(/.test(str(i?.detail)) || c.state === 'unknown') return 'unknown'
  if (i?.kind !== 'kanban') return 'unknown'
  if (i.cardStatus) return 'blocked'
  if (c.contained) return 'contained'
  const on = str(c.waitingOn)
  if (c.state === 'todo' || /^(blocked |an? )?(parent|card|task)s?\b/i.test(on)) return 'parent'
  if (c.state === 'triage' || c.state === 'review' || c.state === 'blocked') return c.state
  if (c.state === 'scheduled' || on) return 'schedule'
  return str(c.state) || 'unknown'
}
export function waitBreakdown(rows) {
  const n = Object.create(null)
  for (const i of rows || []) { const k = waitKind(i); n[k] = (n[k] || 0) + 1 }
  // Only buckets with something in them show; a zero bucket is noise. Known kinds first, any other state after.
  const keys = [...WAIT_KINDS.filter(k => n[k]), ...Object.keys(n).filter(k => !WAIT_KINDS.includes(k))]
  return { n, text: keys.map(k => `${n[k]} ${Object.hasOwn(WAIT_LABEL, k) ? WAIT_LABEL[k] : k}`).join(' · ') }
}

/** True when the chat has anything not finished: an ask, a card/sub-agent/process that is open, an open ledger
 *  step or an agent issue. Done/archived cards never count as open work; history has its own disclosure. */
export function hasOpenWork(sorted, { asks = 0, steps = [], issues = [] } = {}) {
  const open = list => (list || []).some(i => !(i.kind === 'kanban' && TERMINAL.has(str(i.chip?.state))))
  return !!(asks || sorted.unasked.length || open(sorted.active) || open(sorted.stuck) || open(sorted.blockedUpstream) || open(sorted.waiting) || open(sorted.ready) || open(sorted.queued) || (sorted.reported || []).length ||
    (Array.isArray(steps) && steps.some(s => s?.id && (s.status || 'open') === 'open')) || (Array.isArray(issues) && issues.length))
}

/** Attach each work row's evidence line (projected at `nowMs`, never cached); returns how many rows have SOURCE evidence that is unknown (rows with none show their own explicit unknown line). */
export function annotateEvidence(sorted, nowMs, opts) {
  let unknown = 0
  for (const list of [sorted.active, sorted.stuck, sorted.blockedUpstream || [], sorted.waiting, sorted.ready || [], sorted.queued, sorted.unasked]) {
    for (const i of list) {
      if (!['kanban', 'bg', 'sub'].includes(i.kind)) continue
      const v = chipObservation(i.chip, nowMs / 1000, opts)
      i.evidenceText = chipEvidenceLine(i.chip, nowMs / 1000, opts)
      i.evidenceState = v.state
      // A parked card read fresh from its board has no run to observe: unknown execution evidence there is
      // expected, not a gap (it read "1 waiting · 1 evidence unknown" as if the evidence were the wait).
      if (!i.parked && v.state === 'unknown' && (v.sourced || i.kind === 'bg' || i.kind === 'sub' || legacyLive(i.chip, v))) unknown++
    }
  }
  return unknown
}

function item(c) {
  return { key: c.key, kind: c.kind, label: str(c.label) || str(c.id) || 'task', id: c.id || null, chip: c }
}

export const ASK_MAX = 400 // characters of a step shown inline; the full text stays in the tooltip
const clip = (s, n = ASK_MAX) => { const t = str(s).replace(/\s+/g, ' ').trim(); return t.length > n ? `${t.slice(0, n - 1).trimEnd()}…` : t }
const clause = (text, word) => {
  // A clause ends at ';' or at '. <next outcome> =' ("Unblock = A. Archive = B." as well as "...; archive = B").
  const m = new RegExp(`\\b${word}\\s*(?:\\([^)]*\\))?\\s*=\\s*([^;]+?)\\s*(?:;|\\.\\s+(?=(?:unblock|yes|archive|no)\\s*(?:\\([^)]*\\))?\\s*=)|$)`, 'i').exec(text)
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
  const b = r.brief && typeof r.brief === 'object' ? r.brief : null
  // generate-unblock-card's brief (session-tasks briefOf): what happened, the options with their costs, the pick.
  const rec = recommendParts(b?.recommend)
  const options = (b && Array.isArray(b.options) ? b.options : []).slice(0, 6).map(optionParts).filter(o => o.name)
  const brief = b && str(b.situation)
    ? { situation: clip(b.situation, 600), options: options.map(o => o.full), recommend: clip(b.recommend, 240) }
    : null
  const why = whyParts(r.why)
  return { known: true, step: clip(step), full: step, why: typeof r.why === 'string' ? clip(r.why, 300) : '', next, brief,
    yes, no, decision: !!brief && options.length >= 2,
    options: options.map(o => (rec.letter && o.letter === rec.letter ? { ...o, recommended: true, reason: rec.reason } : { ...o, recommended: false })),
    // A recommendation no tile carries (no letter, or a letter not in the list) is shown as its own line.
    recommendLine: b?.recommend && !(rec.letter && options.some(o => o.letter === rec.letter)) ? clip(b.recommend, 300) : '',
    single: options.length === 1 ? options[0] : null,
    reason: rec.reason, whyLine: why.line, tried: why.tried }
}

/** "A) One test run: restarts the app (~1 min)" → {letter: 'A', name: 'One test run', detail: 'restarts the app (~1 min)'}.
 *  The name ends at the first ':' / ' - ' / ' — ' within 80 chars; otherwise the first clause is the name. */
export function optionParts(text) {
  const full = clip(text, 400)
  const m = /^\s*(?:[Oo]ption\s+)?\(?([A-F1-9])(?:\)|\.|:|\s+[-\u2014\u2013])\s*/.exec(full)
  const letter = m ? m[1].toUpperCase() : ''
  const rest = m ? full.slice(m[0].length) : full
  const sep = /\s*(?::\s|\s[-\u2014\u2013]\s)/.exec(rest)
  const name = sep && sep.index > 0 && sep.index <= 80 ? rest.slice(0, sep.index) : clip(rest.split(/[.;,]\s/)[0], 80)
  const detail = sep && sep.index > 0 && sep.index <= 80 ? rest.slice(sep.index + sep[0].length) : rest.slice(name.length).replace(/^[.;,]\s*/, '')
  const cap = t => t ? t[0].toUpperCase() + t.slice(1) : t
  return { letter, name: cap(name.trim()), detail: cap(detail.trim()), full }
}

/** "A - two reloads buy the check" → {letter: 'A', reason: 'two reloads buy the check'}; no single leading letter
 *  ("Roll back because…", "A, B both ok", "I recommend A") → {letter: '', reason: the whole text}. */
export function recommendParts(text) {
  const t = str(text).replace(/^\s*recommend(?:ation|ed)?\s*:\s*/i, '').trim()
  const m = /^(?:[Oo]ption\s+)?\(?([A-F1-9])\)?(?=[\s:.—–-]|$)/.exec(t)
  if (!m || /^\s*(?:,|and\b|or\b|\/)\s*/i.test(t.slice(m[0].length))) return { letter: '', reason: t }
  const reason = t.slice(m[0].length).replace(/^[\s.:,—–-]+/, '').replace(/^because\s+/i, '').trim()
  return { letter: m[1], reason }
}

/** why_human split: "Tried: … Only you can: …" → the user-facing line and the agent's trail for Details. Without an
 *  "Only you can" part the line is the free-text reason ("Why you: …"), or what was tried when that is all there is. */
export function whyParts(text) {
  const t = str(text).replace(/\s+/g, ' ').trim()
  const i = t.search(/\bonly you can\b/i)
  if (i >= 0) {
    const part = t.slice(i).replace(/^only you can\s*:?\s*/i, '').trim()
    return { line: `Only you can: ${part}`, tried: t.slice(0, i).replace(/^tried\s*:\s*/i, '').trim() }
  }
  if (/^tried\s*:/i.test(t)) { const tried = t.replace(/^tried\s*:\s*/i, ''); return { line: `Agent tried: ${clip(tried, 300)}`, tried } }
  return { line: t ? `Why you: ${clip(t, 300)}` : '', tried: '' }
}

/** Composer draft for an option tile: the user's pick, sent only when they press send. */
export const chooseText = (i, o) => `Card ${i.id}: go with ${o.letter ? `option ${o.letter}` : 'this option'} (${o.name}).\n`
/** Composer drafts for a single-step card. */
export const doneText = i => `Card ${i.id}: done.\n`
export const skipText = i => `Card ${i.id}: skip this one; archive it.\n`

/** Composer draft for Ask agent: a plain question about one card, sent only when the user presses send. */
export const askAgentText = i => `Card ${i.id}: what exactly do you need me to do, and where? Please answer in one or two plain sentences.\n`

/** Pip/summary counts: one per row the panel lists, so "In progress · 1" always shows one running box. Sub-agents,
 *  background processes and rented machines count like cards (a sub-agent alone used to show no blue box).
 *  Needs you = open asks plus unasked cards that need the user. */
export function counts(sorted, { asks = 0, done = null } = {}) {
  return {
    done: done ?? 0,
    running: sorted.active.length,
    needsYou: asks + sorted.unasked.length,
    stuck: sorted.stuck.length,
    blockedUpstream: (sorted.blockedUpstream || []).length,
    waiting: sorted.waiting.length,
    ready: (sorted.ready || []).length,
    queued: sorted.queued.length
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
    if (!t?.id || !['done', 'archived'].includes(str(t.status)) || seen.has(key)) continue
    seen.add(key)
    out.push({ key, id: t.id, label: str(t.title) || t.id, at: Number(t.activity_at) || 0, ...(t.status === 'archived' ? { archived: true } : {}) })
  }
  return out.sort((a, b) => b.at - a.at)
}

/** Attention keys: a new ask or a new stuck item reopens a collapsed panel. */
export const attentionKeys = (askKeys, sorted) => [...askKeys.map(key => `ask:${key}`), ...sorted.unasked.map(i => i.key), ...sorted.stuck.map(i => i.key)]

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

// Last-known finished cards per chat, so a switch paints the Done count at once instead of after the 2-5 s
// /session-cards read (then the read replaces it). Memory + localStorage, newest DONE_CACHE_MAX chats.
const DONE_CACHE_KEY = 'hermes.sessionPanel.done.v1'
const DONE_CACHE_MAX = 200
let doneCache = null
function doneStore() {
  if (doneCache) return doneCache
  doneCache = new Map()
  try {
    const raw = JSON.parse(window.localStorage.getItem(DONE_CACHE_KEY) || '{}')
    for (const [k, v] of Object.entries(raw)) if (Array.isArray(v)) doneCache.set(k, v)
  } catch {}
  return doneCache
}
/** Last-known done rows for a chat scope, or null (exported for tests). */
export const cachedDone = scope => doneStore().get(scope) || null
function rememberDone(scope, rows) {
  const store = doneStore()
  store.delete(scope)
  store.set(scope, rows)
  while (store.size > DONE_CACHE_MAX) store.delete(store.keys().next().value)
  try { window.localStorage.setItem(DONE_CACHE_KEY, JSON.stringify(Object.fromEntries(store))) } catch {}
}
export const resetDoneCacheForTests = () => { doneCache = null; done = { scope: null, rows: null } }

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
    if (done.rows) rememberDone(scope, done.rows)
  } catch (e) {
    log('session-cards read failed', e?.message || e)
    if (done.scope !== scope) done = { scope, rows: null }
  }
  doneSubs.forEach(fn => fn(done))
}

// ── background warm of recent chats ────────────────────────────────────────
// The chats you switch between are kept warm, so the first switch after launch (or after a while away) paints
// from a fresh cache too. Recent = the last WARM_RECENT chats focused in this window (persisted, so it survives
// a relaunch). While the window is visible and focus has been still for WARM_SETTLE_MS, each recent chat (not
// the focused one, which polls live) is re-read at most every WARM_EVERY_MS, one chat at a time.
const RECENT_KEY = 'hermes.sessionPanel.recent.v1'
export const WARM_RECENT = 8
export const WARM_EVERY_MS = 3 * 60_000
export const WARM_TICK_MS = 30_000
export const WARM_SETTLE_MS = 10_000
const warmedAt = new Map() // routeKey -> ms
let lastFocusMoveAt = 0
let warming = false

export function readRecent() {
  try { const v = JSON.parse(window.localStorage.getItem(RECENT_KEY) || '[]'); return Array.isArray(v) ? v.filter(r => r?.connectionId && r?.session) : [] } catch { return [] }
}
/** Put a focused chat at the head of the recent list (deduped by route, capped). */
export function noteRecent(route) {
  if (!route?.connectionId || !route?.session) return
  lastFocusMoveAt = Date.now()
  const key = routeKey(route)
  const next = [{ connectionId: route.connectionId, profile: route.profile || 'default', session: route.session }, ...readRecent().filter(r => routeKey(r) !== key)].slice(0, WARM_RECENT)
  try { window.localStorage.setItem(RECENT_KEY, JSON.stringify(next)) } catch {}
}

async function warmDone(route) {
  const st = window.__sessionTasks, bridge = window.hermesDesktop?.api
  if (!bridge || typeof st?.lineageIds !== 'function') return false
  const ids = await st.lineageIds(route.session, route.connectionId, route.profile)
  if (ids?.incomplete) return false
  const r = await bridge({ path: `/api/plugins/user-steps/session-cards?ids=${encodeURIComponent([...ids].join(','))}`, connectionId: route.connectionId, profile: route.profile, timeoutMs: 15_000 })
  if (!(Array.isArray(r?.tasks) && Array.isArray(r.includes) && r.includes.includes('done'))) return false
  rememberDone(routeKey(route), doneRows(r.tasks))
  return true
}

/** One warm pass: every recent chat that is not focused and not warmed within WARM_EVERY_MS, sequentially.
 *  Skips while hidden, while focus moved in the last WARM_SETTLE_MS, or while a pass is running. Exported for tests;
 *  `force` ignores the settle wait. Returns the routes it warmed. */
export async function warmRecent({ nowMs = Date.now(), force = false } = {}) {
  if (warming || (typeof document !== 'undefined' && document.hidden)) return []
  if (!force && nowMs - lastFocusMoveAt < WARM_SETTLE_MS) return []
  warming = true
  const done = []
  try {
    const focused = routeKey(panelRoute())
    for (const route of readRecent()) {
      const key = routeKey(route)
      if (key === focused || nowMs - (warmedAt.get(key) || 0) < WARM_EVERY_MS) continue
      if (typeof document !== 'undefined' && document.hidden) break
      if (routeKey(panelRoute()) !== focused) break // the user switched: stop, the next pass starts over
      warmedAt.set(key, nowMs)
      const safe = p => Promise.resolve(p).catch(e => { log('warm failed', key, e?.message || e); return false })
      await safe(window.__sessionTasks?.warm?.(route))
      await safe(window.__userSteps?.warm?.(route))
      await safe(warmDone(route))
      done.push(route)
    }
  } finally { warming = false }
  return done
}
export const resetWarmForTests = () => { warmedAt.clear(); lastFocusMoveAt = 0; warming = false }

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

// green = ready, red = blocked on the user, grey = done, yellow = not ready (waiting on something / not started).
export const C = {
  needsYou: '#f85149', ready: '#3fb950', done: '#8b949e', waiting: '#d29922', queued: '#d29922',
  running: '#3b82f6', stuck: '#f0883e', blockedUpstream: '#db61a2'
}
const LABEL = { done: 'done', running: 'running', needsYou: 'needs you', stuck: 'stuck', blockedUpstream: 'blocked upstream', ready: 'ready', waiting: 'waiting', queued: 'not started' }
const muted = { color: 'var(--muted-foreground)', fontSize: 12 }
// Text colours with >= 4.5:1 contrast on both themes (light-dark needs the host's color-scheme; the first value is
// the fallback where light-dark is unsupported). ACCENT_FILL carries white text in either theme.
const ACCENT_TEXT = 'light-dark(#0969da, #58a6ff)', ALERT_TEXT = 'light-dark(#cf222e, #ff7b72)', ACCENT_FILL = '#0969da'
const heading = color => ({ margin: '12px 0 6px', fontSize: 12, fontWeight: 600, color })
const btn = { minHeight: 32, padding: '4px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'transparent', color: 'var(--foreground)', font: 'inherit', fontSize: 12.5, cursor: 'pointer' }

// One small box per card, in the header beside the status: a full-width bar spent a whole row to say "1 waiting".
// Shape carries state as well as colour: open work is filled, waiting is an outline, not started is dashed.
const PIP_ORDER = ['needsYou', 'stuck', 'blockedUpstream', 'running', 'ready', 'waiting', 'queued', 'done']
export const PIPS_MAX = 24 // boxes drawn before the rest fold into "+N"
// A hub-scheduled card's box is a little larger and carries an "H" (running and waiting work alike).
// A one-line waiting row carries the same box as its full row, so its colour says what it is (yellow = waiting).
const lineKind = i => (i.blockedUpstreamRow ? 'blockedUpstream' : 'waiting')
const pipStyle = (k, hub = false) => {
  const base = { width: 9, height: 9, borderRadius: 2, boxSizing: 'border-box', flex: 'none' }
  const h = hub ? { width: 12, height: 12, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 8.5, fontWeight: 700, lineHeight: 1 } : {}
  if (k === 'waiting' || k === 'blockedUpstream') return { ...base, ...h, border: `1.5px solid ${C[k]}`, color: C[k] }
  if (k === 'queued') return { ...base, ...h, border: `1.5px dashed ${C.queued}`, color: C.queued }
  return { ...base, ...h, background: C[k], color: 'var(--card, #fff)' }
}

/** Per pip state, how many of its open cards are scheduled on the hub (session-tasks `runMachine`). */
const onHub = i => i.kind === 'kanban' && i.chip?.runMachine === 'hub'
export function hubCounts(sorted) {
  const on = list => (list || []).filter(onHub).length
  return { running: on(sorted.active), stuck: on(sorted.stuck), blockedUpstream: on(sorted.blockedUpstream), waiting: on(sorted.waiting), ready: on(sorted.ready), queued: on(sorted.queued) }
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

// Each row leads with the same box as its header pip (shape + colour + hub "H"), not a bullet dot, so the
// header and the list read as one legend. Centred on the 19px first line.
function Row({ i, k }) {
  if (i.cardStatus) return jsx(CardStatusRow, { i })
  const hub = onHub(i)
  return jsxs('div', { 'data-panel-row': i.key, style: { display: 'grid', gridTemplateColumns: '12px minmax(0,1fr)', columnGap: 6, padding: '3px 0', fontSize: 13 }, children: [
    jsx('span', { 'aria-hidden': true, 'data-row-pip': k, ...(hub ? { 'data-row-pip-hub': '' } : {}), style: { ...pipStyle(k, hub), justifySelf: 'center', marginTop: hub ? 3.5 : 5 }, children: hub ? 'H' : undefined }),
    jsxs('div', { style: { minWidth: 0 }, children: [
      jsx('div', { title: str(i.chip?.title) || i.label, style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: i.label }),
      i.detail && jsx('div', { 'data-row-detail': '', style: { ...muted, overflowWrap: 'anywhere' }, children: jsx(WaitText, { text: i.detail, chip: i.chip }) }),
      i.evidenceText && jsx('div', { 'data-evidence': i.evidenceState || '', style: { ...muted, fontSize: 11, overflowWrap: 'anywhere' }, children: `evidence: ${i.evidenceText}` })
    ] })
  ] })
}

// ── waiting rows ───────────────────────────────────────────────────────────
// [daveey 2026-11-20] "come on, this is ugly": a waiting row is one line like a running row, box · title on the
// left, and on the right a SHORT reason plus how long it has waited. A card it waits on is named by a short
// linked title with its status as a tag, never its whole title inline; "board status" and other source notes
// live in the click-open details, never on the line.
const CLOCK_WAIT = /^the clock until (\d{4}-\d\d-\d\d) (\d\d:\d\d) UTC\b/
const PT_DAY = typeof Intl !== 'undefined' ? new Intl.DateTimeFormat('en-US', { timeZone: 'America/Los_Angeles', weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZoneName: 'short' }) : null
/** `Sat 00:00 PST` within the coming/past 6 days, else `Dec 2 22:20 PST` (Pacific wall clock). */
export function pacificShort(sec, nowMs = Date.now()) {
  if (!PT_DAY || !Number.isFinite(Number(sec))) return ''
  const p = Object.fromEntries(PT_DAY.formatToParts(new Date(Number(sec) * 1000)).map(x => [x.type, x.value]))
  const near = Math.abs(Number(sec) - nowMs / 1000) < 6 * 86_400
  return `${near ? p.weekday : `${p.month} ${p.day}`} ${p.hour}:${p.minute} ${p.timeZoneName}`
}

/** A published wait, short enough for the line: a CI run URL reads `CI run 44819160264`, a PR URL `PR #121`; the
 *  full text stays in the details. */
export function shortWait(w) {
  const t = str(w).trim()
  const run = /actions\/runs\/(\d+)/.exec(t), pr = /\/pull\/(\d+)/.exec(t)
  if (/^ci\b/i.test(t) && run) return `CI run ${run[1]}`
  if (pr) return `${/^ci\b/i.test(t) ? 'CI on ' : ''}PR #${pr[1]}`
  // URLs lose their scheme and are cut; a full commit sha is cut to 9 characters.
  return t.replace(/https?:\/\/\S+/g, u => { const bare = u.replace(/^https?:\/\/(www\.)?/, ''); return bare.length > 40 ? `${bare.slice(0, 39)}…` : bare })
    .replace(/\b([0-9a-f]{9})[0-9a-f]{31}\b/g, '$1')
}

/** What a Waiting row shows on its line: `{ reason: [text | { ref }], tone, age, sub }`. `ref` is
 *  `{ id, status, text }`; `tone` 'warn' (orange: well overdue, a retired ask still blocked) or 'note' (yellow: no wake
 *  or reason published);
 *  `age` is how long it has waited (from the card's published wait start). Built from the chip, not from
 *  the long `detail` string, which stays for the details and tooltip. */
export function rowWait(i, nowMs = Date.now(), parkInfo = null) {
  const c = i?.chip || {}, now = nowMs / 1000
  const since = Number(c.waitingSince) > 0 && Number(c.waitingSince) <= now ? Number(c.waitingSince) : 0
  const age = since ? shortDur(now - since) : ''
  const v = (reason, tone = '', extra = {}) => ({ reason, tone, age, sub: '', ...extra })
  // A retired ask's still-blocked owner: its own status text (gateway / profile / board / id · blocked · request …).
  if (i?.cardStatus) {
    const [path, ...rest] = str(i?.detail).split(' · ')
    const id = /\bt_[0-9a-f]{6,}\b|[^/\s]+$/.exec(path.trim())?.[0] || ''
    return v([rest.length ? `${rest.join(' · ')}${id ? ` · ${id}` : ''}` : 'still blocked · its ask was retired'], 'warn')
  }
  if (i?.blockedUpstreamRow) {
    const bu = c.blockedUpstream || {}, auth = bu.contained?.authority
    return bu.contained ? v(['released by ', { ref: { id: str(auth?.id), status: str(auth?.status) || 'unknown', text: `card ${str(auth?.id)}` } }])
      : v(['parent blocked: ', { ref: { id: str(bu.id), status: str(bu.status), text: `card ${str(bu.id)}` } }])
  }
  if (i?.kind !== 'kanban') return v([str(i?.detail) || 'waiting'])
  if (waitKind(i) === 'unknown') {
    // The cause stays on the line (source expired, card could not be re-read); the last known state is in the details.
    const why = /^status unknown \(([^)]+)\)/.exec(str(i?.detail))?.[1] || (c.stale ? 'could not be re-read' : '')
    return v([`status unknown${why ? ` · ${why}` : ''}`])
  }
  const w = str(c.waitingOn).trim()
  const clock = CLOCK_WAIT.exec(w)
  if (clock) {
    const at = Date.parse(`${clock[1]}T${clock[2]}:00Z`) / 1000
    if (Number.isFinite(at)) {
      if (at >= now) return v([`${shortDur(at - now)} left`], '', { sub: pacificShort(at, nowMs) })
      const late = now - at
      return v([`${late >= OVERDUE_FLAG_S ? '⚠ ' : ''}${shortDur(late)} overdue`], late >= OVERDUE_FLAG_S ? 'warn' : '', { sub: `due ${pacificShort(at, nowMs)}` })
    }
  }
  // A card named in the wait is parsed exactly as the long form parses it (waitSegments: id, status, other gateway).
  const named = /\bt_[0-9a-f]{6,}\b/.test(w) ? waitSegments(w, c, nowMs).find(x => x.id) : null
  const refId = named ? named.id : (!w || /did not publish/i.test(w)) && c.upstream?.id ? str(c.upstream.id) : ''
  if (refId) {
    const status = named?.status || (c.upstream?.id === refId ? str(c.upstream.status) : '')
    return v([/\brelease\b/.test(w) ? 'released by ' : 'on ', { ref: { id: refId, status, text: named ? w : `card ${refId}` } }])
  }
  if (c.state === 'triage' && (!w || w === 'triage')) return v(['needs triage'])
  if (c.state === 'review' && (!w || /did not publish/i.test(w))) {
    const label = typeof window !== 'undefined' ? window.__sessionTasks?.activityLabel : null
    const kind = (typeof label === 'function' ? str(label(c.activity)) : str(c.activity).replace(/_/g, ' ')).trim()
    const at = Number(c.activityAt)
    return v([`no reviewer running${kind ? ` · ${kind}${at > 0 ? ` ${shortDur(now - at)} ago` : ''}` : ''}`])
  }
  if (w && !/did not publish/i.test(w)) return v([`on ${shortWait(w)}`])
  // Nothing on the row: the owner's park (read by the caller via parkFor) says what wakes it.
  if (parkInfo?.state === 'loading' && !parkInfo.park) return v(['reading wake…'])
  const pk = parkInfo?.park
  if (pk) {
    const vv = (reason, tone = '', extra = {}) => ({ ...v(reason, tone, extra), age: age || (pk.parkedAt && pk.parkedAt <= now ? shortDur(now - pk.parkedAt) : '') })
    const by = pk.wakeAt ? (pk.wakeAt >= now ? `by ${pacificShort(pk.wakeAt, nowMs)}` : `due ${pacificShort(pk.wakeAt, nowMs)}`) : ''
    const late = pk.wakeAt && pk.wakeAt < now ? now - pk.wakeAt : 0
    const lateText = late ? `${late >= OVERDUE_FLAG_S ? '⚠ ' : ''}${shortDur(late)} overdue` : ''
    const tone = late >= OVERDUE_FLAG_S ? 'warn' : ''
    const a = pk.awaiting && !pk.awaiting.inferred ? pk.awaiting : null
    const staleNote = parkInfo.state === 'stale' ? ' · not re-read' : ''
    if (pk.forHuman) return vv(['no timed wake · will be blocked for a person'], 'warn')
    if (pk.superseded) return vv(['new park not synced yet'], '', { sub: `backstop ${pacificShort(pk.wakeAt, nowMs)}${staleNote}` })
    if (a?.kind === 'human') return vv([`waits on a person${a.why ? ` (${a.why.slice(0, 60)})` : ''}`], 'warn')
    if (a && /\bt_[0-9a-f]{6,}\b/.test(a.ref)) {
      const id = /\bt_[0-9a-f]{6,}\b/.exec(a.ref)[0]
      return vv(['on ', { ref: { id, status: '', text: `card ${id}` } }], tone, { sub: (lateText || by) + staleNote })
    }
    if (a && a.kind !== 'clock' && a.ref) {
      const ci = a.kind === 'ci' ? shortWait(`ci ${a.ref}`) : ''
      const what = ci ? (/^ci /i.test(ci) ? `CI: ${ci.slice(3)}` : ci) : a.kind === 'external' || a.kind === 'resource' ? shortWait(a.ref) : `${a.kind} ${shortWait(a.ref)}`
      return vv([`on ${what}`], tone, { sub: (lateText || by) + staleNote })
    }
    if (pk.check) {
      const probe = /([\w.-]+\.(?:py|sh|mjs|js))\b/.exec(pk.check)?.[1] || shortWait(pk.check).split(/\s+/).slice(0, 3).join(' ')
      return vv([`on probe ${probe}`], tone, { sub: (lateText || by) + staleNote })
    }
    if (pk.wakeAt) {
      const why = a?.kind === 'clock' && a.why ? ` (${a.why.slice(0, 60)})` : ''
      if (pk.wakeAt >= now) return vv([`${pk.continued ? 'resumes in ' : ''}${shortDur(pk.wakeAt - now)}${pk.continued ? '' : ' left'}${why}`], '', { sub: pacificShort(pk.wakeAt, nowMs) + staleNote })
      return vv([lateText], tone, { sub: `due ${pacificShort(pk.wakeAt, nowMs)}${staleNote}` })
    }
  }
  if (c.state === 'scheduled') return v(['no wake published'], 'note')
  if (c.state === 'blocked') return v(['blocked · no reason given'], 'note')
  if (c.state === 'todo') return v(['on parent cards'])
  return v([str(i?.detail) || str(c.state) || 'waiting'])
}

const refLink = { padding: 0, border: 0, background: 'transparent', font: 'inherit', color: 'inherit', cursor: 'pointer', textDecoration: 'underline', textDecorationColor: 'color-mix(in srgb, currentColor 45%, transparent)', textUnderlineOffset: 3 }
const REF_CHARS = 28
const SOURCE_TAIL = / · ((?:reported )?board status)$/
/** A card named in a wait: its title cut to REF_CHARS (the whole title, id and status in the tooltip), linked when this
 *  Desktop can open it, the bare id until its title is known; then its status as plain muted text. The link is its own
 *  button (its click never reaches the row; the row handles no keys). Its known state is the fallback status. */
function RefChip({ ref: r, chip }) {
  const seg = waitSegments(r.text || `card ${r.id}`, chip).find(x => x.id === r.id) || { id: r.id, title: '', linkable: false }
  const st = typeof window !== 'undefined' ? window.__sessionTasks : null
  const status = r.status || seg.state || ''
  const name = seg.title ? (seg.title.length > REF_CHARS ? `${seg.title.slice(0, REF_CHARS - 1).trimEnd()}…` : seg.title) : r.id
  const tip = `${seg.title ? `${seg.title}\n` : ''}${r.id}${status ? ` · ${status}` : ''}`
  const open = e => { e.stopPropagation(); st.openTaskChip(seg.open) }
  return jsxs('span', { 'data-wait-ref': r.id, children: [
    seg.linkable && typeof st?.openTaskChip === 'function'
      ? jsx('button', { type: 'button', 'data-wait-card': r.id, title: `Open: ${tip}`, style: refLink, onClick: open, children: name })
      : jsx('span', { title: tip, children: name }),
    status && jsx('span', { 'data-wait-status': '', style: { opacity: 0.75 }, children: ` · ${status.replace(/_/g, ' ')}` })
  ] })
}

// One stylesheet for the waiting rows: a hover tint says a row opens; the disclosure button is the title itself.
const WAIT_CSS = `[data-wait-row]{border-radius:5px}[data-wait-row]:hover{background:color-mix(in srgb,var(--foreground) 5%,transparent)}
[data-wait-toggle]:focus-visible{outline:1px solid color-mix(in srgb,var(--foreground) 50%,transparent);outline-offset:1px;border-radius:3px}`
/** Waiting rows share one grid, so the reason column lines up across rows: box · title · reason · age. The reason
 *  column is a fixed 42% in every list, so it starts at the same place folded, opened and in Blocked upstream, and
 *  the title always keeps the rest. */
/** The cards a waiting row waits on, nearest first: the cards its published wait names, its upstream, the parent
 *  holding it (blocked upstream) and, when contained, the card that releases it. */
const waitsOnIds = i => {
  const c = i?.chip || {}
  const ids = str(c.waitingOn).match(/\bt_[0-9a-f]{6,}\b/g) || []
  for (const id of [c.upstream?.id, c.blockedUpstream?.contained?.authority?.id, c.blockedUpstream?.id]) if (id) ids.push(str(id))
  return ids
}
/** Waiting rows as an outline: a card that waits on another card of the same list sits under it.
 *  [daveey 2026-11-20] "these cards are waiting for other cards in the session, can we display them as outline view".
 *  Returns `[{ i, depth, parentId }]` in display order: roots keep the list's order, each followed by its subtree.
 *  A card whose blocker is not in the list stays a root (and its line names the blocker); a cycle never nests.
 *  Ids are matched bare (`t_<hex>` is unique in practice); a card seen twice (two gateways) nests under its first copy. */
export function waitTree(rows, rankOf = () => 0) {
  const list = (rows || []).filter(Boolean)
  const byId = new Map(list.filter(i => i.chip?.id).map(i => [str(i.chip.id), i]))
  const parent = new Map()
  for (const i of list) {
    const own = str(i.chip?.id)
    const p = waitsOnIds(i).find(id => id !== own && byId.has(id))
    if (p) parent.set(i.key, p)
  }
  // Break cycles: a card whose chain comes back to itself is a root. A chain that runs into some other loop just
  // stops there (that loop is broken when its own cards are walked), so a card leading into a loop still nests.
  for (const i of list) {
    const own = str(i.chip?.id), seen = new Set([own])
    let cur = parent.get(i.key)
    while (cur) {
      if (cur === own) { parent.delete(i.key); break }
      if (seen.has(cur)) break
      seen.add(cur)
      cur = parent.get(byId.get(cur)?.key)
    }
  }
  const kids = new Map()
  for (const i of list) {
    const p = parent.get(i.key)
    if (p) kids.set(p, [...(kids.get(p) || []), i])
  }
  // A root's rank is the most urgent (lowest) rank in its subtree, so a three-line cut never hides a blocked-upstream
  // or overdue line behind a long calm chain. Equal ranks keep the list's order.
  const rankMemo = new Map()
  const subtreeRank = (i, seen = new Set()) => {
    if (rankMemo.has(i.key)) return rankMemo.get(i.key)
    if (seen.has(i.key)) return Infinity
    seen.add(i.key)
    let r = Number(rankOf(i)) || 0
    for (const k of kids.get(str(i.chip?.id)) || []) r = Math.min(r, subtreeRank(k, seen))
    rankMemo.set(i.key, r)
    return r
  }
  const roots = list.map((i, n) => ({ i, n })).filter(x => !parent.has(x.i.key))
    .sort((a, b) => subtreeRank(a.i) - subtreeRank(b.i) || a.n - b.n).map(x => x.i)
  const out = [], done = new Set()
  const walk = (i, depth, parentId) => {
    if (done.has(i.key)) return
    done.add(i.key)
    out.push({ i, depth, parentId })
    for (const k of kids.get(str(i.chip?.id)) || []) walk(k, depth + 1, str(i.chip?.id))
  }
  for (const i of roots) walk(i, 0, null)
  return out
}

/** Waiting rows share one grid, so the reason column lines up across rows: (indent · box · title) · reason · age.
 *  The reason column is a fixed 42% in every list, so it starts at the same place folded, opened and in Blocked
 *  upstream, and the title always keeps the rest. Rows are an outline (waitTree); `limit` cuts the outline after
 *  that many lines (children stay under their parent) and the rest count as "+N more waiting". */
export function WaitList({ rows, now = Date.now(), kindOf = () => 'waiting', limit = Infinity, more = 0, attrs = {} }) {
  // Most urgent first when cut: blocked upstream, then a warning (well overdue, a retired ask), then the rest.
  const tree = waitTree(rows, i => (i.blockedUpstreamRow ? 0 : rowWait(i, now, needsPark(i) ? parkFor(i.chip, now) : null).tone === 'warn' ? 1 : 2))
  const shown = tree.slice(0, limit)
  const rest = tree.length - shown.length + more
  if (!shown.length && !rest) return null
  return jsxs('div', { ...attrs, style: { display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,42%) auto', columnGap: 10, minWidth: 0 }, children: [
    ...shown.map(n => jsx(WaitRow, { i: n.i, now, k: kindOf(n.i), depth: n.depth, parentId: n.parentId }, n.i.key)),
    rest > 0 && jsx('div', { 'data-wait-more': '', style: { ...muted, gridColumn: '1 / -1', padding: '2px 0 2px 22px' }, children: `+${rest} more waiting` }, 'more')
  ] })
}

/** The owner's park in words, for the long form: what it waits on (and why), the probe, and the wake. */
export function parkLine(pk) {
  if (!pk) return ''
  const a = pk.awaiting
  const parts = []
  if (a && (a.ref || a.why)) parts.push(`${a.inferred ? 'inferred: ' : ''}waits on ${a.kind}${a.ref ? ` ${a.ref}` : ''}${a.why ? ` (${a.why})` : ''}`)
  if (pk.check) parts.push(`probe \`${pk.check}\`${pk.nextProbeAt ? `, next ${pacificShort(pk.nextProbeAt)}` : ''}`)
  if (pk.superseded) parts.push('its newest park already ended; the next park has not synced')
  if (pk.forHuman) parts.push('no timed wake: the dispatcher blocks it for a person on its next tick')
  else if (pk.wakeAt) parts.push(`${pk.superseded ? 'backstop' : pk.continued ? 'resumes' : 'wakes'} ${pacificShort(pk.wakeAt)}`)
  return `owner's park: ${parts.join(' · ') || 'no wake'}`
}

/** One waiting card on one line of its WaitList (subgrid): box · title · short reason · how long. The title is the
 *  row's disclosure button (keyboard: Tab, Enter/Space); a click anywhere on the line opens it too. The long form:
 *  the full wait text (cards linked, the source), id · state · where, the execution evidence, and Open card. */
export function WaitRow({ i, now = Date.now(), k = 'waiting', depth = 0, parentId = null }) {
  const [open, setOpen] = useState(false)
  const parkInfo = needsPark(i) ? parkFor(i.chip, now) : null
  const base = rowWait(i, now, parkInfo)
  // Nested under the card it waits on: the outline already says "on <parent>", so that reason is dropped.
  const view = parentId && base.reason[0] === 'on ' && base.reason[1]?.ref?.id === parentId ? { ...base, reason: [], sub: '' } : base
  // Indent is capped so a deep chain never squeezes the title away; the depth is still on the row (data-depth).
  const indent = Math.min(depth, 4) * 20
  const hub = onHub(i)
  const st = typeof window !== 'undefined' ? window.__sessionTasks : null
  const canOpen = i.kind === 'kanban' && !!i.chip?.connectionId && !!i.chip?.profile && typeof st?.openTaskChip === 'function'
  const tip = `${str(i.chip?.title) || i.label}${i.detail ? `\n${i.cardStatus ? i.detail : waitLabel(i.detail, i.chip)}` : ''}`
  const tone = view.tone === 'warn' ? C.stuck : view.tone === 'note' ? C.waiting : null
  const cell = { minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }
  return jsxs('div', { 'data-panel-row': i.key, 'data-waiting-line': i.key, 'data-wait-row': '', title: tip, onClick: () => setOpen(o => !o),
    style: { display: 'grid', gridColumn: '1 / -1', gridTemplateColumns: 'subgrid', alignItems: 'center', fontSize: 13, padding: '3px 0', cursor: 'pointer' }, children: [
      jsxs('span', { 'data-wait-head': '', ...(depth ? { 'data-depth': depth } : {}), style: { display: 'flex', alignItems: 'center', gap: 10, minWidth: 0, overflow: 'hidden', paddingLeft: indent }, children: [
        jsx('span', { 'aria-hidden': true, style: { width: 12, flex: 'none', display: 'inline-flex', justifyContent: 'center' }, children: jsx('span', { 'data-row-pip': k, 'data-line-pip': k, ...(hub ? { 'data-row-pip-hub': '', 'data-line-pip-hub': '' } : {}), style: { ...pipStyle(k, hub), display: 'inline-flex' }, children: hub ? 'H' : undefined }) }),
        jsx('button', { type: 'button', 'data-wait-toggle': '', 'aria-expanded': open, 'data-wait-title': '', style: { ...cell, display: 'block', flex: 1, padding: 0, border: 0, background: 'transparent', font: 'inherit', color: 'inherit', textAlign: 'left', cursor: 'pointer' }, children: i.label })
      ] }),
      jsxs('span', { 'data-wait-reason': '', ...(view.tone ? { 'data-tone': view.tone } : {}), ...(view.tone === 'warn' ? { 'data-warn': '' } : {}), style: { ...muted, ...cell, ...(tone ? { color: tone } : {}) }, children: [
        ...view.reason.map((p, n) => (typeof p === 'string' ? p : jsx(RefChip, { ref: p.ref, chip: i.chip }, `r${n}`))),
        view.sub && jsx('span', { 'data-wait-sub': '', style: { opacity: 0.75 }, children: ` · ${view.sub}` }, 'sub')
      ] }),
      jsx('span', { 'data-wait-age': '', title: view.age ? `waiting ${view.age}` : '', style: { ...muted, textAlign: 'right', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap', minWidth: '2.6em' }, children: view.age }),
      open && jsxs('div', { 'data-wait-details': '', onClick: e => e.stopPropagation(), style: { ...muted, gridColumn: '1 / -1', fontSize: 11.5, lineHeight: 1.5, padding: `2px 0 6px ${indent + 22}px`, overflowWrap: 'anywhere', cursor: 'auto' }, children: [
        // The wait itself first; where the state was read (`board status` / `reported board status`) joins the id line.
        i.detail && jsx('div', { 'data-row-detail': '', children: jsx(WaitText, { text: str(i.detail).replace(SOURCE_TAIL, ''), chip: i.chip, plain: i.cardStatus }) }),
        i.chip?.id && !i.cardStatus && jsx('div', { 'data-wait-ident': '', children: [i.chip.id, i.chip.state, whereText(i.chip), i.chip.board && i.chip.board !== 'default' ? `board ${i.chip.board}` : '', SOURCE_TAIL.exec(str(i.detail))?.[1] ? `read from ${SOURCE_TAIL.exec(str(i.detail))[1]}` : ''].filter(Boolean).join(' · ') }),
        parkInfo?.park && jsx('div', { 'data-wait-park': '', children: parkLine(parkInfo.park) }),
        i.evidenceText && jsx('div', { 'data-evidence': i.evidenceState || '', children: `evidence: ${i.evidenceText}` }),
        canOpen && jsx('button', { type: 'button', 'data-open-card': '', style: { ...btn, minHeight: 26, marginTop: 4, fontSize: 12 }, onClick: () => st.openTaskChip(i.chip), children: 'Open card' })
      ] })
    ] })
}

// ── running rows ───────────────────────────────────────────────────────────
// A running row is one line: box · title · where it runs and for how long. Everything else (card id, assignee,
// board, last event kind, the full execution-evidence line) is one click away in the row's details: on a card the
// header already counts as running, "running · default · just now" plus a 200-character evidence line spent three
// lines restating what the blue box says. A row reaches In progress only with running evidence, so the default
// view hides nothing that changes what the user would do; unknown evidence still lists under Waiting.
export const QUIET_S = 20 * 60 // a running card with no event this long reads "quiet" (Stuck takes over at STUCK_CARD_S)

/** Where a row's work runs, in one short word: hub, a worker name, pool, or the owning gateway. */
export function whereText(c) {
  const m = str(c?.runMachine)
  if (m === 'worker-pool') return 'pool'
  if (m) return m
  return str(c?.gateway) && c.gateway !== 'local' ? c.gateway : ''
}

/** A run's model, short: `class:deep` -> `deep`, `claude-opus-5-5` -> `opus-5-5`; '' when unknown. */
export function modelText(c) {
  const m = str(c?.runModel)
  if (!m) return ''
  return m.split(',').map(s => s.trim().replace(/^class:/, '').replace(/^claude-/, '')).filter(Boolean).join(', ')
}

/** The right-hand meta of a running row: `{ text, quiet, tip }`. `text` is short (`hub · 12 min`); `tip` says the
 *  same in full words for the tooltip. `quiet` = a running card whose newest event is older than QUIET_S. */
export function runningMeta(i, nowMs = Date.now()) {
  const c = i?.chip || {}, now = nowMs / 1000
  const join = (...p) => p.filter(Boolean).join(' · ')
  if (i?.kind === 'sub') {
    const tools = Number(c.toolCount) || 0
    return { text: join('sub-agent', tools ? `${tools} tool${tools === 1 ? '' : 's'}` : ''), quiet: false, tip: join('sub-agent', c.lastTool ? `last tool ${c.lastTool}` : '') }
  }
  if (i?.kind === 'bg') return { text: 'process', quiet: false, tip: 'background process' }
  if (i?.kind === 'machine') return { text: 'rented machine', quiet: false, tip: 'rented machine' }
  const where = whereText(c)
  const model = modelText(c)
  const modelTip = model && (str(c.runModel).startsWith('class:') ? `model class ${model}` : `model ${str(c.runModel)}`)
  const progress = Number(c.activityAt) || Number(c.progressAt) || 0
  const since = Number(c.since) || 0
  const lastEvent = progress ? `last event${c.activity ? ` ${c.activity}` : ''} ${ago(progress, nowMs)}` : 'last event unknown'
  if (c.state === 'review') {
    const at = progress || since
    return { text: join('in review', model, at ? span(now - at) : ''), quiet: false, tip: join('in review', where && `on ${where}`, modelTip, lastEvent) }
  }
  const quiet = !!progress && now - progress >= QUIET_S
  const runFor = since ? span(now - since) : ''
  return {
    text: join(where, model, quiet ? `quiet ${span(now - progress)}` : runFor || (progress ? ago(progress, nowMs) : '')),
    quiet,
    tip: join(where ? `running on ${where}` : 'running', modelTip, runFor && `for ${runFor}`, lastEvent)
  }
}

/** The details a running row reveals on click, as plain lines (exported for tests). */
export function runningDetails(i, nowMs = Date.now()) {
  const c = i?.chip || {}
  const progress = Number(c.activityAt) || Number(c.progressAt) || 0
  const lines = []
  if (i?.kind === 'kanban') {
    lines.push([i.id && `card ${i.id}`, c.assignee && `assignee ${c.assignee}`, c.runModel && `model ${c.runModel}`, c.board && c.board !== 'default' && `board ${c.board}`, c.gateway && `on ${c.gateway}`].filter(Boolean).join(' · '))
    lines.push([c.since && `started ${ago(c.since, nowMs)}`, progress ? `last event${c.activity ? ` ${c.activity}` : ''} ${ago(progress, nowMs)}` : 'last event unknown'].filter(Boolean).join(' · '))
  } else if (i?.kind === 'sub' && c.lastTool) lines.push(`last tool ${c.lastTool}`)
  return lines.filter(Boolean)
}

function RunningRow({ i, now }) {
  const [open, setOpen] = useState(false)
  const st = typeof window !== 'undefined' ? window.__sessionTasks : null
  const hub = onHub(i)
  const k = 'running'
  const meta = runningMeta(i, now)
  const canOpen = i.kind === 'kanban' && !!i.chip?.connectionId && !!i.chip?.profile && typeof st?.openTaskChip === 'function'
  const details = runningDetails(i, now)
  const expandable = details.length > 0 || !!i.evidenceText || canOpen
  const title = `${str(i.chip?.label) || i.label}\n${meta.tip}${expandable ? `\n${open ? 'Click to hide details' : 'Click for details'}` : ''}`
  return jsxs('div', { 'data-panel-row': i.key, 'data-running-row': '', style: { fontSize: 13 }, children: [
    jsxs(expandable ? 'button' : 'div', { ...(expandable ? { type: 'button', 'aria-expanded': open, onClick: () => setOpen(v => !v) } : {}), title,
      style: { display: 'grid', gridTemplateColumns: '12px minmax(0,1fr) auto', columnGap: 8, alignItems: 'center', width: '100%', padding: '3px 0', background: 'transparent', border: 0, font: 'inherit', color: 'inherit', textAlign: 'left', cursor: expandable ? 'pointer' : 'default' }, children: [
        jsx('span', { 'aria-hidden': true, 'data-row-pip': k, ...(hub ? { 'data-row-pip-hub': '' } : {}), style: { ...pipStyle(k, hub), justifySelf: 'center' }, children: hub ? 'H' : undefined }),
        jsx('span', { 'data-running-title': '', style: { minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, children: i.label }),
        meta.text && jsx('span', { 'data-running-meta': '', ...(meta.quiet ? { 'data-quiet': '' } : {}), style: { ...muted, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums', ...(meta.quiet ? { color: C.waiting } : {}) }, children: meta.text })
      ] }),
    open && jsxs('div', { 'data-running-details': '', style: { ...muted, fontSize: 11.5, lineHeight: 1.5, padding: '2px 0 6px 20px', overflowWrap: 'anywhere' }, children: [
      ...details.map((d, n) => jsx('div', { children: d }, n)),
      i.evidenceText && jsx('div', { 'data-evidence': i.evidenceState || '', children: `evidence: ${i.evidenceText}` }),
      canOpen && jsx('button', { type: 'button', 'data-open-card': '', style: { ...btn, minHeight: 26, marginTop: 4, fontSize: 12 }, onClick: () => st.openTaskChip(i.chip), children: 'Open card' })
    ] })
  ] })
}

function CardStatusRow({ i }) {
  const st = typeof window !== 'undefined' ? window.__sessionTasks : null
  const canOpen = !!i.chip.connectionId && !!i.chip.profile && typeof st?.openTaskChip === 'function'
  const request = i.chip.humanRequest
  return jsxs('div', { 'data-card-status': i.key, style: { padding: '6px 0', overflowWrap: 'anywhere', fontSize: 13 }, children: [
    jsx('div', { children: jsx(WaitText, { text: i.detail, chip: i.chip, plain: true }) }),
    jsx('div', { style: muted, children: `Previous request (reference only): ${str(request?.action) || 'Not available.'}` }),
    request?.why && jsx('div', { style: muted, children: str(request.why) }),
    jsx('div', { style: muted, children: 'The owner card still reports blocked; this does not reopen its decision.' }),
    canOpen && jsx('button', { type: 'button', 'data-open-card': '', style: btn, onClick: () => st.openTaskChip(i.chip), children: 'Open card' })
  ] })
}

const clamp = n => ({ display: '-webkit-box', WebkitLineClamp: n, WebkitBoxOrient: 'vertical', overflow: 'hidden' })
const pill = color => ({ fontSize: 10, fontWeight: 700, letterSpacing: 0.4, textTransform: 'uppercase', color, border: '1px solid currentColor', borderRadius: 999, padding: '1px 6px', whiteSpace: 'nowrap' })
const askLinkBtn = { background: 'none', border: 0, padding: 0, font: 'inherit', fontSize: 12, color: ACCENT_TEXT, cursor: 'pointer' }
export const SITUATION_MORE_AT = 140 // characters; without layout (tests, first paint) More shows from here

/** Text clamped to `lines` with a More/Less toggle shown when it actually overflows (measured after paint), or from
 *  SITUATION_MORE_AT characters when layout can't be measured. */
function Clamped({ text, lines, attr }) {
  const ref = useRef(null)
  const [more, setMore] = useState(false)
  const [over, setOver] = useState(text.length > SITUATION_MORE_AT)
  useEffect(() => {
    const el = ref.current
    if (el && el.clientHeight > 0 && !more) setOver(el.scrollHeight > el.clientHeight + 1)
  }, [text, more])
  return jsxs('div', { children: [
    jsx('div', { ref, [attr]: '', style: { fontSize: 12.5, lineHeight: 1.5, overflowWrap: 'anywhere', ...(more ? {} : clamp(lines)) }, children: text }),
    (over || more) && jsx('button', { type: 'button', 'data-ask-more': '', 'aria-expanded': more, onClick: () => setMore(m => !m), style: askLinkBtn, children: more ? 'Less' : 'More' })
  ] })
}

function OptionTile({ o, onChoose }) {
  return jsxs('div', { 'data-ask-option': o.letter || '', ...(o.recommended ? { 'data-recommended': '' } : {}), style: { display: 'grid', gridTemplateColumns: '22px minmax(0,1fr) auto', columnGap: 10, alignItems: 'start', padding: '8px 10px', borderRadius: 6, border: `1px solid ${o.recommended ? ACCENT_FILL : 'var(--border)'}`, background: o.recommended ? `color-mix(in srgb, ${ACCENT_FILL} 9%, transparent)` : 'transparent' }, children: [
    jsx('span', { 'aria-hidden': true, style: { width: 22, height: 22, borderRadius: 999, display: 'grid', placeItems: 'center', fontSize: 11.5, fontWeight: 700, background: o.recommended ? ACCENT_FILL : 'color-mix(in srgb, var(--foreground) 12%, transparent)', color: o.recommended ? '#fff' : 'var(--foreground)' }, children: o.letter || '•' }),
    jsxs('div', { style: { minWidth: 0 }, children: [
      jsxs('div', { style: { display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }, children: [
        jsx('span', { 'data-ask-option-name': '', style: { fontWeight: 600, fontSize: 13 }, children: o.name }),
        o.recommended && jsx('span', { style: pill(ACCENT_TEXT), children: 'Recommended' })
      ] }),
      o.detail && jsx('div', { 'data-ask-option-detail': '', title: o.detail, style: { ...clamp(3), fontSize: 12, lineHeight: 1.45, color: 'var(--muted-foreground)', marginTop: 2 }, children: o.detail }),
      o.recommended && o.reason && jsx('div', { 'data-ask-reason': '', style: { fontSize: 12, lineHeight: 1.45, marginTop: 3, color: ACCENT_TEXT }, children: `Why: ${o.reason}` })
    ] }),
    jsx('button', { type: 'button', 'data-ask-choose': o.letter || '', 'aria-label': `Choose ${o.letter ? `option ${o.letter}: ` : ''}${o.name}`, title: 'Drafts your answer in the chat box; nothing happens until you send it', onClick: onChoose,
      style: { ...btn, minHeight: 28, alignSelf: 'center', ...(o.recommended ? { background: ACCENT_FILL, borderColor: ACCENT_FILL, color: '#fff', fontWeight: 600 } : {}) }, children: `Choose${o.letter ? ` ${o.letter}` : ''}` })
  ] })
}

function AskRow({ i, session }) {
  const st = typeof window !== 'undefined' ? window.__sessionTasks : null
  const canOpen = i.kind === 'kanban' && !!i.chip.connectionId && !!i.chip.profile && typeof st?.openTaskChip === 'function'
  const v = askView(i.chip)
  const draft = text => { void host.composer?.setDraft?.(session, text); host.composer?.focus?.(session) }
  const small = { fontSize: 12, lineHeight: 1.45, overflowWrap: 'anywhere', minWidth: 0 }
  const situation = v.brief?.situation || ''
  const kind = v.decision ? 'Needs your decision' : v.known ? 'Needs you' : 'Needs you · step not shown'
  const details = [
    v.decision && v.options.length > 0 && ['Options in full', v.options.map(o => o.full).join('  ·  ')],
    v.tried && ['What the agent tried', v.tried],
    v.known && (v.yes || v.no) && ['Card buttons', `Unblock = ${v.yes || 'continue'}; Archive = ${v.no || 'drop it'}`],
    v.decision && v.full && ['Ask as filed', v.full],
    i.evidenceText && ['Evidence', i.evidenceText]
  ].filter(Boolean)
  const recLine = text => jsx('div', { 'data-ask-recommend': '', style: { ...small, color: ACCENT_TEXT }, children: `Recommended: ${text}` })
  return jsxs('div', { 'data-panel-row': i.key, 'data-ask-row': v.known ? (v.decision ? 'decision' : 'known') : 'unknown', style: { border: '1px solid var(--border)', borderLeft: `3px solid ${C.needsYou}`, borderRadius: 8, padding: '10px 12px', marginBottom: 8, minWidth: 0, display: 'grid', gap: 8 }, children: [
    jsxs('div', { style: { display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0 }, children: [
      jsx('span', { 'data-ask-kind': '', style: { fontSize: 10.5, fontWeight: 700, letterSpacing: 0.5, textTransform: 'uppercase', color: ALERT_TEXT, whiteSpace: 'nowrap' }, children: kind }),
      jsx('span', { 'data-ask-card': '', title: str(i.chip?.title) || i.label, style: { ...muted, marginLeft: 'auto', fontSize: 10.5, fontFamily: 'var(--font-mono, ui-monospace, monospace)', whiteSpace: 'nowrap' }, children: i.id || '' })
    ] }),
    jsx('div', { 'data-ask-title': '', style: { fontWeight: 600, fontSize: 14, lineHeight: 1.35, overflowWrap: 'anywhere', marginTop: -4 }, children: i.label }),
    situation && jsx(Clamped, { text: situation, lines: 2, attr: 'data-ask-situation' }),
    v.decision && jsxs('div', { 'data-ask-options': '', style: { display: 'grid', gap: 6 }, children: [
      ...v.options.map((o, n) => jsx(OptionTile, { o, onChoose: () => draft(chooseText(i, o)) }, n)),
      v.recommendLine && recLine(v.recommendLine),
      jsx('div', { 'data-ask-next': '', style: { ...muted, fontSize: 11 }, children: 'Choose drafts your answer in the chat box; send it to decide.' })
    ] }),
    !v.decision && jsxs('div', { 'data-ask-step-box': '', style: { borderRadius: 6, padding: '8px 10px', background: 'color-mix(in srgb, var(--foreground) 6%, transparent)', display: 'grid', gap: 4 }, children: [
      jsx('div', { style: { fontSize: 10.5, fontWeight: 700, letterSpacing: 0.4, textTransform: 'uppercase', color: 'var(--muted-foreground)' }, children: v.known ? 'Your step' : 'Step' }),
      jsx('div', { 'data-ask-step': '', title: v.full || undefined, style: { fontSize: 13, lineHeight: 1.45, fontWeight: 500, overflowWrap: 'anywhere' }, children: v.step }),
      v.single && jsx('div', { 'data-ask-single-option': '', style: { ...small, color: 'var(--muted-foreground)' }, children: v.single.full }),
      v.brief?.recommend && recLine(v.brief.recommend),
      !v.known && jsx('div', { 'data-ask-hint': '', style: { ...small, color: 'var(--muted-foreground)' }, children: v.next })
    ] }),
    v.whyLine && jsx('div', { 'data-ask-why': '', style: { ...small, color: 'var(--muted-foreground)' }, children: v.whyLine }),
    !v.decision && jsxs('div', { style: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6 }, children: [
      v.known && jsx('button', { type: 'button', 'data-ask-done': '', style: { ...btn, minHeight: 28, background: ACCENT_FILL, borderColor: ACCENT_FILL, color: '#fff', fontWeight: 600 }, title: 'Drafts "done" to the agent in the chat box; nothing is sent until you send it', onClick: () => draft(doneText(i)), children: 'I did it' }),
      v.known && jsx('button', { type: 'button', 'data-ask-skip': '', style: { ...btn, minHeight: 28 }, title: 'Drafts "skip" to the agent in the chat box', onClick: () => draft(skipText(i)), children: 'Skip it' }),
      !v.known && jsx('button', { type: 'button', 'data-ask-agent': '', style: { ...btn, minHeight: 28 }, title: 'Drafts a question to the agent in the chat box; nothing is sent until you send it', onClick: () => draft(askAgentText(i)), children: 'Ask agent' }),
      v.known && jsx('span', { 'data-ask-next': '', style: { ...muted, fontSize: 11 }, children: 'Buttons draft your answer in the chat box; send it to decide.' })
    ] }),
    (details.length > 0 || canOpen) && jsxs('div', { style: { display: 'flex', alignItems: 'flex-start', gap: 12, minWidth: 0 }, children: [
      details.length > 0 && jsxs('details', { 'data-ask-details': '', style: { ...small, color: 'var(--muted-foreground)', flex: 1, minWidth: 0 }, children: [
        jsx('summary', { style: { cursor: 'pointer', fontSize: 11.5 }, children: 'Details' }),
        ...details.map(([k, t], n) => jsxs('div', { ...(k === 'Evidence' ? { 'data-evidence': i.evidenceState || '' } : {}), style: { marginTop: 4, fontSize: 11.5 }, children: [jsx('span', { style: { fontWeight: 600 }, children: `${k}: ` }), t] }, n))
      ] }),
      canOpen && jsx('button', { type: 'button', 'data-open-card': '', style: { ...askLinkBtn, fontSize: 11.5, ...(details.length ? {} : { marginRight: 'auto' }) }, onClick: () => st.openTaskChip(i.chip), children: 'Open card' })
    ] })
  ] })
}

function StuckCard({ i, session, now }) {
  const st = typeof window !== 'undefined' ? window.__sessionTasks : null
  const canOpen = i.kind === 'kanban' && !!i.chip.connectionId && !!i.chip.profile && typeof st?.openTaskChip === 'function'
  const nudge = () => { void host.composer?.setDraft?.(session, nudgeText(i, now)); host.composer?.focus?.(session) }
  return jsxs('div', { 'data-stuck': i.key, style: { border: `1px solid color-mix(in srgb, ${C.stuck} 45%, transparent)`, borderRadius: 6, padding: '8px 10px', marginBottom: 6 }, children: [
    jsx('div', { style: { fontWeight: 600, fontSize: 13, overflowWrap: 'anywhere' }, children: i.label }),
    jsx('div', { style: { ...muted, marginBottom: 4, overflowWrap: 'anywhere' }, children: jsx(WaitText, { text: i.detail, chip: i.chip }) }),
    i.evidenceText && jsx('div', { 'data-evidence': i.evidenceState || '', style: { ...muted, fontSize: 11, marginBottom: 4, overflowWrap: 'anywhere' }, children: `evidence: ${i.evidenceText}` }),
    jsx('ol', { 'data-unblock-steps': '', style: { margin: '0 0 8px', paddingLeft: 18, fontSize: 12.5, lineHeight: 1.5 }, children: unblockSteps(i, now).map((s, n) => jsx('li', { children: s }, n)) }),
    jsxs('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 6 }, children: [
      jsx('button', { type: 'button', 'data-nudge': '', style: btn, title: 'Drafts a message to the agent in the composer; nothing is sent until you send it', onClick: nudge, children: 'Nudge agent' }),
      canOpen && jsx('button', { type: 'button', 'data-open-card': '', style: { ...btn, borderColor: 'transparent', color: 'var(--muted-foreground)' }, onClick: () => st.openTaskChip(i.chip), children: 'Open card' })
    ] })
  ] })
}

function Fold({ label, items, k }) {
  if (!items.length) return null
  return jsxs('details', { open: true, 'data-fold': label, children: [
    jsx('summary', { style: { cursor: 'pointer', minHeight: 28, display: 'flex', alignItems: 'center', ...muted }, children: label }),
    ...items.map(i => jsx(Row, { i, k }, i.key))
  ] })
}

export function Panel() {
  const [tasks, setTasks] = useState(() => (typeof window !== 'undefined' ? window.__sessionTasks?.snapshot : null) || null)
  const [doneState, setDone] = useState(done)
  const [, setTick] = useState(0)
  const [collapsedKeys, setCollapsedState] = useState(readCollapsed)
  const [finishedScopes, setFinishedScopes] = useState(() => new Set())
  const [reportedScopes, setReportedScopes] = useState(() => new Set())
  const [waitingScopes, setWaitingScopes] = useState(() => new Set())
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
    const offTitles = subscribeTitles(() => setTick(t => t + 1))
    return () => { offTasks?.(); offAsks?.(); offDone(); offTitles() }
  }, [])
  // Kick every source on a switch: user-steps' queue and strip mount only once the panel shows, so a chat whose
  // only open work is an ask used to wait for user-steps' 8 s poll before its panel appeared at all.
  useEffect(() => {
    noteRecent(route)
    void refreshDone(); void window.__sessionTasks?.refresh?.()
    void window.__userSteps?.refresh?.(); void window.__userSteps?.refreshRequests?.()
  }, [scope])
  if (!route.session) return null
  const now = Date.now()
  // Match on the stored chat only: the runtime id lands later (resume), and gating on it hid this chat's
  // already-painted cards until session-tasks' next round (seconds after a switch).
  const chips = tasks && tasks.stored === stored ? tasks.chips || [] : []
  const silence = trackSubagents(chips, now, subMemory)
  noteTitles(chips)
  const isAsked = c => !!US?.requestForSource?.({ gatewayId: c.gatewayId || c.gateway, profile: c.profile, type: 'kanban', board: c.board, id: c.id })
  const presentationFor = c => US?.presentationForSource?.({ gatewayId: c.gatewayId || c.gateway, profile: c.profile, type: 'kanban', board: c.board, id: c.id })
  const sorted = classify(chips, { nowMs: now, silence, isAsked, presentationFor, evidence: c => (c.kind === 'kanban' ? chipObservation(c, now / 1000) : null) })
  const evidenceUnknown = annotateEvidence(sorted, now)
  const asks = typeof US?.openAskCount === 'function' ? US.openAskCount() : 0
  const finished = doneState.scope === scope ? doneState.rows : cachedDone(scope)
  const n = counts(sorted, { asks, done: finished ? finished.filter(d => !d.archived).length : null })
  const finishedOpen = finishedScopes.has(scope)
  const toggleFinished = () => setFinishedScopes(previous => {
    const next = new Set(previous)
    if (next.has(scope)) next.delete(scope)
    else next.add(scope)
    return next
  })
  // Waiting rows are folded by default: the heading carries what they wait on as counts; open is per chat.
  const waitingOpen = waitingScopes.has(scope)
  const toggleWaiting = () => setWaitingScopes(previous => {
    const next = new Set(previous)
    if (next.has(scope)) next.delete(scope)
    else next.add(scope)
    return next
  })
  const waitingBreakdown = waitBreakdown(sorted.waiting)
  const reported = sorted.reported
  const reportedOpen = reportedScopes.has(scope)
  const toggleReported = () => setReportedScopes(previous => {
    const next = new Set(previous)
    if (next.has(scope)) next.delete(scope)
    else next.add(scope)
    return next
  })
  // A legacy card listed above as an unasked row is the same exact qualified card the strip's "Earlier asks"
  // would show again: hand those identities to the strip so each card renders once. Exact identity only.
  const legacy = US?.Strip ? jsx(US.Strip, { hideCards: sorted.unasked.map(i => i.chip) }, 'legacy') : null
  // Keep finished history reachable even after the final open card closes.
  if (!finished?.length && !US?.hasQueuePresentation?.() && !hasOpenWork(sorted, { asks, steps: US?.snapshot?.ledger?.steps, issues: US?.snapshot?.issues })) return null
  // The user-steps bridge qualifies these by owner/request/revision. A count alone cannot distinguish
  // a replacement ask; old bridges without identity must fail open rather than hide an unknown set.
  const askKeys = typeof US?.openAskKeys === 'function' ? US.openAskKeys() : []
  const attention = attentionKeys(askKeys, sorted)
  // Empty attention only matches an explicitly empty fold. Otherwise every([])
  // would reuse a fold saved for answered asks (including legacy asks:N keys).
  const collapsed = askKeys.length === asks && !!collapsedKeys &&
    (attention.length > 0 ? attention.every(k => collapsedKeys.includes(k)) : collapsedKeys.length === 0)
  const toggle = () => { const next = collapsed ? null : attention; writeCollapsed(next); setCollapsedState(next) }
  // `busy` is this chat's own turn (the agent writing a reply), not card work: say so, or "working" next to
  // "2 waiting" reads as a card running when none is.
  const status = busy ? 'Agent replying in this chat' : sorted.lastActivity ? `Idle · last activity ${ago(sorted.lastActivity, now)}` : 'Idle'
  // Collapsed, a bare "2 waiting" left the user asking "on what?": keep one short line per waiting card.
  const waitingRows = [...sorted.blockedUpstream, ...sorted.waiting]
  const foldRows = sorted.waiting.filter(i => !i.cardStatus) // cardStatus rows always render in full
  const needsYou = [
    (asks > 0 || sorted.unasked.length > 0) && jsx('h3', { 'data-section': 'needs-you', style: heading(C.needsYou), children: `Needs you · ${asks + sorted.unasked.length}` }, 'h-needs-you'),
    US?.ActionQueue && jsx(US.ActionQueue, { embedded: true, audience: 'human' }, 'queue'),
    ...sorted.unasked.map(i => jsx(AskRow, { i, session: route.session }, i.key)),
    legacy
  ].filter(Boolean)
  const summaryParts = PIP_ORDER.filter(k => n[k]).map(k => ({ k, text: `${n[k]} ${LABEL[k]}` }))
  const otherSections = asks + sorted.unasked.length + sorted.stuck.length + sorted.blockedUpstream.length + sorted.waiting.length > 0
  // Rows whose source evidence is unknown (expired, missing from an older producer, or SDK projector absent) are
  // counted apart: they are neither green nor zero.
  if (reported.length) summaryParts.push({ k: 'reported', text: `${reported.length} reported finished, unconfirmed` })
  if (evidenceUnknown) summaryParts.push({ k: 'evidence', text: `${evidenceUnknown} evidence unknown` })
  // Same chrome as the composer status stack's sections (StatusSection): a full-width disclosure row with a caret,
  // a glyph and a muted label. The caret is the minimize toggle; folded, only this row (and waiting one-liners)
  // stays. A folded ask is never muted: its count keeps the needs-you colour, and a new ask reopens the panel.
  const trigger = 'status-section-trigger flex min-w-0 flex-1 items-center gap-1.5 px-2 py-1 text-left text-xs font-normal text-muted-foreground/92 transition-colors hover:text-foreground/90'
  return jsxs('section', { 'data-session-panel': '', 'data-slot': 'status-section', 'aria-label': 'Session status', style: { width: '100%', minWidth: 0, boxSizing: 'border-box', paddingBottom: collapsed ? 2 : 6, borderBottom: '1px solid color-mix(in srgb, var(--border) 60%, transparent)', color: 'var(--foreground)', userSelect: 'text', WebkitUserSelect: 'text' }, children: [
    jsx('style', { 'data-wait-css': '', children: WAIT_CSS }, 'wait-css'),
    jsx('div', { className: 'status-section-header flex items-center gap-1 pr-1', children: jsxs('button', { type: 'button', 'data-panel-toggle': '', className: trigger, 'aria-expanded': !collapsed, 'aria-label': collapsed ? 'Expand session status' : 'Collapse session status', title: collapsed ? 'Expand' : 'Collapse (reopens when something new needs you or gets stuck)', onClick: toggle, style: { background: 'transparent', border: 0, cursor: 'pointer', minWidth: 0 }, children: [
      jsx(DisclosureCaret, { className: 'shrink-0', open: !collapsed, size: '1em' }),
      jsx('span', { className: 'status-section-icon flex shrink-0 items-center', children: jsx(Codicon, { className: 'text-muted-foreground/70', name: 'pulse', size: '0.8rem' }) }),
      jsx('span', { 'data-panel-title': '', className: 'shrink-0', children: 'This session' }),
      jsxs('span', { 'data-session-status': '', className: 'min-w-0 truncate', style: { display: 'flex', alignItems: 'center', gap: 5, opacity: 0.8 }, children: [busy && jsx('span', { 'aria-hidden': true, style: { color: C.running }, children: '●' }), status] }),
      jsx(Pips, { n, hub: hubCounts(sorted) }),
      summaryParts.length > 0 && jsx('span', { 'data-status-summary': '', ...(collapsed ? { 'data-collapsed-summary': '' } : {}), className: 'min-w-0 truncate', children: summaryParts.flatMap((p, idx) => [idx > 0 && ' · ', jsx('span', { style: p.k === 'needsYou' || p.k === 'stuck' ? { color: C[p.k], fontWeight: 600 } : undefined, children: p.text }, p.k)].filter(Boolean)) })
    ] }) }),
    // Collapsed, a bare "2 waiting" left the user asking "on what?": keep one short line per waiting card.
    collapsed && waitingRows.length > 0 && jsx('div', { style: { padding: '0 12px 2px 30px', minWidth: 0 }, children: jsx(WaitList, { rows: waitingRows, now, kindOf: lineKind, limit: WAIT_LINES_COLLAPSED, attrs: { 'data-collapsed-waiting': '' } }) }),
    !collapsed && jsxs('div', { 'data-panel-body': '', className: 'status-section-body', style: { maxHeight: '55vh', overflowY: 'auto', minWidth: 0, padding: '0 10px 0 12px' }, children: [
      ...needsYou,
      US?.ActionQueue && jsx('div', { 'data-agent-deliveries': '', style: { color: 'var(--muted-foreground)' }, children: jsx(US.ActionQueue, { embedded: true, audience: 'agent' }, 'agent-queue') }),
      sorted.stuck.length > 0 && jsx('h3', { 'data-section': 'stuck', style: heading(C.stuck), children: `Stuck · ${sorted.stuck.length}` }),
      ...sorted.stuck.map(i => jsx(StuckCard, { i, session: route.session, now }, i.key)),
      sorted.blockedUpstream.length > 0 && jsx('h3', { 'data-section': 'blocked-upstream', style: heading(C.blockedUpstream), children: `Blocked upstream · ${sorted.blockedUpstream.length}` }),
      jsx(WaitList, { rows: sorted.blockedUpstream, now, kindOf: () => 'blockedUpstream' }, 'wl-bu'),
      // The header already says "N running" with N blue boxes: the heading only earns its line when other
      // sections share the body and the running rows need telling apart from them.
      sorted.active.length > 0 && otherSections &&
        jsx('h3', { 'data-section': 'in-progress', style: heading('var(--muted-foreground)'), children: `In progress · ${sorted.active.length}` }),
      sorted.active.length > 0 && jsx('div', { 'data-running-list': '', style: { paddingTop: otherSections ? 0 : 4 }, children: sorted.active.map(i => jsx(RunningRow, { i, now }, i.key)) }),
      sorted.waiting.length > 0 && jsxs('button', { type: 'button', 'data-waiting-toggle': '', 'aria-expanded': waitingOpen, onClick: toggleWaiting, title: waitingOpen ? 'Hide waiting cards' : 'Show waiting cards', style: { display: 'flex', alignItems: 'baseline', flexWrap: 'wrap', gap: 8, width: '100%', margin: '12px 0 6px', padding: 0, background: 'transparent', border: 0, cursor: 'pointer', font: 'inherit', textAlign: 'left', color: 'var(--muted-foreground)' }, children: [
        jsx('span', { 'aria-hidden': true, style: { fontSize: 10, width: 10 }, children: waitingOpen ? '▾' : '▸' }),
        jsx('h3', { 'data-section': 'waiting', style: { ...heading('var(--muted-foreground)'), margin: 0 }, children: `Waiting · ${sorted.waiting.length}` }),
        jsx('span', { 'data-waiting-breakdown': '', style: muted, children: waitingBreakdown.text })
      ] }),
      // Folded, the counts alone ("1 other") left the user asking "on what?": name up to 3 cards and what each
      // waits on, one line each, as the collapsed panel does. Opening the fold shows the full rows.
      !waitingOpen && foldRows.length > 0 && jsx(WaitList, { rows: foldRows, now, limit: WAIT_LINES_COLLAPSED, attrs: { 'data-folded-waiting': '' } }, 'wl-fold'),
      // A still-blocked owner whose ask was retired has no other surface: its status row is never folded away.
      ...(waitingOpen ? [] : sorted.waiting.filter(i => i.cardStatus).map(i => jsx(Row, { i, k: 'waiting' }, i.key))),
      waitingOpen && jsx(WaitList, { rows: sorted.waiting, now, attrs: { 'data-open-waiting': '' } }, 'wl-open'),
      (finished?.length || reported.length || sorted.ready.length || sorted.queued.length) ? jsxs('div', { style: { borderTop: '1px solid color-mix(in srgb, var(--border) 60%, transparent)', marginTop: 10, paddingTop: 4 }, children: [
        jsx(Fold, { label: `Ready · ${sorted.ready.length}`, items: sorted.ready, k: 'ready' }),
        jsx(Fold, { label: `Not started · ${sorted.queued.length}`, items: sorted.queued, k: 'queued' }),
        reported.length > 0 && jsxs('div', { 'data-reported-cards': '', children: [
          jsx('button', { type: 'button', 'data-reported-toggle': '', 'aria-expanded': reportedOpen, onClick: toggleReported, title: 'Cards on another gateway that reported closing but were never confirmed to this chat (for example, a worker that was retired)', style: { ...btn, ...muted }, children: `${reported.length} reported finished, not confirmed` }),
          reportedOpen && reported.map(i => jsx(Row, { i, k: 'done' }, i.key))
        ] }),
        finished?.length > 0 && jsxs('div', { 'data-finished-cards': '', children: [
          jsx('button', { type: 'button', 'data-finished-toggle': '', 'aria-expanded': finishedOpen, onClick: toggleFinished, style: { ...btn, ...muted }, children: `${finished.length} finished` }),
          finishedOpen && finished.map(d => jsx(Row, { i: { key: d.key, label: d.label, detail: d.archived ? 'archived' : d.at ? `finished ${ago(d.at, now)}` : '' }, k: 'done' }, d.key))
        ] })
      ] }) : null
    ] })
  ] })
}

export default {
  id: ID,
  name: 'Session Panel',
  register(ctx) {
    if (typeof window !== 'undefined') window.__sessionPanel = { active: true, version: VERSION, refreshDone, warmRecent }
    ctx.register({ id: 'panel', area: COMPOSER_AREAS.top, order: -30, render: () => jsx(Panel, {}) })
    // First pass shortly after launch (covers the first switch after a relaunch), then every WARM_TICK_MS.
    const idle = fn => (typeof window !== 'undefined' && typeof window.requestIdleCallback === 'function' ? window.requestIdleCallback(fn, { timeout: 5_000 }) : fn())
    const tick = () => idle(() => void warmRecent())
    const first = setTimeout(() => idle(() => void warmRecent({ force: true })), 4_000)
    const warmTimer = setInterval(tick, WARM_TICK_MS)
    return () => {
      clearTimeout(first)
      clearInterval(warmTimer)
      if (typeof window !== 'undefined' && window.__sessionPanel?.version === VERSION) delete window.__sessionPanel
      if (doneTimer) clearInterval(doneTimer)
      doneTimer = null
    }
  }
}

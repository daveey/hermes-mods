/**
 * Session Panel — one panel above the composer that answers, for the focused chat:
 *   is work moving, how much is left, what is waiting on me, and what is stuck.
 *
 *   header       agent replying in this chat / idle since …, then one small box per kanban card and the counts:
 *                needs you · stuck · running · ready · waiting · not started · done
 *                colours: red = needs you, green = ready, yellow = not ready (waiting / not started), grey = done,
 *                blue = running, orange = stuck
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
  if (!split.present) return missingView('producer_sent_no_observations')
  const candidates = split.work.filter(r => {
    const id = obsOwn(r, 'identity')
    const kind = chip.kind === 'bg' ? 'process' : chip.kind === 'sub' ? 'subagent' : 'card'
    if (obsOwn(id, 'kind') !== kind || obsOwn(id, 'work_id') !== chip.id) return false

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
export function classify(chips, { nowMs = Date.now(), silence = new Map(), isAsked = () => false, presentationFor = () => null, evidence = () => null } = {}) {
  const now = nowMs / 1000
  const out = { active: [], stuck: [], waiting: [], ready: [], queued: [], unasked: [], lastActivity: 0 }
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
    const on = c.waitingOn ? `waiting on ${c.waitingOn}${c.waitingSince ? ` since ${clock(c.waitingSince, nowMs)}` : ''}` : ''
    // A stale chip is session-tasks' last-known copy after a failed read: the card may have been answered,
    // re-blocked or archived since, so it is never stuck (no nudge) or needs-you, only "status unknown".
    if (c.stale) { out.waiting.push({ ...item(c), detail: `status unknown · last known: ${c.look === 'needs-you' ? 'needs you' : c.state || 'unknown'}${last ? ` (${ago(last, nowMs)})` : ''}; the card could not be re-read` }); continue }
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
    // legacy fields still say (a cached running card must not read as running). A card with NO evidence (older
    // producer) keeps its legacy section; the explicit unknown line is shown on its row instead.
    const ev = evidence(c)
    if (ev?.sourced && ev.state === 'unknown') {
      out.waiting.push({ ...item(c), detail: `status unknown${ev.reason ? ` (${ev.reason.replace(/_/g, ' ')})` : ''} · last known: ${ev.lastKnown || c.state || 'unknown'}${ev.ageSec != null ? ` (observed ${span(ev.ageSec)} ago)` : ''}` })
      continue
    }
    if (c.look === 'stalled') { out.stuck.push({ ...item(c), reason: 'stalled', detail: `stalled: ${on || 'agent-owned wait over 2 h'}` }); continue }
    if (c.state === 'running') {
      if (progress && now - progress >= STUCK_CARD_S) out.stuck.push({ ...item(c), reason: 'no-progress', lastAt: progress, detail: `running, no progress since ${clock(progress, nowMs)} (${ago(progress, nowMs)})${c.activity ? ` · last event: ${c.activity}` : ''}` })
      else out.active.push({ ...item(c), detail: `running${c.assignee ? ` · ${c.assignee}` : ''}${progress ? ` · ${ago(progress, nowMs)}` : ' · last event unknown'}` })
      continue
    }
    if (c.state === 'review') { out.active.push({ ...item(c), detail: `in review${last ? ` · ${ago(last, nowMs)}` : ''}` }); continue }
    if (c.state === 'ready') { out.ready.push({ ...item(c), detail: 'ready to start' }); continue }
    if (c.state === 'todo') { out.queued.push({ ...item(c), detail: 'not started' }); continue }
    out.waiting.push({ ...item(c), detail: on || (c.state === 'scheduled' ? 'scheduled' : c.state === 'unknown' ? 'status unknown' : c.state) })
  }
  return out
}

/** True when the chat has anything not finished: an ask, a card/sub-agent/process that is open, an open ledger
 *  step or an agent issue. Done/archived cards never count as open work; history has its own disclosure. */
export function hasOpenWork(sorted, { asks = 0, steps = [], issues = [] } = {}) {
  const open = list => (list || []).some(i => !(i.kind === 'kanban' && TERMINAL.has(str(i.chip?.state))))
  return !!(asks || sorted.unasked.length || open(sorted.active) || open(sorted.stuck) || open(sorted.waiting) || open(sorted.ready) || open(sorted.queued) ||
    (Array.isArray(steps) && steps.some(s => s?.id && (s.status || 'open') === 'open')) || (Array.isArray(issues) && issues.length))
}

/** Attach each work row's evidence line (projected at `nowMs`, never cached); returns how many rows have SOURCE evidence that is unknown (rows with none show their own explicit unknown line). */
export function annotateEvidence(sorted, nowMs, opts) {
  let unknown = 0
  for (const list of [sorted.active, sorted.stuck, sorted.waiting, sorted.queued, sorted.unasked]) {
    for (const i of list) {
      if (!['kanban', 'bg', 'sub'].includes(i.kind)) continue
      const v = chipObservation(i.chip, nowMs / 1000, opts)
      i.evidenceText = chipEvidenceLine(i.chip, nowMs / 1000, opts)
      i.evidenceState = v.state
      if (v.state === 'unknown' && (v.sourced || i.kind === 'bg' || i.kind === 'sub')) unknown++
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

/** Pip/summary counts: one per row the panel lists, so "In progress · 1" always shows one running box. Sub-agents,
 *  background processes and rented machines count like cards (a sub-agent alone used to show no blue box).
 *  Needs you = open asks plus unasked cards that need the user. */
export function counts(sorted, { asks = 0, done = null } = {}) {
  return {
    done: done ?? 0,
    running: sorted.active.length,
    needsYou: asks + sorted.unasked.length,
    stuck: sorted.stuck.length,
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
  running: '#3b82f6', stuck: '#f0883e'
}
const LABEL = { done: 'done', running: 'running', needsYou: 'needs you', stuck: 'stuck', ready: 'ready', waiting: 'waiting', queued: 'not started' }
const muted = { color: 'var(--muted-foreground)', fontSize: 12 }
const heading = color => ({ margin: '12px 0 6px', fontSize: 12, fontWeight: 600, color })
const btn = { minHeight: 32, padding: '4px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'transparent', color: 'var(--foreground)', font: 'inherit', fontSize: 12.5, cursor: 'pointer' }

// One small box per card, in the header beside the status: a full-width bar spent a whole row to say "1 waiting".
// Shape carries state as well as colour: open work is filled, waiting is an outline, not started is dashed.
const PIP_ORDER = ['needsYou', 'stuck', 'running', 'ready', 'waiting', 'queued', 'done']
export const PIPS_MAX = 24 // boxes drawn before the rest fold into "+N"
// A hub-scheduled card's box is a little larger and carries an "H" (running and waiting work alike).
const pipStyle = (k, hub = false) => {
  const base = { width: 9, height: 9, borderRadius: 2, boxSizing: 'border-box', flex: 'none' }
  const h = hub ? { width: 12, height: 12, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 8.5, fontWeight: 700, lineHeight: 1 } : {}
  if (k === 'waiting') return { ...base, ...h, border: `1.5px solid ${C.waiting}`, color: C.waiting }
  if (k === 'queued') return { ...base, ...h, border: `1.5px dashed ${C.queued}`, color: C.queued }
  return { ...base, ...h, background: C[k], color: 'var(--card, #fff)' }
}

/** Per pip state, how many of its open cards are scheduled on the hub (session-tasks `runMachine`). */
const onHub = i => i.kind === 'kanban' && i.chip?.runMachine === 'hub'
export function hubCounts(sorted) {
  const on = list => (list || []).filter(onHub).length
  return { running: on(sorted.active), stuck: on(sorted.stuck), waiting: on(sorted.waiting), ready: on(sorted.ready), queued: on(sorted.queued) }
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
      i.detail && jsx('div', { style: { ...muted, overflowWrap: 'anywhere' }, children: i.detail }),
      i.evidenceText && jsx('div', { 'data-evidence': i.evidenceState || '', style: { ...muted, fontSize: 11, overflowWrap: 'anywhere' }, children: `evidence: ${i.evidenceText}` })
    ] })
  ] })
}

function CardStatusRow({ i }) {
  const st = typeof window !== 'undefined' ? window.__sessionTasks : null
  const canOpen = !!i.chip.connectionId && !!i.chip.profile && typeof st?.openTaskChip === 'function'
  const request = i.chip.humanRequest
  return jsxs('div', { 'data-card-status': i.key, style: { padding: '6px 0', overflowWrap: 'anywhere', fontSize: 13 }, children: [
    jsx('div', { children: i.detail }),
    jsx('div', { style: muted, children: `Previous request (reference only): ${str(request?.action) || 'Not available.'}` }),
    request?.why && jsx('div', { style: muted, children: str(request.why) }),
    jsx('div', { style: muted, children: 'The owner card still reports blocked; this does not reopen its decision.' }),
    canOpen && jsx('button', { type: 'button', 'data-open-card': '', style: btn, onClick: () => st.openTaskChip(i.chip), children: 'Open card' })
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
    i.evidenceText && jsx('div', { 'data-evidence': i.evidenceState || '', style: { ...muted, fontSize: 11, overflowWrap: 'anywhere' }, children: `evidence: ${i.evidenceText}` }),
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
  // A legacy card listed above as an unasked row is the same exact qualified card the strip's "Earlier asks"
  // would show again: hand those identities to the strip so each card renders once. Exact identity only.
  const legacy = US?.Strip ? jsx(US.Strip, { hideCards: sorted.unasked.map(i => i.chip) }, 'legacy') : null
  // Keep finished history reachable even after the final open card closes.
  if (!finished?.length && !US?.hasQueuePresentation?.() && !hasOpenWork(sorted, { asks, steps: US?.snapshot?.ledger?.steps, issues: US?.snapshot?.issues })) return null
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
  // Rows whose source evidence is unknown (expired, missing from an older producer, or SDK projector absent) are
  // counted apart: they are neither green nor zero.
  if (evidenceUnknown) summaryParts.push({ k: 'evidence', text: `${evidenceUnknown} evidence unknown` })
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
      ...sorted.active.map(i => jsx(Row, { i, k: 'running' }, i.key)),
      sorted.waiting.length > 0 && jsx('h3', { 'data-section': 'waiting', style: heading('var(--muted-foreground)'), children: `Waiting · ${sorted.waiting.length}` }),
      ...sorted.waiting.map(i => jsx(Row, { i, k: 'waiting' }, i.key)),
      (finished?.length || sorted.ready.length || sorted.queued.length) ? jsxs('div', { style: { borderTop: '1px solid color-mix(in srgb, var(--border) 60%, transparent)', marginTop: 10, paddingTop: 4 }, children: [
        jsx(Fold, { label: `Ready · ${sorted.ready.length}`, items: sorted.ready, k: 'ready' }),
        jsx(Fold, { label: `Not started · ${sorted.queued.length}`, items: sorted.queued, k: 'queued' }),
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

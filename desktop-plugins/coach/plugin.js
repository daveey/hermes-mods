/**
 * Coach — desktop half of the nudge bus. One panel above the composer, ONE primary button + Dismiss.
 *
 * The backend (plugins/coach, /api/plugins/coach) decides WHAT may show: it evaluates the user-channel rules from
 * the signals the agent hooks record and passes them through one gate (at most 1 nudge per session per 15 user
 * turns, a dismissal mutes that rule for the session, 3 dismissals in a week demote it to digest-only). Every
 * shown nudge and every choice lands in <HERMES_HOME>/coach/log.jsonl.
 *
 *   still_broken        3rd "still not working" turn -> Restart clean (new session with brief + repro + done-check)
 *   long_session        >250 msgs                    -> Spin out (hands over to the spin-out plugin's planner)
 *                                                       or Compact (runs /compress in the session)
 *   screenshot_midturn  image sent while busy        -> Add a line (prefills the composer)
 *   move_to_board       10th subagent / >10 min job  -> Move to board (handoff card)
 *   hermes_bug          same Hermes bug in 2 sessions -> File it once (card on the hermes board)
 *   tip:<id>            a curriculum trigger fired     -> Try it (prefills the drill) / Got it  (lowest priority;
 *                       spaced repetition + retirement on adoption are decided server side, plugins/coach/curriculum.py)
 *
 * When it checks: after a turn finishes in the focused session (busy -> idle), every 60 s while that turn runs, and
 * on a screenshot send. It never shows while another bus panel (spin-out, topic-shift, prompt-coach) is up for the
 * session. Those plugins share the gate through window.__coach.claim(stored, rule) / report(stored, rule, event).
 */
import { COMPOSER_AREAS, host, PALETTE_AREA, useValue } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useSyncExternalStore } from 'react'

const ID = 'coach'
const AFTER_TURN_MS = 1800 // after spin-out's own 1.5 s check, so its panel wins when both apply
const BUSY_POLL_MS = 60000
const ON_KEY = 'coach.enabled'

let ctxRef = null
let panel = null // { stored, runtime, profile, nudge, phase: 'shown'|'working'|'done', note, error }
const facts = new Map() // stored -> { muted: [], demoted: [], turn }
const subs = new Set()
const emit = () => subs.forEach(fn => fn())
const setPanel = next => {
  panel = next
  emit()
}
const patch = p => panel && setPanel({ ...panel, ...p })
const usePanel = () => useSyncExternalStore(fn => (subs.add(fn), () => subs.delete(fn)), () => panel, () => panel)
const log = (...a) => console.info('[coach]', ...a)
const enabled = () => {
  try {
    return window.localStorage.getItem(ON_KEY) !== 'off'
  } catch {
    return true
  }
}

// ── the other panels on the bus ─────────────────────────────────────────
const PEERS = {
  'spin-out': stored => {
    const j = window.__spinOut?.job
    return !!(j && j.parent === stored && j.phase !== 'error' && !(j.phase === 'done'))
  },
  'topic-shift': stored => {
    const f = window.__topicShift?.flag
    return !!(f && f.stored === stored)
  },
  'prompt-coach': stored => {
    const f = window.__promptCoach?.flag
    return !!(f && (!f.stored || f.stored === stored))
  }
}
function busyElsewhere(stored, except) {
  if (except !== ID && panel && panel.stored === stored && panel.phase !== 'done') return true
  return Object.entries(PEERS).some(([name, visible]) => name !== except && visible(stored))
}

// Peer API (prompt-coach / topic-shift): sync, fail-open on their side when window.__coach is absent.
function claim(stored, rule) {
  if (!stored) return true
  if (busyElsewhere(stored, rule)) return false
  const f = facts.get(stored)
  return !(f && (f.muted?.includes(rule) || f.demoted?.includes(rule)))
}
function report(stored, rule, event) {
  if (!stored || !ctxRef) return
  const f = facts.get(stored) || {}
  if (event === 'dismissed') facts.set(stored, { ...f, muted: [...(f.muted || []), rule] })
  ctxRef.rest('/event', { method: 'POST', body: { session_id: stored, rule, event, turn: f.turn ?? null } }).catch(e => log('event', e))
}

// ── checks ──────────────────────────────────────────────────────────────
function show(stored, res) {
  facts.set(stored, { muted: res?.muted || [], demoted: res?.demoted || [], turn: res?.turn })
  const n = res?.nudge
  if (!n) return false
  if (host.state.focusedStoredSessionId.get() !== stored || busyElsewhere(stored, ID)) {
    // Lost the race to another panel or the user moved on: the show was already logged, so record it as expired.
    ctxRef.rest('/event', { method: 'POST', body: { session_id: stored, rule: n.rule, event: 'expired', id: n.id } }).catch(() => {})
    return false
  }
  setPanel({ stored, runtime: host.state.focusedSessionId.get(), profile: host.state.focusedSessionProfile.get() || 'default', nudge: n, phase: 'shown' })
  return true
}

let inflight = false
async function check(stored = host.state.focusedStoredSessionId.get()) {
  if (!enabled() || !stored || !ctxRef || inflight) return false
  if (busyElsewhere(stored, ID)) return false
  inflight = true
  try {
    return show(stored, await ctxRef.rest('/state', { method: 'POST', body: { session_id: stored }, timeoutMs: 15000 }))
  } catch (e) {
    log('state failed', e)
    return false
  } finally {
    inflight = false
  }
}

const words = t => (t || '').trim().split(/\s+/).filter(Boolean).length
async function onSent(draft) {
  const stored = host.state.focusedStoredSessionId.get()
  const runtime = host.state.focusedSessionId.get()
  if (!enabled() || !stored || !ctxRef) return
  const images = (draft?.attachments || []).filter(a => a?.kind === 'image').length
  const busy = !!(runtime && host.state.busyBySession.get()?.[runtime])
  if (!images || !busy || words(draft?.text) >= 3 || busyElsewhere(stored, ID)) return
  try {
    show(stored, await ctxRef.rest('/offer', { method: 'POST', body: { session_id: stored, rule: 'screenshot_midturn' }, timeoutMs: 15000 }))
  } catch (e) {
    log('offer failed', e)
  }
}

function watch(ctx) {
  let prev = {}
  let timer = null
  const onChange = () => {
    const map = host.state.busyBySession.get() || {}
    const runtime = host.state.focusedSessionId.get()
    const finished = runtime && prev[runtime] && !map[runtime]
    prev = { ...map }
    if (!finished) return
    const stored = host.state.focusedStoredSessionId.get()
    clearTimeout(timer)
    timer = setTimeout(() => void check(stored), AFTER_TURN_MS)
  }
  const unsub = host.state.busyBySession?.subscribe(onChange)
  if (unsub) ctx.onDispose?.(unsub)
  // Mid-turn: subagent fan-out and long jobs show up while the turn is still running.
  const poll = setInterval(() => {
    const runtime = host.state.focusedSessionId.get()
    if (runtime && host.state.busyBySession.get()?.[runtime]) void check()
  }, BUSY_POLL_MS)
  ctx.onDispose?.(() => {
    clearTimeout(timer)
    clearInterval(poll)
  })
}

// ── actions (one per rule) ──────────────────────────────────────────────
const accept = p => report(p.stored, p.nudge.rule, 'accepted')

async function launch(p, msg) {
  const created = await host.request('session.create', { profile: msg.profile || p.profile, ...(msg.cwd ? { cwd: msg.cwd } : {}), title: msg.title }, 60000)
  const sid = created?.session_id
  if (!sid) throw new Error('session.create returned no id')
  const sub = await host.request('prompt.submit', { session_id: sid, text: msg.message, profile: msg.profile || p.profile }, 60000)
  if (!['streaming', 'queued'].includes(sub?.status)) throw new Error(`submit status ${sub?.status || 'unknown'}`)
  return created?.stored_session_id || null
}

// Extra ghost buttons next to a rule's primary one (frontend-only; the backend still names one primary).
const SECONDARY = { spin_out: { label: 'Compact', action: 'compact' } }

async function act(override) {
  const p = panel
  if (!p || p.phase !== 'shown') return
  const { data } = p.nudge
  const action = override || p.nudge.action
  patch({ phase: 'working', error: null })
  try {
    if (action === 'restart') {
      patch({ note: 'Writing the restart brief…' })
      const b = await ctxRef.rest('/restart', { method: 'POST', body: { session_id: p.stored, profile: p.profile }, timeoutMs: 180000 })
      if (b?.error || !b?.message) throw new Error(b?.error || 'no brief')
      const stored = await launch(p, b)
      accept(p)
      setPanel({ ...p, phase: 'done', note: `Clean session started: ${b.title}`, child: stored })
      if (stored) host.openSession?.(stored)
    } else if (action === 'spin_out') {
      if (!window.__spinOut?.spinOut) throw new Error('the spin-out plugin is not loaded')
      accept(p)
      setPanel(null) // spin-out's own panel takes over
      await window.__spinOut.spinOut()
    } else if (action === 'compact') {
      // The app's own /compress path (transcript swap, progress notice, stale-runtime recovery).
      if (!host.composer?.submit?.(p.stored, '/compress')) throw new Error('no visible composer for this session')
      accept(p)
      setPanel(null)
    } else if (action === 'tip') {
      accept(p)
      if (data?.try) await host.composer?.setDraft?.(p.stored, data.try)
      setPanel(null)
    } else if (action === 'add_text') {
      accept(p)
      await host.composer?.setDraft?.(p.stored, 'About the screenshot: ')
      setPanel(null)
    } else if (action === 'board') {
      patch({ note: 'Writing the card…' })
      const r = await ctxRef.rest('/board', { method: 'POST', body: { session_id: p.stored, profile: p.profile }, timeoutMs: 180000 })
      const card = (r?.cards || []).find(c => c.task_id)
      if (!card) throw new Error(r?.error || r?.cards?.[0]?.error || 'card not created')
      accept(p)
      host.notify({ kind: 'success', message: `Moved to the ${r.board || 'Kanban'} board: ${card.task_id}` })
      setPanel({ ...p, phase: 'done', note: `On the ${r.board || 'Kanban'} board as ${card.task_id}`, board: true })
    } else if (action === 'file_bug') {
      const r = await ctxRef.rest('/file-bug', { method: 'POST', body: { session_id: p.stored, sig: data?.sig || '' }, timeoutMs: 60000 })
      const card = (r?.cards || []).find(c => c.task_id)
      if (!card) throw new Error(r?.error || r?.cards?.[0]?.error || 'card not created')
      accept(p)
      host.notify({ kind: 'success', message: `Filed on the ${r.board} board: ${card.task_id}` })
      setPanel({ ...p, phase: 'done', note: `Filed as ${card.task_id} on the ${r.board} board`, board: true })
    } else if (action === 'tip') {
      // Instant: pre-fill the composer with the tip's "try" text (if any) and close.
      accept(p)
      setPanel(null)
      if (data?.try) await host.composer?.setDraft?.(p.stored, data.try)
    } else {
      throw new Error(`unknown action: ${action}`)
    }
  } catch (err) {
    patch({ phase: 'shown', note: null, error: String(err?.message || err) })
  }
}

function dismiss() {
  const p = panel
  if (!p) return
  if (p.phase !== 'done') report(p.stored, p.nudge.rule, 'dismissed')
  setPanel(null)
}

// ── UI ──────────────────────────────────────────────────────────────────
const BTN = 'inline-flex h-6 items-center rounded-md px-2 text-[0.75rem] font-medium transition-colors disabled:opacity-50 '
const PRIMARY = BTN + 'bg-(--ui-accent) text-(--ui-accent-foreground,white) hover:opacity-90'
const GHOST = BTN + 'border border-(--ui-stroke-secondary) text-(--ui-text-secondary) hover:bg-(--chrome-action-hover) hover:text-foreground'

function Panel() {
  const p = usePanel()
  const focused = useValue(host.state.focusedStoredSessionId)
  if (!p || p.stored !== focused) return null
  const n = p.nudge
  const working = p.phase === 'working'
  const done = p.phase === 'done'
  return jsxs('div', {
    'data-slot': 'coach-panel',
    'data-rule': n.rule,
    className: 'flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-(--ui-stroke-secondary) px-2.5 py-1.5 text-[0.75rem]',
    children: [
      jsx('span', { className: 'text-(--ui-accent)', children: '✦' }),
      jsxs('span', {
        className: 'min-w-0 flex-1 text-(--ui-text-secondary)',
        children: [
          jsx('span', { className: 'font-medium text-foreground', children: n.title }),
          jsx('span', { className: 'ml-1.5', children: `· ${done ? p.note : n.text}` }),
          working && p.note && jsx('span', { className: 'ml-1.5 text-(--ui-text-tertiary)', children: `· ${p.note}` }),
          p.error && jsx('span', { className: 'ml-1.5 text-(--ui-danger,#f85149)', children: `· ${p.error}` })
        ]
      }),
      !done && jsx('button', { type: 'button', 'data-slot': 'coach-primary', className: PRIMARY, disabled: working, onClick: () => void act(), children: n.button }),
      !done && SECONDARY[n.action] && jsx('button', { type: 'button', 'data-slot': 'coach-secondary', className: GHOST, disabled: working, onClick: () => void act(SECONDARY[n.action].action), children: SECONDARY[n.action].label }),
      done && p.board && jsx('button', { type: 'button', className: GHOST, onClick: () => host.navigate?.('/kanban'), children: 'Open board' }),
      jsx('button', { type: 'button', 'data-slot': 'coach-dismiss', className: GHOST, disabled: working, onClick: dismiss, children: done ? 'Close' : 'Dismiss' })
    ]
  })
}

export default {
  id: ID,
  name: 'Coach',
  register(ctx) {
    ctxRef = ctx
    ctx.register({ id: 'panel', area: COMPOSER_AREAS.top, render: () => jsx(Panel, {}) })
    // Pass-through middleware: never alters or blocks the send; only observes it (screenshot rule).
    ctx.register({
      id: 'observe-send',
      area: COMPOSER_AREAS.middleware,
      data: {
        handler: draft => {
          try {
            void onSent(draft)
          } catch (e) {
            log('observe failed', e)
          }
          return draft
        }
      }
    })
    ctx.register({
      id: 'toggle',
      area: PALETTE_AREA,
      data: {
        id: 'coach.toggle',
        label: 'Coach: toggle nudges',
        keywords: ['coach', 'nudge', 'hint', 'restart', 'board'],
        run: () => {
          const next = enabled() ? 'off' : 'on'
          try {
            window.localStorage.setItem(ON_KEY, next)
          } catch {}
          if (next === 'off') setPanel(null)
          host.notify({ kind: 'info', message: `Coach nudges ${next}` })
        }
      }
    })
    watch(ctx)
    window.__coach = { get panel() { return panel }, claim, report, check, onSent, act, dismiss, setPanel, facts }
    ctx.onDispose?.(() => {
      delete window.__coach
    })
  }
}

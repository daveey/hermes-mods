/**
 * Topic Shift — desktop half. After a message is SENT, ask the topic-shift backend
 * (/api/plugins/topic-shift/check) whether it starts a new topic. If so, show a
 * banner above the composer: [New session] [Fork] [Send here].
 *
 *   New session: stop the turn → /undo (removes the message from this session)
 *                → /new → send "<context brief> + @session link + message".
 *   Fork:        stop → /undo → /branch (full history) → send the message there.
 *   Send here:   dismiss (message already sent here).
 *
 * Backend: ~/.hermes/plugins/topic-shift (core.py, dashboard/plugin_api.py).
 */
import { COMPOSER_AREAS, host, PALETTE_AREA, useValue } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useSyncExternalStore } from 'react'

const ID = 'topic-shift'
const CHECK_DELAY_MS = 400
// A message sent while a turn is running (steered in or queued) is checked once that turn ends.
const BUSY_WAIT_MS = 30 * 60 * 1000

// ── tiny store ──────────────────────────────────────────────────────────
let flag = null // { stored, runtime, profile, text, label, reason, sentAt, tools, phase, error }
let seq = 0
const subs = new Set()
let ctxRef = null
let muted = new Set() // stored ids with "don't ask in this session"
let enabled = true
let lastCheck = null

const emit = () => subs.forEach(fn => fn())
const setFlag = next => {
  flag = next
  emit()
}
const patchFlag = patch => flag && setFlag({ ...flag, ...patch })
const getFlag = () => flag
const useFlag = () => useSyncExternalStore(fn => (subs.add(fn), () => subs.delete(fn)), getFlag, getFlag)

const log = (...a) => console.info('[topic-shift]', ...a)
const sleep = ms => new Promise(r => setTimeout(r, ms))

async function waitIdle(runtime, timeoutMs = 10000, everyMs = 120) {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    if (!host.state.busyBySession.get()[runtime]) return true
    await sleep(everyMs)
  }
  return false
}

async function waitFocusChange(from, timeoutMs = 10000) {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    const cur = host.state.focusedStoredSessionId.get()
    if (cur && cur !== from) return cur
    await sleep(120)
  }
  return null
}

async function waitFreshDraft(timeoutMs = 8000) {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    if (!host.state.focusedStoredSessionId.get() && !host.state.activeSessionId.get()) return true
    await sleep(120)
  }
  return false
}

// ── detection (after send) ──────────────────────────────────────────────
function onSent(text) {
  const stored = host.state.focusedStoredSessionId.get()
  const runtime = host.state.focusedSessionId.get()
  if (!enabled || !stored || !text || text.trim().startsWith('/')) return
  if (flag && flag.stored === stored && flag.phase === 'shown') setFlag(null) // a new send supersedes
  if (muted.has(stored)) return
  // Sent mid-turn: it was steered into (or queued behind) the running turn. Check it once
  // that turn ends, so the banner doesn't race the reply. A queue drain re-sends through
  // this middleware and supersedes the wait via `seq`.
  const busy = Boolean(runtime && host.state.busyBySession.get()[runtime])
  const mine = ++seq
  const sentAt = Date.now()
  setTimeout(async () => {
    try {
      if (busy && !(await waitIdle(runtime, BUSY_WAIT_MS, 1000))) return
      if (mine !== seq) return
      const res = await ctxRef.rest('/check', { method: 'POST', body: { session_id: stored, text }, timeoutMs: 30000 })
      log('check', stored, busy ? '(after turn)' : '', res)
      lastCheck = { stored, text, res, busy, at: Date.now() }
      if (mine !== seq || !res?.shift) return
      // Coach nudge bus: one panel at a time, session mutes and weekly demotion (fail-open without coach).
      if (window.__coach && !window.__coach.claim(stored, ID)) return log('coach gate: not now', stored)
      window.__coach?.report(stored, ID, 'shown')
      setFlag({
        stored,
        runtime: runtime || host.state.focusedSessionId.get(),
        profile: host.state.focusedSessionProfile.get() || 'default',
        text,
        label: res.label,
        reason: res.reason,
        sentAt,
        tools: 0,
        steered: busy,
        phase: 'shown'
      })
    } catch (err) {
      log('check failed', err)
      lastCheck = { stored, text, error: String(err?.message || err), at: Date.now() }
    }
  }, CHECK_DELAY_MS)
}

// ── actions ─────────────────────────────────────────────────────────────
async function retireOld(f) {
  // A steered-in message is part of an earlier turn: /undo would remove that whole turn.
  // Leave the old chat as it is and only carry the message over.
  if (f.steered) return
  // 1) stop the turn
  if (f.runtime && host.state.busyBySession.get()[f.runtime]) {
    await host.request('session.interrupt', { session_id: f.runtime }).catch(e => log('interrupt', e))
  }
  if (!(await waitIdle(f.runtime))) throw new Error('turn did not stop')
  // 2) take the message back out of this session (desktop /undo → prefill)
  host.composer.submit(f.stored, '/undo')
  await sleep(900)
  // /undo prefills the backed-up message into this composer; we're moving it, so clear it.
  const draft = await host.composer.getDraft(f.stored)
  if (draft && draft.trim() === f.text.trim()) await host.composer.setDraft(f.stored, '')
}

async function sendWhenReady(target, text, timeoutMs = 8000) {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    if (host.composer.submit(target, text)) return true
    await sleep(200)
  }
  return false
}

async function doNew() {
  const f = flag
  if (!f) return
  patchFlag({ phase: 'working', error: null, step: 'Building context brief…' })
  try {
    // Brief first, while the old transcript still has everything.
    const briefP = ctxRef.rest('/brief', {
      method: 'POST',
      body: { session_id: f.stored, text: f.text, profile: f.profile },
      timeoutMs: 60000
    })
    patchFlag({ step: 'Stopping turn & removing message…' })
    await retireOld(f)
    const b = await briefP
    patchFlag({ step: 'Opening new session…' })
    // Open the fresh chat directly: a '/new' submitted into a session that is still busy (the
    // steered case keeps its turn running) is queued behind that turn instead of running.
    if (typeof host.newChat === 'function') host.newChat(f.profile || null)
    else host.composer.submit(f.stored, '/new')
    await waitFreshDraft()
    await sleep(300)
    const ok = await sendWhenReady('new', b.seeded)
    if (!ok) {
      await host.composer.setDraft('new', b.seeded)
      throw new Error('could not auto-send; message + brief left in the new composer')
    }
    window.__coach?.report(f.stored, ID, 'accepted')
    setFlag(null)
  } catch (err) {
    patchFlag({ phase: 'shown', error: String(err?.message || err) })
  }
}

async function doFork() {
  const f = flag
  if (!f) return
  patchFlag({ phase: 'working', error: null, step: 'Stopping turn & removing message…' })
  try {
    await retireOld(f)
    patchFlag({ step: 'Forking…' })
    host.composer.submit(f.stored, '/branch')
    const child = await waitFocusChange(f.stored)
    if (!child) throw new Error('fork did not open')
    await sleep(300)
    const ok = await sendWhenReady(child, f.text)
    if (!ok) {
      await host.composer.setDraft(child, f.text)
      throw new Error('could not auto-send; message left in the fork composer')
    }
    window.__coach?.report(f.stored, ID, 'accepted')
    setFlag(null)
  } catch (err) {
    patchFlag({ phase: 'shown', error: String(err?.message || err) })
  }
}

function sendHere() {
  if (flag) window.__coach?.report(flag.stored, ID, 'dismissed')
  setFlag(null)
}

function muteSession() {
  if (!flag) return
  muted.add(flag.stored)
  ctxRef?.storage?.set('muted', Array.from(muted).slice(-200))
  window.__coach?.report(flag.stored, ID, 'dismissed')
  setFlag(null)
}

// ── UI ──────────────────────────────────────────────────────────────────
const BTN =
  'inline-flex h-6 items-center rounded-md px-2 text-[0.75rem] font-medium transition-colors disabled:opacity-50 '
const PRIMARY = BTN + 'bg-(--ui-accent) text-(--ui-accent-foreground,white) hover:opacity-90'
const GHOST = BTN + 'border border-(--ui-stroke-secondary) text-(--ui-text-secondary) hover:bg-(--chrome-action-hover) hover:text-foreground'
const LINK = 'text-[0.6875rem] text-(--ui-text-quaternary) hover:text-(--ui-text-secondary) underline-offset-2 hover:underline'

function Banner() {
  const f = useFlag()
  const focused = useValue(host.state.focusedStoredSessionId)
  if (!f || f.stored !== focused) return null
  const working = f.phase === 'working'
  return jsxs('div', {
    'data-slot': 'topic-shift-banner',
    className:
      'flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-(--ui-stroke-secondary) px-2.5 py-1.5 text-[0.75rem]',
    children: [
      jsx('span', { className: 'text-(--ui-accent)', children: '⑂' }),
      jsxs('span', {
        className: 'min-w-0 flex-1 text-(--ui-text-secondary)',
        title: f.reason || '',
        children: [
          'New topic? ',
          jsx('span', { className: 'font-medium text-foreground', children: `“${f.label || 'different task'}”` }),
          f.tools > 0 &&
            jsx('span', { className: 'ml-1.5 text-(--ui-text-tertiary)', children: `· already started: ${f.tools} tool call${f.tools === 1 ? '' : 's'}` }),
          working && jsx('span', { className: 'ml-1.5 text-(--ui-text-tertiary)', children: `· ${f.step || 'Working…'}` }),
          f.error && jsx('span', { className: 'ml-1.5 text-(--ui-danger,#f85149)', children: `· ${f.error}` })
        ]
      }),
      jsx('button', { type: 'button', className: PRIMARY, disabled: working, onClick: () => void doNew(), title: f.steered ? 'Start a fresh session with this message + a context brief (it stays in this chat too)' : 'Move this message to a fresh session with a context brief + @session link', children: 'New session' }),
      !f.steered &&
        jsx('button', { type: 'button', className: GHOST, disabled: working, onClick: () => void doFork(), title: 'Fork this session (full history) and move the message there', children: 'Fork' }),
      jsx('button', { type: 'button', className: GHOST, disabled: working, onClick: sendHere, title: 'Keep it in this session', children: f.steered ? 'Keep here' : 'Send here' }),
      jsx('button', { type: 'button', className: LINK, disabled: working, onClick: muteSession, children: "don't ask in this session" })
    ]
  })
}

export default {
  id: ID,
  name: 'Topic Shift',
  register(ctx) {
    ctxRef = ctx
    try {
      const saved = ctx.storage.get('muted', [])
      if (Array.isArray(saved)) muted = new Set(saved)
      if (ctx.storage.get('enabled', true) === false) enabled = false
    } catch {}

    // Pass-through middleware: never alters or blocks the send; only observes it.
    ctx.register({
      id: 'observe-send',
      area: COMPOSER_AREAS.middleware,
      data: {
        handler: draft => {
          try {
            onSent(draft?.text || '')
          } catch (e) {
            log('observe failed', e)
          }
          return draft
        }
      }
    })

    ctx.register({ id: 'banner', area: COMPOSER_AREAS.top, render: () => jsx(Banner, {}) })

    // Count tool calls the flagged turn has already made.
    ctx.onEvent('tool.start', ev => {
      const sid = ev?.session_id ?? ev?.sid
      if (flag && flag.phase === 'shown' && sid && sid === flag.runtime) patchFlag({ tools: (flag.tools || 0) + 1 })
    })

    ctx.register({
      id: 'toggle',
      area: PALETTE_AREA,
      data: {
        id: 'topic-shift.toggle',
        label: 'Topic Shift: toggle detection',
        keywords: ['topic', 'fork', 'new session', 'shift'],
        run: () => {
          enabled = !enabled
          ctx.storage.set('enabled', enabled)
          host.notify({ kind: 'info', message: `Topic Shift detection ${enabled ? 'on' : 'off'}` })
        }
      }
    })

    // Test/debug door: window.__topicShift.{flag, onSent, doNew, doFork}
    window.__topicShift = { get flag() { return flag }, get lastCheck() { return lastCheck }, setFlag, onSent, doNew, doFork, sendHere }
    ctx.onDispose?.(() => {
      delete window.__topicShift
    })
  }
}

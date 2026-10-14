/**
 * Prompt Coach — desktop half. After a message is SENT, ask the prompt-coach backend
 * (/api/plugins/prompt-coach/check) whether a more ambitious framing (Altitude / Depth / Range)
 * would get a materially better result. If so, show a panel above the composer with the
 * critique and an editable rewrite:
 *
 *   Run in new session (primary): /new → send the rewrite (+ @session pointer back). The
 *                                 original turn keeps running, so you can compare.
 *   Replace:                      stop the turn → /undo → send the rewrite in this session.
 *   Keep mine:                    dismiss.   "don't coach this session": mute it.
 *
 * Every shown suggestion and the choice made are logged backend-side (log.jsonl) for the
 * weekly digest. Backend: ~/.hermes/plugins/prompt-coach (core.py, dashboard/plugin_api.py).
 */
import { COMPOSER_AREAS, host, PALETTE_AREA, useValue } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useSyncExternalStore } from 'react'

const ID = 'prompt-coach'
const CHECK_DELAY_MS = 300
const LENS = {
  altitude: { icon: '↑', name: 'Altitude', hint: 'ask for the outcome, not the step' },
  depth: { icon: '↓', name: 'Depth', hint: 'do it the way a top expert would' },
  range: { icon: '↔', name: 'Range', hint: 'bring in other fields\u2019 methods' }
}

// ── tiny store ──────────────────────────────────────────────────────────
let flag = null // { id, stored, runtime, profile, text, lens, critique, rewrite, draft, open, phase, step, error }
let seq = 0
const subs = new Set()
let ctxRef = null
let muted = new Set()
let enabled = true
let lastCheck = null

const emit = () => subs.forEach(fn => fn())
const setFlag = next => {
  flag = next
  emit()
}
const patchFlag = patch => flag && setFlag({ ...flag, ...patch })
const useFlag = () => useSyncExternalStore(fn => (subs.add(fn), () => subs.delete(fn)), () => flag, () => flag)

const log = (...a) => console.info('[prompt-coach]', ...a)
const sleep = ms => new Promise(r => setTimeout(r, ms))

async function waitFor(fn, timeoutMs = 8000, step = 120) {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    const v = fn()
    if (v) return v
    await sleep(step)
  }
  return null
}

const isBusy = runtime => !!(runtime && host.state.busyBySession.get()[runtime])
// Coach nudge bus outcome for each choice (superseded by a new send = expired, which mutes nothing).
const BUS = { new_session: 'accepted', replace: 'accepted', keep: 'dismissed', mute: 'dismissed', dismissed: 'expired' }
const choice = (f, c) => {
  if (f.stored) window.__coach?.report(f.stored, ID, BUS[c] || 'expired')
  return ctxRef?.rest('/choice', { method: 'POST', body: { id: f.id, choice: c, edited: f.draft.trim() !== f.rewrite.trim() } }).catch(e => log('choice', e))
}

// ── detection (after send) ──────────────────────────────────────────────
function onSent(text) {
  if (!enabled || !text || text.trim().startsWith('/')) return
  const stored0 = host.state.focusedStoredSessionId.get()
  const runtime0 = host.state.focusedSessionId.get()
  if (stored0 && muted.has(stored0)) return
  if (isBusy(runtime0)) return // queued behind a running turn → almost always a follow-up
  if (flag && flag.phase === 'shown') {
    choice(flag, 'dismissed')
    setFlag(null) // a new send supersedes
  }
  const mine = ++seq
  setTimeout(async () => {
    try {
      // A first message in a new chat has no stored id yet; wait for the session to exist.
      const stored = stored0 || (await waitFor(() => host.state.focusedStoredSessionId.get(), 6000)) || ''
      const res = await ctxRef.rest('/check', { method: 'POST', body: { session_id: stored, text }, timeoutMs: 90000 })
      log('check', stored, res)
      lastCheck = { stored, text, res, at: Date.now() }
      if (mine !== seq || !res?.coach) return
      // Coach nudge bus: one panel at a time, session mutes and weekly demotion (fail-open without coach).
      if (stored && window.__coach && !window.__coach.claim(stored, ID)) return log('coach gate: not now', stored)
      if (stored) window.__coach?.report(stored, ID, 'shown')
      setFlag({
        id: res.id,
        stored,
        runtime: runtime0 || host.state.focusedSessionId.get(),
        profile: host.state.focusedSessionProfile.get() || 'default',
        text,
        lens: res.lens,
        critique: res.critique,
        rewrite: res.rewrite,
        draft: res.rewrite,
        open: true,
        phase: 'shown'
      })
    } catch (err) {
      log('check failed', err)
      lastCheck = { text, error: String(err?.message || err), at: Date.now() }
    }
  }, CHECK_DELAY_MS)
}

// ── actions ─────────────────────────────────────────────────────────────
async function sendWhenReady(target, text, timeoutMs = 8000, step = 200) {
  return !!(await waitFor(() => host.composer.submit(target, text), timeoutMs, step))
}

// Same text as core.seeded_message (kept in sync; core.skip_reason keys off the marker line).
function seededMessage(f) {
  const ref = f.stored ? `@session:${f.profile || 'default'}/${f.stored}` : 'another session'
  const orig = (f.text || '').replace(/<memory-context>[\s\S]*?(<\/memory-context>|$)/g, '').replace(/\s+/g, ' ').trim().slice(0, 600)
  return `${f.draft.trim()}\n\n---\n[prompt-coach] Rewritten from my request in ${ref}, which is still running separately. My original wording: "${orig}"`
}

const onFreshDraft = () => !host.state.focusedStoredSessionId.get() && !host.state.activeSessionId.get()

async function doNewSession() {
  const f = flag
  if (!f || !f.draft.trim()) return
  // Close the panel immediately; the launch below needs no further input.
  setFlag(null)
  choice(f, 'new_session')
  const seeded = seededMessage(f)
  try {
    // Open the fresh chat directly. Submitting '/new' into the original composer does not
    // work while its turn runs: an external slash-command submit to a busy session is
    // QUEUED behind the turn, so the new session only opened once the turn finished.
    if (typeof host.newChat === 'function') host.newChat(f.profile || null)
    else host.composer.submit(f.stored || 'new', '/new')
    if (!(await waitFor(onFreshDraft, 4000, 30))) throw new Error('new chat did not open')
    const ok = await sendWhenReady('new', seeded, 5000, 50)
    if (!ok) {
      await host.composer.setDraft('new', seeded)
      throw new Error('could not auto-send; the rewrite is in the new composer')
    }
  } catch (err) {
    log('new session', err)
    host.notify({ kind: 'error', message: `Prompt Coach: ${String(err?.message || err)}` })
  }
}

async function doReplace() {
  const f = flag
  if (!f || !f.draft.trim() || !f.stored) return
  patchFlag({ phase: 'working', error: null, step: 'Stopping turn & removing message…' })
  try {
    if (isBusy(f.runtime)) await host.request('session.interrupt', { session_id: f.runtime }).catch(e => log('interrupt', e))
    if (!(await waitFor(() => !isBusy(f.runtime), 10000))) throw new Error('turn did not stop')
    host.composer.submit(f.stored, '/undo')
    await sleep(900)
    patchFlag({ step: 'Sending rewrite…' })
    await host.composer.setDraft(f.stored, '') // /undo prefilled the original; we're replacing it
    const ok = await sendWhenReady(f.stored, f.draft)
    if (!ok) {
      await host.composer.setDraft(f.stored, f.draft)
      throw new Error('could not auto-send; the rewrite is in the composer')
    }
    choice(f, 'replace')
    setFlag(null)
  } catch (err) {
    patchFlag({ phase: 'shown', error: String(err?.message || err) })
  }
}

function keepMine() {
  if (flag) choice(flag, 'keep')
  setFlag(null)
}

function muteSession() {
  if (!flag) return
  choice(flag, 'mute')
  if (flag.stored) {
    muted.add(flag.stored)
    ctxRef?.storage?.set('muted', Array.from(muted).slice(-200))
  }
  setFlag(null)
}

// ── UI ──────────────────────────────────────────────────────────────────
const BTN =
  'inline-flex h-6 items-center rounded-md px-2 text-[0.75rem] font-medium transition-colors disabled:opacity-50 '
const PRIMARY = BTN + 'bg-(--ui-accent) text-(--ui-accent-foreground,white) hover:opacity-90'
const GHOST = BTN + 'border border-(--ui-stroke-secondary) text-(--ui-text-secondary) hover:bg-(--chrome-action-hover) hover:text-foreground'
const LINK = 'text-[0.6875rem] text-(--ui-text-quaternary) hover:text-(--ui-text-secondary) underline-offset-2 hover:underline'

function Panel() {
  const f = useFlag()
  const focused = useValue(host.state.focusedStoredSessionId)
  if (!f || (f.stored && f.stored !== focused)) return null
  const working = f.phase === 'working'
  const lens = LENS[f.lens] || LENS.altitude
  return jsxs('div', {
    'data-slot': 'prompt-coach-panel',
    className: 'flex flex-col gap-1.5 rounded-md border border-(--ui-stroke-secondary) px-2.5 py-1.5 text-[0.75rem]',
    children: [
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-x-2 gap-y-1',
        children: [
          jsx('span', { className: 'text-(--ui-accent)', title: lens.hint, children: lens.icon }),
          jsxs('span', {
            className: 'min-w-0 flex-1 text-(--ui-text-secondary)',
            children: [
              jsx('span', { className: 'font-medium text-foreground', title: lens.hint, children: `${lens.name}: ` }),
              f.critique || 'a more ambitious version of this request',
              working && jsx('span', { className: 'ml-1.5 text-(--ui-text-tertiary)', children: `· ${f.step || 'Working…'}` }),
              f.error && jsx('span', { className: 'ml-1.5 text-(--ui-danger,#f85149)', children: `· ${f.error}` })
            ]
          }),
          jsx('button', { type: 'button', className: LINK, onClick: () => patchFlag({ open: !f.open }), children: f.open ? 'hide rewrite' : 'show rewrite' })
        ]
      }),
      f.open &&
        jsx('textarea', {
          'data-slot': 'prompt-coach-rewrite',
          className:
            'w-full resize-y rounded border border-(--ui-stroke-secondary) bg-transparent px-2 py-1 text-[0.75rem] leading-snug text-foreground outline-none focus:border-(--ui-accent)',
          rows: Math.min(8, Math.max(3, Math.ceil(f.draft.length / 110))),
          value: f.draft,
          disabled: working,
          onChange: e => patchFlag({ draft: e.target.value }),
          onKeyDown: e => e.stopPropagation()
        }),
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-2',
        children: [
          jsx('button', { type: 'button', className: PRIMARY, disabled: working, onClick: () => void doNewSession(), title: 'Start a fresh session with this rewrite; your original keeps running here', children: 'Run in new session' }),
          f.stored && jsx('button', { type: 'button', className: GHOST, disabled: working, onClick: () => void doReplace(), title: 'Stop the current turn and send the rewrite here instead', children: 'Replace' }),
          jsx('button', { type: 'button', className: GHOST, disabled: working, onClick: keepMine, children: 'Keep mine' }),
          jsx('button', { type: 'button', className: LINK, disabled: working, onClick: muteSession, children: "don't coach this session" })
        ]
      })
    ]
  })
}

export default {
  id: ID,
  name: 'Prompt Coach',
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

    ctx.register({ id: 'panel', area: COMPOSER_AREAS.top, render: () => jsx(Panel, {}) })

    ctx.register({
      id: 'toggle',
      area: PALETTE_AREA,
      data: {
        id: 'prompt-coach.toggle',
        label: 'Prompt Coach: toggle',
        keywords: ['prompt', 'coach', 'ambitious', 'rewrite'],
        run: () => {
          enabled = !enabled
          ctx.storage.set('enabled', enabled)
          host.notify({ kind: 'info', message: `Prompt Coach ${enabled ? 'on' : 'off'}` })
        }
      }
    })

    // Test/debug door
    window.__promptCoach = { get flag() { return flag }, get lastCheck() { return lastCheck }, setFlag, onSent, doNewSession, doReplace, keepMine }
    ctx.onDispose?.(() => {
      delete window.__promptCoach
    })
  }
}

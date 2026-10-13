/**
 * Spin Out — ⇧⌘F on a session: find the follow-up work at the END of the session and start one
 * new session per task. Each child gets a self-contained brief (written by the spin-out backend
 * from the parent transcript) plus an @session pointer back to the parent, and starts working
 * right away in the background. A banner above the composer lists the children (click to open).
 *
 * Backend: plugins/spin-out (core.py, dashboard/plugin_api.py → POST /api/plugins/spin-out/plan).
 *
 * ⇧⌘F is the shipped default for "focus session search". On first load this plugin moves that
 * action to ⌥⌘F (only if you never rebound it yourself); the move applies after a window reload.
 */
import { COMPOSER_AREAS, host, KEYBINDS_AREA, PALETTE_AREA, useValue } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useSyncExternalStore } from 'react'

const ID = 'spin-out'
const COMBO = 'mod+shift+f'
const SEARCH_ACTION = 'session.focusSearch'
const SEARCH_NEW_COMBO = 'mod+alt+f'
const KEYBINDS_KEY = 'hermes.desktop.keybinds'

let ctxRef = null
let job = null // { parent, phase: 'planning'|'spawning'|'done'|'error', step, children: [{title, stored, error}], skipped, error }
const subs = new Set()
const emit = () => subs.forEach(fn => fn())
const setJob = next => {
  job = next
  emit()
}
const patchJob = patch => job && setJob({ ...job, ...patch })
const useJob = () => useSyncExternalStore(fn => (subs.add(fn), () => subs.delete(fn)), () => job)
const log = (...a) => console.info('[spin-out]', ...a)

// ── free ⇧⌘F from the built-in "focus session search" ───────────────────
function freeChord() {
  try {
    const raw = window.localStorage.getItem(KEYBINDS_KEY)
    const overrides = raw ? JSON.parse(raw) : {}
    if (Object.hasOwn(overrides, SEARCH_ACTION)) return false // user already chose; respect it
    overrides[SEARCH_ACTION] = [SEARCH_NEW_COMBO]
    window.localStorage.setItem(KEYBINDS_KEY, JSON.stringify(overrides))
    return true
  } catch (e) {
    log('freeChord failed', e)
    return false
  }
}

// ── spin out ────────────────────────────────────────────────────────────
async function spinOut() {
  if (job && (job.phase === 'planning' || job.phase === 'spawning')) return
  const parent = host.state.focusedStoredSessionId.get()
  if (!parent) {
    host.notify({ kind: 'info', message: 'Spin out: open a session first' })
    return
  }
  const profile = host.state.focusedSessionProfile.get() || 'default'
  setJob({ parent, phase: 'planning', step: 'Reading the session and planning follow-ups…', children: [], skipped: [] })
  let plan
  try {
    plan = await ctxRef.rest('/plan', { method: 'POST', body: { session_id: parent, profile }, timeoutMs: 180000 })
  } catch (err) {
    patchJob({ phase: 'error', error: `planning failed: ${err?.message || err}` })
    return
  }
  log('plan', plan)
  const tasks = Array.isArray(plan?.tasks) ? plan.tasks : []
  if (!tasks.length) {
    patchJob({ phase: 'error', error: plan?.error || 'no follow-up work found at the end of this session', skipped: plan?.skipped || [] })
    return
  }
  patchJob({ phase: 'spawning', skipped: plan.skipped || [], children: tasks.map(t => ({ title: t.title })) })
  const children = []
  for (const [i, t] of tasks.entries()) {
    patchJob({ step: `Starting ${i + 1}/${tasks.length}: ${t.title}` })
    const child = { title: t.title }
    try {
      const created = await host.request(
        'session.create',
        { profile: plan.profile || profile, ...(plan.cwd ? { cwd: plan.cwd } : {}), title: t.title },
        60000
      )
      const sid = created?.session_id
      if (!sid) throw new Error('session.create returned no id')
      child.stored = created?.stored_session_id || null
      const sub = await host.request('prompt.submit', { session_id: sid, text: t.message, profile: plan.profile || profile }, 60000)
      if (!['streaming', 'queued'].includes(sub?.status)) child.error = `submit status ${sub?.status || 'unknown'}`
    } catch (err) {
      child.error = String(err?.message || err)
    }
    children.push(child)
    patchJob({ children: [...children, ...tasks.slice(i + 1).map(x => ({ title: x.title }))] })
  }
  const failed = children.filter(c => c.error).length
  patchJob({ phase: 'done', step: null, children })
  host.notify({
    kind: failed ? 'warning' : 'success',
    message: `Spun out ${children.length - failed} session${children.length - failed === 1 ? '' : 's'}${failed ? `, ${failed} failed` : ''}`
  })
}

// ── UI ──────────────────────────────────────────────────────────────────
const BTN = 'inline-flex h-6 items-center rounded-md px-2 text-[0.75rem] font-medium transition-colors disabled:opacity-50 '
const GHOST = BTN + 'border border-(--ui-stroke-secondary) text-(--ui-text-secondary) hover:bg-(--chrome-action-hover) hover:text-foreground'
const CHIP =
  'inline-flex max-w-[22rem] items-center gap-1 truncate rounded-md border border-(--ui-stroke-secondary) px-1.5 py-0.5 text-[0.75rem] '

function Banner() {
  const j = useJob()
  const focused = useValue(host.state.focusedStoredSessionId)
  if (!j || j.parent !== focused) return null
  const busy = j.phase === 'planning' || j.phase === 'spawning'
  return jsxs('div', {
    'data-slot': 'spin-out-banner',
    className: 'flex flex-col gap-1.5 rounded-md border border-(--ui-stroke-secondary) px-2.5 py-1.5 text-[0.75rem]',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx('span', { className: 'text-(--ui-accent)', children: '⑃' }),
          jsxs('span', {
            className: 'min-w-0 flex-1 text-(--ui-text-secondary)',
            children: [
              jsx('span', { className: 'font-medium text-foreground', children: 'Spin out' }),
              j.step && jsx('span', { className: 'ml-1.5', children: `· ${j.step}` }),
              j.phase === 'done' && jsx('span', { className: 'ml-1.5', children: `· ${j.children.filter(c => !c.error).length} child session(s) started` }),
              j.error && jsx('span', { className: 'ml-1.5 text-(--ui-danger,#f85149)', children: `· ${j.error}` })
            ]
          }),
          !busy && jsx('button', { type: 'button', className: GHOST, onClick: () => setJob(null), children: 'Dismiss' })
        ]
      }),
      j.children.length > 0 &&
        jsx('div', {
          className: 'flex flex-wrap gap-1.5',
          children: j.children.map((c, i) =>
            jsx(
              'button',
              {
                type: 'button',
                disabled: !c.stored,
                title: c.error || (c.stored ? 'Open child session' : 'pending'),
                className:
                  CHIP +
                  (c.error
                    ? 'text-(--ui-danger,#f85149)'
                    : c.stored
                      ? 'text-foreground hover:bg-(--chrome-action-hover)'
                      : 'text-(--ui-text-tertiary)'),
                onClick: () => c.stored && void host.openSession?.(c.stored),
                children: `${c.error ? '✕' : c.stored ? '↳' : '…'} ${c.title}`
              },
              i
            )
          )
        }),
      j.skipped?.length > 0 &&
        jsx('div', {
          className: 'text-(--ui-text-tertiary)',
          children: 'Left for you: ' + j.skipped.map(s => `${s.item}${s.reason ? ` (${s.reason})` : ''}`).join('; ')
        })
    ]
  })
}

export default {
  id: ID,
  name: 'Spin Out',
  register(ctx) {
    ctxRef = ctx
    if (freeChord()) {
      host.notify({ kind: 'info', message: 'Spin Out: ⇧⌘F is now "spin out follow-ups"; session search moved to ⌥⌘F. Reload the window (⇧⌘R) to apply.' })
    }

    ctx.register({
      id: 'run-key',
      area: KEYBINDS_AREA,
      data: { id: 'spinout.run', category: 'session', defaults: [COMBO], label: 'Spin out follow-up work into new sessions', run: () => void spinOut() }
    })
    ctx.register({
      id: 'run-palette',
      area: PALETTE_AREA,
      data: {
        id: 'spinout.run',
        label: 'Spin out follow-up work into new sessions',
        keywords: ['spin', 'fork', 'follow-up', 'followup', 'next steps', 'split', 'children'],
        run: () => void spinOut()
      }
    })
    ctx.register({ id: 'banner', area: COMPOSER_AREAS.top, render: () => jsx(Banner, {}) })

    window.__spinOut = { get job() { return job }, spinOut, setJob }
    ctx.onDispose?.(() => {
      delete window.__spinOut
    })
  }
}

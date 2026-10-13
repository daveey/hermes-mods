/**
 * Spin Out — ⇧⌘F on a session: find the follow-up work at the END of the session and offer it as
 * choices in a banner above the composer. Click a task to launch it as a new session, or [All] to
 * launch every remaining one. Each child gets a self-contained brief (written by the spin-out
 * backend from the parent transcript) plus an @session pointer back to the parent, and starts
 * working in the background. Launched rows turn into links to the child session.
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
const useJob = () => useSyncExternalStore(fn => (subs.add(fn), () => subs.delete(fn)), () => job, () => job)
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
// Phase flow: planning → choosing (pick tasks; click one or [All]) → done when every task is
// launched or dismissed. Child state: {title, message, prompt, status: 'idle'|'launching'|'ok'|'error', stored, error}
async function spinOut() {
  if (job && job.phase === 'planning') return
  const parent = host.state.focusedStoredSessionId.get()
  if (!parent) {
    host.notify({ kind: 'info', message: 'Spin out: open a session first' })
    return
  }
  const profile = host.state.focusedSessionProfile.get() || 'default'
  setJob({ parent, profile, phase: 'planning', step: 'Reading the session and planning follow-ups…', children: [], skipped: [] })
  let plan
  try {
    plan = await ctxRef.rest('/plan', { method: 'POST', body: { session_id: parent, profile }, timeoutMs: 180000 })
  } catch (err) {
    patchJob({ phase: 'error', step: null, error: `planning failed: ${err?.message || err}` })
    return
  }
  log('plan', plan)
  const tasks = Array.isArray(plan?.tasks) ? plan.tasks : []
  if (!tasks.length) {
    patchJob({ phase: 'error', step: null, error: plan?.error || 'no follow-up work found at the end of this session', skipped: plan?.skipped || [] })
    return
  }
  patchJob({
    phase: 'choosing',
    step: null,
    plan: { profile: plan.profile || profile, cwd: plan.cwd || null },
    skipped: plan.skipped || [],
    children: tasks.map(t => ({ title: t.title, message: t.message, prompt: t.prompt, status: 'idle' }))
  })
}

const patchChild = (i, patch) => {
  if (!job) return
  const children = job.children.map((c, k) => (k === i ? { ...c, ...patch } : c))
  const allLaunched = children.every(c => c.status === 'ok' || c.status === 'error')
  setJob({ ...job, children, phase: allLaunched ? 'done' : job.phase })
}

async function launchOne(i) {
  const j = job
  const c = j?.children?.[i]
  if (!c || c.status !== 'idle') return false
  const profile = j.plan?.profile || j.profile || 'default'
  patchChild(i, { status: 'launching' })
  try {
    const created = await host.request('session.create', { profile, ...(j.plan?.cwd ? { cwd: j.plan.cwd } : {}), title: c.title }, 60000)
    const sid = created?.session_id
    if (!sid) throw new Error('session.create returned no id')
    const stored = created?.stored_session_id || null
    const sub = await host.request('prompt.submit', { session_id: sid, text: c.message, profile }, 60000)
    if (!['streaming', 'queued'].includes(sub?.status)) {
      patchChild(i, { status: 'error', stored, error: `submit status ${sub?.status || 'unknown'}` })
      return false
    }
    patchChild(i, { status: 'ok', stored })
    return true
  } catch (err) {
    patchChild(i, { status: 'error', error: String(err?.message || err) })
    return false
  }
}

async function launchAll() {
  if (!job) return
  const idle = job.children.map((c, i) => (c.status === 'idle' ? i : -1)).filter(i => i >= 0)
  let ok = 0
  for (const i of idle) if (await launchOne(i)) ok++
  const failed = idle.length - ok
  host.notify({
    kind: failed ? 'warning' : 'success',
    message: `Spun out ${ok} session${ok === 1 ? '' : 's'}${failed ? `, ${failed} failed` : ''}`
  })
}

async function launchSingle(i) {
  const ok = await launchOne(i)
  if (ok) host.notify({ kind: 'success', message: `Spun out: ${job?.children?.[i]?.title || 'session'}` })
}

// ── UI ──────────────────────────────────────────────────────────────────
const BTN = 'inline-flex h-6 items-center rounded-md px-2 text-[0.75rem] font-medium transition-colors disabled:opacity-50 '
const GHOST = BTN + 'border border-(--ui-stroke-secondary) text-(--ui-text-secondary) hover:bg-(--chrome-action-hover) hover:text-foreground'
const PRIMARY = BTN + 'border border-(--ui-accent) text-(--ui-accent) hover:bg-(--chrome-action-hover)'
const ROW =
  'flex w-full items-center gap-2 rounded-md border border-(--ui-stroke-secondary) px-2 py-1 text-left text-[0.75rem] transition-colors disabled:cursor-default '

function Banner() {
  const j = useJob()
  const focused = useValue(host.state.focusedStoredSessionId)
  if (!j || j.parent !== focused) return null
  const kids = j.children || []
  const launching = kids.some(c => c.status === 'launching')
  const idle = kids.filter(c => c.status === 'idle').length
  const started = kids.filter(c => c.status === 'ok').length
  const busy = j.phase === 'planning' || launching
  const summary =
    j.phase === 'choosing'
      ? started
        ? `${started} started · ${idle} more to pick`
        : `${kids.length} follow-up${kids.length === 1 ? '' : 's'} found — click to launch`
      : j.phase === 'done'
        ? `${started} child session(s) started`
        : null
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
              summary && jsx('span', { className: 'ml-1.5', children: `· ${summary}` }),
              j.error && jsx('span', { className: 'ml-1.5 text-(--ui-danger,#f85149)', children: `· ${j.error}` })
            ]
          }),
          j.phase === 'choosing' &&
            idle > 1 &&
            jsx('button', { type: 'button', 'data-slot': 'spin-out-all', className: PRIMARY, disabled: launching, onClick: () => void launchAll(), children: `All (${idle})` }),
          !busy && jsx('button', { type: 'button', className: GHOST, onClick: () => setJob(null), children: 'Dismiss' })
        ]
      }),
      kids.length > 0 &&
        jsx('div', {
          className: 'flex flex-col gap-1',
          children: kids.map((c, i) => {
            const icon = c.status === 'error' ? '✕' : c.status === 'ok' ? '↳' : c.status === 'launching' ? '…' : '▸'
            const clickable = c.status === 'idle' ? !launching : c.status === 'ok' && !!c.stored
            return jsxs(
              'button',
              {
                type: 'button',
                'data-slot': 'spin-out-choice',
                disabled: !clickable,
                title:
                  c.status === 'idle'
                    ? `Launch: ${c.prompt || c.title}`
                    : c.status === 'ok'
                      ? 'Open child session'
                      : c.status === 'error'
                        ? c.error
                        : 'starting…',
                className:
                  ROW +
                  (c.status === 'error'
                    ? 'text-(--ui-danger,#f85149)'
                    : c.status === 'ok'
                      ? 'text-foreground hover:bg-(--chrome-action-hover)'
                      : c.status === 'launching'
                        ? 'text-(--ui-text-tertiary)'
                        : 'text-foreground hover:border-(--ui-accent) hover:bg-(--chrome-action-hover)'),
                onClick: () => {
                  if (c.status === 'idle') void launchSingle(i)
                  else if (c.status === 'ok' && c.stored) void host.openSession?.(c.stored)
                },
                children: [
                  jsx('span', { className: 'shrink-0 w-3 text-center', children: icon }),
                  jsx('span', { className: 'min-w-0 flex-1 truncate', children: c.title }),
                  jsx('span', {
                    className: 'shrink-0 text-(--ui-text-tertiary)',
                    children: c.status === 'idle' ? 'launch' : c.status === 'ok' ? 'open' : c.status === 'launching' ? 'starting' : 'failed'
                  })
                ]
              },
              i
            )
          })
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

    window.__spinOut = { get job() { return job }, spinOut, setJob, launchOne, launchAll }
    ctx.onDispose?.(() => {
      delete window.__spinOut
    })
  }
}

/**
 * Spin Out — find the follow-up work at the END of a session and offer it as choices in a banner
 * above the composer. Click a task to launch it as a new session, [All] to launch every remaining one,
 * or [Kanban] to put the remaining ones on the active Kanban board as cards. Each child gets a
 * self-contained brief (written by the spin-out backend from the parent transcript) plus an @session
 * pointer back to the parent. Launched rows link to the child session; carded rows open the board.
 *
 * Triggers: ⇧⌘F (always plans), and automatically whenever a turn finishes in the focused session and its
 * last reply mentions follow-up work (cheap server-side cue filter first, so most turns cost no model call;
 * an unchanged reply reuses the cached plan). Palette "Spin out: toggle automatic follow-up panel" turns
 * the automatic check off (localStorage `spinout.auto`). A dismissed panel does not come back for the
 * same reply.
 *
 * Backend: plugins/spin-out (core.py, dashboard/plugin_api.py → POST /api/plugins/spin-out/plan, /kanban).
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

const AUTO_KEY = 'spinout.auto'
const AUTO_DELAY_MS = 1500 // let the finished turn land in state.db first

let ctxRef = null
let apiVersion = 0 // backend capability (GET /ping → api). 2 = auto check + /kanban; older backends get neither.
const subsApi = new Set()
async function probeApi() {
  try {
    const r = await ctxRef.rest('/ping', { timeoutMs: 10000 })
    apiVersion = Number(r?.api) || 1
  } catch {
    apiVersion = 0
  }
  subsApi.forEach(fn => fn())
  return apiVersion
}
const useApi = () => useSyncExternalStore(fn => (subsApi.add(fn), () => subsApi.delete(fn)), () => apiVersion, () => apiVersion)
const dismissed = new Map() // parent session -> reply_index the user dismissed (auto never re-shows it)
const autoOn = () => {
  try {
    return window.localStorage.getItem(AUTO_KEY) !== 'off'
  } catch {
    return true
  }
}
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
  void probeApi() // a restarted backend may have gained /kanban
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
  showPlan(parent, profile, plan, false)
}

// Fill the banner from a plan. Rows: {title, message, prompt, status}.
function showPlan(parent, profile, plan, auto) {
  setJob({
    parent,
    profile,
    auto,
    replyIndex: plan.reply_index ?? null,
    phase: 'choosing',
    step: null,
    plan: { profile: plan.profile || profile, cwd: plan.cwd || null },
    skipped: plan.skipped || [],
    children: plan.tasks.map(t => ({ title: t.title, message: t.message, prompt: t.prompt, status: 'idle' }))
  })
}

// Automatic check after a finished turn: silent on errors and on "nothing to do".
async function autoCheck(parent, profile) {
  if (!autoOn() || !parent || !ctxRef) return
  if (apiVersion < 2 && (await probeApi()) < 2) return // old backend would run the full planner every turn
  if (job && job.parent !== parent && (job.phase === 'planning' || job.children?.some(c => c.status === 'launching'))) return
  if (job && job.parent === parent && job.phase !== 'done' && job.phase !== 'error' && !job.auto) return // manual run in progress
  let plan
  try {
    plan = await ctxRef.rest('/plan', { method: 'POST', body: { session_id: parent, profile, auto: true }, timeoutMs: 180000 })
  } catch (err) {
    log('auto check failed', err)
    return
  }
  if (host.state.focusedStoredSessionId.get() !== parent) return // user moved on; it will re-check next turn
  const tasks = Array.isArray(plan?.tasks) ? plan.tasks : []
  if (!tasks.length) {
    if (job?.parent === parent && job.auto && !job.children.some(c => c.status !== 'idle')) setJob(null)
    return
  }
  if (dismissed.get(parent) === plan.reply_index) return
  if (job?.parent === parent && job.replyIndex === plan.reply_index) return // already showing this plan
  log('auto plan', plan)
  showPlan(parent, profile, plan, true)
}

// A turn "finished" = the focused session's runtime flips busy → idle in busyBySession. Keyed by runtime id, so
// switching sessions mid-turn never reads as a finished turn (the focused-only `busy` atom would flip on focus).
function watchTurns(ctx) {
  let prevBusy = {}
  let timer = null
  const onChange = () => {
    const map = host.state.busyBySession.get() || {}
    const runtime = host.state.focusedSessionId.get()
    const finished = runtime && prevBusy[runtime] && !map[runtime]
    prevBusy = { ...map }
    if (!finished) return
    const sid = host.state.focusedStoredSessionId.get()
    const profile = host.state.focusedSessionProfile.get() || 'default'
    clearTimeout(timer)
    timer = setTimeout(() => void autoCheck(sid, profile), AUTO_DELAY_MS)
  }
  const unsub = host.state.busyBySession?.subscribe(onChange)
  if (unsub) ctx.onDispose?.(unsub)
  ctx.onDispose?.(() => clearTimeout(timer))
}

function dismiss() {
  if (job && job.replyIndex != null) dismissed.set(job.parent, job.replyIndex)
  setJob(null)
}

// Put every idle follow-up on the active Kanban board (one card each, assigned to the session's profile).
async function toKanban() {
  const j = job
  if (!j) return
  const idle = j.children.map((c, i) => (c.status === 'idle' ? i : -1)).filter(i => i >= 0)
  if (!idle.length) return
  idle.forEach(i => patchChild(i, { status: 'launching' }))
  let res
  try {
    res = await ctxRef.rest('/kanban', {
      method: 'POST',
      body: {
        session_id: j.parent,
        profile: j.plan?.profile || j.profile || 'default',
        cwd: j.plan?.cwd || '',
        tasks: idle.map(i => ({ title: j.children[i].title, message: j.children[i].message }))
      },
      timeoutMs: 60000
    })
  } catch (err) {
    idle.forEach(i => patchChild(i, { status: 'idle' }))
    host.notify({ kind: 'warning', message: `Spin out → Kanban failed: ${err?.message || err}` })
    return
  }
  const cards = Array.isArray(res?.cards) ? res.cards : []
  let ok = 0
  idle.forEach((i, k) => {
    const card = cards[k]
    if (card?.task_id) {
      ok++
      patchChild(i, { status: 'carded', taskId: card.task_id, board: res.board })
    } else patchChild(i, { status: 'error', error: card?.error || res?.error || 'card not created' })
  })
  host.notify({
    kind: ok === idle.length ? 'success' : 'warning',
    message: `Put ${ok} follow-up${ok === 1 ? '' : 's'} on the ${res?.board || 'Kanban'} board${ok < idle.length ? `, ${idle.length - ok} failed` : ''}`
  })
}

const patchChild = (i, patch) => {
  if (!job) return
  const children = job.children.map((c, k) => (k === i ? { ...c, ...patch } : c))
  const allLaunched = children.every(c => c.status === 'ok' || c.status === 'error' || c.status === 'carded')
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
  const api = useApi()
  if (!j || j.parent !== focused) return null
  const kids = j.children || []
  const launching = kids.some(c => c.status === 'launching')
  const idle = kids.filter(c => c.status === 'idle').length
  const started = kids.filter(c => c.status === 'ok').length
  const carded = kids.filter(c => c.status === 'carded').length
  const moved = [started && `${started} started`, carded && `${carded} on the board`].filter(Boolean).join(' · ')
  const busy = j.phase === 'planning' || launching
  const summary =
    j.phase === 'choosing'
      ? moved
        ? `${moved} · ${idle} more to pick`
        : `${kids.length} follow-up${kids.length === 1 ? '' : 's'} ${j.auto ? 'left at the end of this turn' : 'found'} — click to launch`
      : j.phase === 'done'
        ? moved
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
          j.phase === 'choosing' &&
            idle > 0 &&
            api >= 2 &&
            jsx('button', {
              type: 'button',
              'data-slot': 'spin-out-kanban',
              title: 'Put the remaining follow-ups on the active Kanban board as cards',
              className: PRIMARY,
              disabled: launching,
              onClick: () => void toKanban(),
              children: `Kanban (${idle})`
            }),
          !busy && jsx('button', { type: 'button', className: GHOST, onClick: () => dismiss(), children: 'Dismiss' })
        ]
      }),
      kids.length > 0 &&
        jsx('div', {
          className: 'flex flex-col gap-1',
          children: kids.map((c, i) => {
            const icon = c.status === 'error' ? '✕' : c.status === 'ok' ? '↳' : c.status === 'carded' ? '▤' : c.status === 'launching' ? '…' : '▸'
            const clickable = c.status === 'idle' ? !launching : (c.status === 'ok' && !!c.stored) || c.status === 'carded'
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
                      : c.status === 'carded'
                        ? `Open the ${c.board || 'Kanban'} board (card ${c.taskId})`
                        : c.status === 'error'
                        ? c.error
                        : 'starting…',
                className:
                  ROW +
                  (c.status === 'error'
                    ? 'text-(--ui-danger,#f85149)'
                    : c.status === 'ok' || c.status === 'carded'
                      ? 'text-foreground hover:bg-(--chrome-action-hover)'
                      : c.status === 'launching'
                        ? 'text-(--ui-text-tertiary)'
                        : 'text-foreground hover:border-(--ui-accent) hover:bg-(--chrome-action-hover)'),
                onClick: () => {
                  if (c.status === 'idle') void launchSingle(i)
                  else if (c.status === 'ok' && c.stored) void host.openSession?.(c.stored)
                  else if (c.status === 'carded') host.navigate?.('/kanban')
                },
                children: [
                  jsx('span', { className: 'shrink-0 w-3 text-center', children: icon }),
                  jsx('span', { className: 'min-w-0 flex-1 truncate', children: c.title }),
                  jsx('span', {
                    className: 'shrink-0 text-(--ui-text-tertiary)',
                    children:
                      c.status === 'idle'
                        ? 'launch'
                        : c.status === 'ok'
                          ? 'open'
                          : c.status === 'carded'
                            ? `card ${c.taskId}`
                            : c.status === 'launching'
                              ? 'starting'
                              : 'failed'
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
    ctx.register({
      id: 'auto-toggle',
      area: PALETTE_AREA,
      data: {
        id: 'spinout.autoToggle',
        label: 'Spin out: toggle automatic follow-up panel',
        keywords: ['spin', 'follow-up', 'auto', 'kanban'],
        run: () => {
          const next = autoOn() ? 'off' : 'on'
          try {
            window.localStorage.setItem(AUTO_KEY, next)
          } catch {}
          host.notify({ kind: 'info', message: `Spin out: automatic follow-up panel ${next}` })
        }
      }
    })
    ctx.register({ id: 'banner', area: COMPOSER_AREAS.top, render: () => jsx(Banner, {}) })
    watchTurns(ctx)
    void probeApi()

    window.__spinOut = { get job() { return job }, spinOut, setJob, launchOne, launchAll, toKanban, autoCheck, dismiss }
    ctx.onDispose?.(() => {
      delete window.__spinOut
    })
  }
}

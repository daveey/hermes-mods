/**
 * Browser Focus — status-bar toggle for the agent-driven Chrome (invisible-chrome-agent).
 *
 *   "Browser hidden"  click → bring the agent's Chrome onto the main display, in front
 *   "Browser shown"   click → send it back to its off-screen virtual display
 *
 * Hidden when no agent Chrome / supervisor is running. Data: GET /api/plugins/browser-focus/status,
 * POST /show, POST /hide (plugins/browser-focus).
 *
 * Route: always THIS machine's gateway (the `local` connection). The backend finds the agent Chrome with
 * `ps` under its own Hermes home, raises it with osascript/NSRunningApplication and moves it on/off the
 * invisible-chrome-agent supervisor's virtual display, all on the host it runs on; "onto the main display"
 * only means anything on the screen this Desktop shows. Through the active gateway (often hermes-hub, a
 * Linux box with no supervisor) the chip just disappeared. Polled every 5 s only while the window is
 * visible; read once when it is shown again.
 */

import { atom, host, STATUSBAR_AREAS, useValue } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'browser-focus'
const POLL_MS = 5_000
const LOCAL = 'local'

const BAR_CLASS =
  'inline-flex h-full items-center gap-1.5 rounded-none px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground'

// ── pure helpers (exported for tests) ─────────────────────────────────────

export function chipFor(status, busy = false) {
  if (!status || typeof status !== 'object' || !status.available) return null
  const shown = !!status.shown
  return {
    shown,
    label: busy ? (shown ? 'Hiding browser…' : 'Showing browser…') : shown ? 'Browser shown' : 'Browser hidden',
    title: shown
      ? "The agent's Chrome is on your screen. Click to send it back off-screen."
      : "The agent's Chrome is off-screen. Click to bring it to the front.",
    icon: shown ? 'codicon-eye' : 'codicon-eye-closed',
    action: shown ? '/hide' : '/show'
  }
}

/** REST options that pin a call to this machine's gateway: none when it is already the routed one. */
export function localRoute(gateways, active) {
  const local = (Array.isArray(gateways) ? gateways : []).find(g => g && g.kind === LOCAL)?.id || LOCAL
  return !active || active === local ? {} : { connectionId: local }
}

const route = () => {
  let active = null
  try { active = host.activeConnectionId?.() || null } catch { active = null }
  return localRoute(host.state?.gateways?.get?.(), active)
}

const isHidden = () => typeof document !== 'undefined' && !!document.hidden

export function createStore(ctx) {
  const $status = atom(null)
  const $busy = atom(false)
  let inflight = null
  const refresh = () => {
    if (inflight) return inflight
    inflight = Promise.resolve().then(() => ctx.rest('/status', route()))
      .then(s => { $status.set(s && typeof s === 'object' ? s : null) })
      .catch(() => { $status.set(null) })
      .finally(() => { inflight = null })
    return inflight
  }
  const toggle = async action => {
    $busy.set(true)
    try {
      await ctx.rest(action, { method: 'POST', ...route() })
    } catch (e) {
      host.notify({ kind: 'error', message: 'Browser toggle failed: ' + String((e && e.message) || e) })
    } finally {
      $busy.set(false)
      refresh()
    }
  }
  return { $status, $busy, refresh, toggle }
}

function Chip({ store }) {
  const status = useValue(store.$status)
  const busy = useValue(store.$busy)
  const chip = chipFor(status, busy)
  if (!chip) return null
  return jsxs('button', {
    type: 'button',
    className: BAR_CLASS,
    'data-slot': 'browser-focus-chip',
    'data-shown': chip.shown ? 'true' : 'false',
    title: chip.title,
    disabled: busy,
    style: chip.shown ? { color: 'var(--ui-accent, #58a6ff)' } : undefined,
    onClick: () => store.toggle(chip.action),
    children: [jsx('span', { className: `codicon ${chip.icon}`, 'aria-hidden': true }), jsx('span', { children: chip.label })]
  })
}

export default {
  id: ID,
  name: 'Browser Focus',
  description: "Status-bar toggle: bring the agent's off-screen Chrome to the front, or hide it again.",
  register(ctx) {
    const store = createStore(ctx)
    store.refresh()
    ctx.setInterval(() => { if (!isHidden()) store.refresh() }, POLL_MS)
    if (typeof document !== 'undefined' && document.addEventListener) {
      const onVis = () => { if (!isHidden()) store.refresh() }
      document.addEventListener('visibilitychange', onVis)
      ctx.onDispose?.(() => document.removeEventListener('visibilitychange', onVis))
    }
    ctx.register({ id: 'chip', area: STATUSBAR_AREAS.right, order: 2, render: () => jsx(Chip, { store }) })
  }
}

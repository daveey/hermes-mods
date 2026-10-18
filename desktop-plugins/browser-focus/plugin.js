/**
 * Browser Focus — status-bar toggle for the agent-driven Chrome (invisible-chrome-agent).
 *
 *   "Browser hidden"  click → bring the agent's Chrome onto the main display, in front
 *   "Browser shown"   click → send it back to its off-screen virtual display
 *
 * Hidden when no agent Chrome / supervisor is running. Data: GET /api/plugins/browser-focus/status,
 * POST /show, POST /hide (plugins/browser-focus).
 */

import { atom, host, STATUSBAR_AREAS, useValue } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'browser-focus'
const POLL_MS = 5_000

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

function createStore(ctx) {
  const $status = atom(null)
  const $busy = atom(false)
  let inflight = null
  const refresh = () => {
    if (inflight) return inflight
    inflight = ctx.rest('/status')
      .then(s => { $status.set(s && typeof s === 'object' ? s : null) })
      .catch(() => { $status.set(null) })
      .finally(() => { inflight = null })
    return inflight
  }
  const toggle = async action => {
    $busy.set(true)
    try {
      await ctx.rest(action, { method: 'POST' })
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
    ctx.setInterval(() => store.refresh(), POLL_MS)
    ctx.register({ id: 'chip', area: STATUSBAR_AREAS.right, order: 2, render: () => jsx(Chip, { store }) })
  }
}

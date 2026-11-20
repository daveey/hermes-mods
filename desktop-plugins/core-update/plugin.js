/**
 * Core Update — status-bar chip for a Hermes core update prepared in the background.
 *
 *   "Building update…"                 an updater is packing the next version off to the side
 *   "Update ready — restart to apply"  a prebuilt app newer than the running checkout is waiting;
 *                                      click → the backend starts the apply-on-quit waiter and quits
 *                                      Desktop; the update lands and Desktop reopens.
 * Hidden otherwise. Data: GET /api/plugins/core-update/status (reads
 * $HERMES_HOME/cache/core-update/state.json; see plugins/core-update/core.py).
 */

import { atom, host, STATUSBAR_AREAS, useValue } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'core-update'
const POLL_MS = 30_000

const BAR_CLASS =
  'inline-flex h-full items-center gap-1.5 rounded-none px-1.5 text-[0.6875rem] text-(--ui-text-tertiary) transition-colors hover:bg-(--chrome-action-hover) hover:text-foreground'

// ── pure helpers (exported for tests) ─────────────────────────────────────

export function chipFor(status, restarting = false) {
  if (!status || typeof status !== 'object') return null
  const short = typeof status.sha === 'string' ? status.sha.slice(0, 10) : ''
  const n = Number(status.commits) || 0
  const what = n ? `${n} new commit${n === 1 ? '' : 's'} (${short})` : short
  if (restarting) return { kind: 'restarting', label: 'Restarting to update…', title: `Applying ${what}`, clickable: false }
  if (status.state === 'building') {
    return { kind: 'building', label: 'Building update…', title: `Packing ${what} in the background`, clickable: false }
  }
  if (status.state === 'ready') {
    return {
      kind: 'ready',
      label: 'Update ready — restart to apply',
      title: `${what} is built. Click to quit and reopen Hermes; the update is applied in between.`,
      clickable: !!status.can_restart
    }
  }
  return null
}

function createStore(ctx) {
  const $status = atom(null)
  const $restarting = atom(false)
  let inflight = null
  const refresh = () => {
    if (inflight) return inflight
    inflight = ctx.rest('/status')
      .then(s => { $status.set(s && typeof s === 'object' ? s : null) })
      .catch(() => { $status.set(null) })
      .finally(() => { inflight = null })
    return inflight
  }
  const restart = async () => {
    $restarting.set(true)
    try {
      const r = await ctx.rest('/restart', { method: 'POST' })
      if (!r || !r.ok) {
        $restarting.set(false)
        host.notify({ kind: 'warning', message: 'Update not applied: ' + ((r && r.reason) || 'the backend refused the restart.') })
        refresh()
      }
    } catch (e) {
      $restarting.set(false)
      host.notify({ kind: 'error', message: 'Update restart failed: ' + String(e && e.message || e) })
    }
  }
  return { $status, $restarting, refresh, restart }
}

function Chip({ store }) {
  const status = useValue(store.$status)
  const restarting = useValue(store.$restarting)
  const chip = chipFor(status, restarting)
  if (!chip) return null
  const icon = chip.kind === 'ready' ? 'codicon-arrow-circle-up' : 'codicon-loading codicon-modifier-spin'
  return jsxs('button', {
    type: 'button',
    className: BAR_CLASS,
    'data-slot': 'core-update-chip',
    'data-kind': chip.kind,
    title: chip.title,
    disabled: !chip.clickable,
    style: chip.kind === 'ready' ? { color: 'var(--ui-green, #3fb950)' } : undefined,
    onClick: chip.clickable ? () => store.restart() : undefined,
    children: [jsx('span', { className: `codicon ${icon}`, 'aria-hidden': true }), jsx('span', { children: chip.label })]
  })
}

export default {
  id: ID,
  name: 'Core Update',
  description: 'Status-bar chip: a background-built Hermes update, and restart-to-apply.',
  register(ctx) {
    const store = createStore(ctx)
    store.refresh()
    ctx.setInterval(() => store.refresh(), POLL_MS)
    ctx.register({ id: 'chip', area: STATUSBAR_AREAS.right, order: 1, render: () => jsx(Chip, { store }) })
  }
}

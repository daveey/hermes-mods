// Real Python dispatcher/merge/present payload -> actual SidebarView DOM.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { loadPlugin, mount, React } from './sdk-mock.mjs'
const snap = JSON.parse(readFileSync(0, 'utf8'))
const P = await loadPlugin({ SIDEBAR_SECTIONS_TOP_AREA: 'sidebar.sections.top' })
const pane = await mount(React.createElement(P.SidebarView, { snap, open: true, setOpen() {}, write: async () => ({}) }))
try {
  const row = pane.container.querySelector('[data-sidebar-lane="babybear"]')
  const ticks = row.querySelectorAll('[data-tick-sources]')
  const tick = ticks[ticks.length - 1]
  const observed = tick.querySelector('[data-source="default"]')
  const missing = tick.querySelector('[data-source="askeve"]')
  assert.equal(observed.dataset.component, 'rejecting')
  assert.ok(observed.querySelector('[fill="#ef4444"]'))
  const hatches = node => [...node.querySelectorAll('rect')].filter(r => r.getAttribute('fill')?.startsWith('url('))
  assert.equal(hatches(observed).length, 0)
  assert.equal(missing.dataset.component, 'unknown')
  assert.equal(hatches(missing).length, 1, missing.outerHTML)
  assert.match(tick.querySelector('title').textContent, /askeve: unknown · no samples/)
  assert.match(tick.querySelector('title').textContent, /default: rejecting · running 1/)
  console.log('real dispatch + missing board -> source-qualified red/hatched DOM PASS')
} finally { await pane.close() }

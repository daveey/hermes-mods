// The card-status totals, "not live" and the expand toggle move into the Sessions tab strip (beside Sessions /
// Bots) when the Desktop has that slot and it is mounted; otherwise they stay on the strip's first row.
import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount, React } from './sdk-mock.mjs'

const lane = () => ({ share: 20, strip: { blocked: 1, running: 1, other: 3, tokens: [1] }, status: { you: 1, run: 1 } })
const snap = { configured: true, lanes: { hermes: lane(), games: lane() }, status: { you: 1, ext: 133, worker: 49, run: 25, dep: 98 } }
const h = React.createElement

test('totals render in the Sessions tab-strip slot, not on the strip row, and stay interactive', async () => {
  const P = await loadPlugin({ SESSIONS_TAB_STRIP_AREA: 'sessions.tabStrip' })
  assert.equal(P.SESSIONS_TAB_STRIP_AREA, 'sessions.tabStrip')
  const registered = []
  P.default.register({ register: (c) => { registered.push(c); return () => {} }, onDispose: () => {} })
  const slot = registered.find((c) => c.area === 'sessions.tabStrip')
  assert.ok(slot, 'registers a contribution in the tab-strip area')
  const view = await mount(h('div', null,
    h('div', { id: 'tabs' }, slot.render()),
    h('div', { id: 'main' }, h(P.Lanes, { ctx: { rest: async () => snap } }))))
  try {
    await view.flush()
    const tabs = view.container.querySelector('#tabs'), main = view.container.querySelector('#main')
    const totals = tabs.querySelector('[data-slot="lanes-tab-totals"] .hermes-lanes-status')
    assert.ok(totals, 'status totals are in the tab strip')
    assert.ok(tabs.querySelector('.hermes-lanes-toggle'), 'expand toggle moved with them')
    assert.equal(main.querySelector('.hermes-lanes-mini > .hermes-lanes-status'), null, 'not duplicated on the strip')
    assert.equal(main.querySelector('.hermes-lanes-toggle'), null)
    assert.ok(main.querySelector('.hermes-lanes-pills'), 'lane pills stay on the strip')
    assert.match(tabs.textContent, /133/)
    // Clicking a category in the tab strip drives the strip's own state.
    const ext = tabs.querySelector('[data-status="ext"]')
    await view.click(ext)
    await view.flush()
    assert.equal(tabs.querySelector('[data-status="ext"]').getAttribute('data-selected'), '1')
    // The toggle in the tab strip expands the strip's body in the main pane.
    await view.click(tabs.querySelector('.hermes-lanes-toggle'))
    await view.flush()
    assert.equal(main.querySelector('.hermes-lanes-strip').getAttribute('data-open'), '1')
    await view.click(tabs.querySelector('.hermes-lanes-toggle'))
    await view.flush()
  } finally { await view.close() }
})

test('without the slot (older Desktop) the totals stay on the strip row', async () => {
  const P = await loadPlugin()
  assert.equal(P.SESSIONS_TAB_STRIP_AREA, '')
  const registered = []
  P.default.register({ register: (c) => { registered.push(c); return () => {} }, onDispose: () => {} })
  assert.equal(registered.some((c) => c.id === 'tab-totals'), false)
  const view = await mount(h(P.Lanes, { ctx: { rest: async () => snap } }))
  try {
    assert.ok(view.container.querySelector('.hermes-lanes-mini > .hermes-lanes-status'))
    assert.ok(view.container.querySelector('.hermes-lanes-mini > .hermes-lanes-toggle'))
  } finally { await view.close() }
})

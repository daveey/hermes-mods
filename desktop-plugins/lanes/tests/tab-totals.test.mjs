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

test('combined modern SDK keeps tab totals live and status filters interactive without a legacy panel', async () => {
  localStorage.clear()
  const P = await loadPlugin({ SIDEBAR_SECTIONS_TOP_AREA: 'sidebar.sections.top',
    SESSIONS_TAB_STRIP_AREA: 'sessions.tabStrip', SESSION_FILTER_AREA: 'sidebar.filter' })
  const registered = new Map()
  const body = { ...snap, sessions: { active: { hermes: { run: 1 } }, waiting: { games: { ext: 1 } } } }
  const ctx = { rest: async () => body, onDispose() {}, register(c) {
    registered.set(c.id, c); return () => registered.delete(c.id)
  } }
  P.default.register(ctx)
  assert.equal(registered.get('lanes-sidebar').area, 'sidebar.sections.top', 'above-Pinned SDK area preserved alongside tab totals')
  const view = await mount(h('div', null,
    h('div', { id: 'tabs' }, registered.get('tab-totals').render()),
    h('div', { id: 'main' }, registered.get('strip').render()),
    h('div', { id: 'sidebar' }, registered.get('lanes-sidebar').render())))
  try {
    await view.flush()
    const tabs = view.container.querySelector('#tabs'), main = view.container.querySelector('#main')
    assert.ok(tabs.querySelector('.hermes-lanes-status'), 'combined SDK populates Sessions tab totals')
    assert.match(tabs.textContent, /133/)
    assert.match(tabs.textContent, /not live/, 'push unavailable remains visible')
    assert.equal(main.querySelector('.hermes-lanes-status'), null, 'no duplicate top-bar totals')
    assert.equal(main.querySelector('.hermes-lanes-pills'), null, 'sidebar replaces legacy lane pills')
    assert.equal(tabs.querySelector('.hermes-lanes-toggle'), null, 'no toggle for an absent legacy panel')
    await view.click(tabs.querySelector('[data-status="ext"]'))
    await view.flush()
    assert.equal(tabs.querySelector('[data-status="ext"]').dataset.selected, '1')
    const filter = registered.get('session-filter')
    assert.ok(filter, 'click registers the real session-list filter')
    assert.equal(filter.data.match({ id: 'waiting' }), true)
    assert.equal(filter.data.match({ id: 'active' }), false)
    await React.act(async () => filter.data.onClear())
    await view.flush()
    assert.equal(registered.has('session-filter'), false)
    assert.equal(tabs.querySelector('[data-status="ext"]').dataset.selected, '0')
    assert.ok(view.container.querySelector('[data-slot="lanes-sidebar"]'))
    await view.click(tabs.querySelector('[data-status="run"]'))
    await view.flush()
    assert.ok(registered.has('session-filter'))
  } finally {
    await view.close()
    assert.equal(registered.has('session-filter'), false, 'unmount cleans up active filter')
    assert.equal(P.tabTotalsArea().item, null, 'unmount clears hosted totals')
    localStorage.clear()
  }
})

test('modern sidebar without tab slot keeps interactive totals in the top bar', async () => {
  localStorage.clear()
  const P = await loadPlugin({ SIDEBAR_SECTIONS_TOP_AREA: 'sidebar.sections.top', SESSION_FILTER_AREA: 'sidebar.filter' })
  const registered = new Map()
  const ctx = { rest: async () => ({ ...snap, sessions: {} }), onDispose() {}, register(c) {
    registered.set(c.id, c); return () => registered.delete(c.id)
  } }
  P.default.register(ctx)
  const view = await mount(registered.get('strip').render())
  try {
    await view.flush()
    await view.click(view.container.querySelector('[data-status="run"]'))
    await view.flush()
    assert.equal(view.container.querySelector('[data-status="run"]').dataset.selected, '1')
    assert.ok(registered.get('session-filter'))
    assert.match(view.container.textContent, /not live/)
    assert.equal(view.container.querySelector('.hermes-lanes-pills'), null)
  } finally { await view.close(); localStorage.clear() }
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

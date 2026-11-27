import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount, React } from './sdk-mock.mjs'

const P = await loadPlugin({ SIDEBAR_SECTIONS_TOP_AREA: 'sidebar.sections.top', SIDEBAR_SECTIONS_AREA: 'sidebar.sections', SESSION_FILTER_AREA: 'sidebar.filter' })
const T0 = Date.UTC(2026, 10, 27, 0, 0) / 1000
const comp = (source, over = {}) => ({ source, t: 0, state: 'partial', running: 1, cap: 4, rejected: 0, visual: { height: .5, opacity: .5 }, ...over })
const missing = source => ({ source, state: 'unknown', running: null, cap: null, rejected: null, reason: 'no samples', visual: { height: 1, opacity: 1 } })
const multi = (i, components, over = {}) => ({ t: T0 + i * 1800, state: 'unknown', running: null, rejected: null, cap: null, visual: { height: 1, opacity: 1 }, components, ...over })
const cells = () => Array.from({ length: 4 }, (_, i) => i === 0 ? multi(i, [comp('babybear'), comp('askeve')])
  : i === 1 ? multi(i, [comp('babybear'), missing('askeve')])
  : i === 2 ? multi(i, [comp('babybear', { state: 'rejecting', running: 4, rejected: 3, visual: { height: 1, opacity: 1 } }), missing('askeve')])
  : multi(i, [missing('babybear'), missing('askeve')]))

const snapshot = (rowOver = {}, over = {}) => ({ configured: true, fleet_slots: 6, status: { run: 2, you: 1 }, totals: { not_ready: 3 },
  spend: { hour: 1, day: 2, week: 3 }, sidebar: { idle_lanes: [] }, governors: { idle_total: 0, rows: [], counts: { on: 0, off: 0 } },
  lanes: { work: { share: 20, actual: .2, target: .3, status: { run: 2, you: 1 }, governor_held: 0, not_ready: 3, limits: { max_workers: 4 }, boards: ['a'],
    spend: { hour: .5, day: 7, week: 49 }, ticks: { window: '24h', bucket_s: 1800, cap: 4, cells: cells() }, ...rowOver } }, ...over })
function setup(body) {
  const ctx = { rest: async () => body, onDispose() {}, register() { return () => {} } }
  P.sourceFor(ctx).subscribe = (_p, cb) => { cb(body); return Object.assign(() => {}, { update() {}, refresh() {} }) }
  return ctx
}
const open = async (body) => {
  localStorage.clear(); localStorage.setItem(P.SIDEBAR_OPEN_KEY, '1')
  const ctx = setup(body)
  await P.sourceFor(ctx).read(24, '24h', true)
  const pane = await mount(React.createElement(P.SidebarLanes, { ctx }))
  const slider = pane.container.querySelector('[role=slider]')
  if (slider) slider.getBoundingClientRect = () => ({ left: 100, width: 200, top: 0, height: 18, right: 300, bottom: 18 })
  return { pane, slider }
}
const ptr = (pane, el, type, frac) => pane.event(el, type, { clientX: 100 + frac * 200 })
const ticks = pane => [...pane.container.querySelectorAll('[data-tick]')]

test('healthy cell: two equal bands, nothing hatched; missing source hatches only its own half', async () => {
  const { pane } = await open(snapshot())
  try {
    const [ok, partial] = ticks(pane)
    assert.equal(ok.dataset.tickSources, '2')
    assert.equal(ok.querySelectorAll('[data-component-unknown="1"]').length, 0)
    assert.equal(ok.dataset.tickUnknown, '0')
    const bands = [...partial.querySelectorAll('[data-source]')]
    assert.deepEqual(bands.map(b => [b.dataset.source, b.dataset.component]), [['babybear', 'partial'], ['askeve', 'unknown']])
    // each source owns exactly half of the 14-unit tick height; hatch rect only in the missing band
    const hatch = bands[1].querySelector('rect[fill^="url(#"]')
    assert.ok(hatch); assert.equal(Number(hatch.getAttribute('y')), 7); assert.equal(Number(hatch.getAttribute('height')), 7)
    assert.equal(bands[0].querySelector('rect[fill^="url(#"]'), null)
    assert.equal(partial.dataset.tickUnknown, '1')
    // all sources missing hatches both shares, still per-source
    assert.equal(ticks(pane)[3].querySelectorAll('[data-component-unknown="1"]').length, 2)
  } finally { await pane.close(); localStorage.clear() }
})

test('known rejecting source keeps red beside a hatched missing source', async () => {
  const { pane } = await open(snapshot())
  try {
    const t = ticks(pane)[2]
    const [bb, ask] = t.querySelectorAll('[data-source]')
    assert.equal(bb.dataset.component, 'rejecting')
    assert.ok(bb.querySelector('rect[fill="#ef4444"]'))
    assert.equal(bb.querySelector('rect[fill^="url(#"]'), null)
    assert.equal(ask.dataset.componentUnknown, '1')
  } finally { await pane.close(); localStorage.clear() }
})

test('tooltips name every component with reason, running/cap/rejections', async () => {
  const { pane } = await open(snapshot())
  try {
    const t = ticks(pane)
    const title = t[2].querySelector('title').textContent
    assert.match(title, /babybear: rejecting · running 4 · cap 4 · 3 rejected/)
    assert.match(title, /askeve: unknown · no samples/)
    assert.match(title, /aggregate running unknown/)
    assert.match(t[0].querySelector('title').textContent, /askeve: partial · running 1 · cap 4 · 0 rejected/)
    assert.doesNotMatch(title, /cap ∞/)
  } finally { await pane.close(); localStorage.clear() }
})

test('cap preview does not recolour components; preview is marked unknown with observed sources preserved', async () => {
  const { pane, slider } = await open(snapshot())
  try {
    await ptr(pane, slider, 'pointerdown', 1 / 6 * 0.97)
    const t = ticks(pane)
    assert.equal(t.length, 4)
    assert.ok(t.every(g => g.dataset.tickPreviewUnknown === '1'))
    assert.equal(pane.container.querySelectorAll('[data-tick="rejecting"]').length, 0)
    assert.equal(t[2].querySelectorAll('[data-component="rejecting"]').length, 1, 'observed red kept')
    assert.equal(t[0].querySelectorAll('[data-component="partial"]').length, 2)
    assert.match(t[0].querySelector('title').textContent, /cap preview unknown/)
    const caption = pane.container.querySelector('[data-caption]').textContent
    assert.match(caption, /preview unknown/); assert.doesNotMatch(caption, /would have turned cards away/)
    assert.match(caption, /of 6 shared-pool slots/)
  } finally { await pane.close(); localStorage.clear() }
})

test('slider tooltip/aria name the shared pool; unknown capacity is disabled', async () => {
  const { pane, slider } = await open(snapshot())
  try {
    assert.equal(slider.getAttribute('aria-valuetext'), 'cap 4 of 6 shared-pool slots')
    assert.equal(slider.title, 'cap 4 of 6 shared-pool slots')
  } finally { await pane.close(); localStorage.clear() }
  const unk = await open(snapshot({}, { fleet_slots: null }))
  try {
    const s = unk.slider
    assert.equal(s.getAttribute('aria-disabled'), 'true')
    assert.equal(s.getAttribute('aria-valuetext'), 'fleet slots unknown')
    assert.doesNotMatch(s.getAttribute('aria-valuetext') + s.title, /shared-pool slots/)
  } finally { await unk.pane.close(); localStorage.clear() }
})

test('out-of-range configured cap is preserved, not shown as infinity', async () => {
  const { pane, slider } = await open(snapshot({ limits: { max_workers: 9 } }))
  try {
    assert.equal(slider.getAttribute('aria-valuetext'), 'cap 9 of 6 shared-pool slots')
    assert.equal(slider.title, 'cap 9 of 6 shared-pool slots')
    assert.equal(pane.container.querySelector('.lanes-cursor').dataset.capCursor, '9')
    assert.equal(slider.getAttribute('aria-valuenow'), '6', 'cursor clamps to last slot, not the infinity stop (7)')
    assert.equal(pane.container.querySelector('.lanes-inf').dataset.open, '0')
    assert.doesNotMatch(slider.getAttribute('aria-valuetext'), /∞/)
  } finally { await pane.close(); localStorage.clear() }
})

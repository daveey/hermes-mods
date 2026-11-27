import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount, React } from './sdk-mock.mjs'

const P = await loadPlugin({ SIDEBAR_SECTIONS_TOP_AREA: 'sidebar.sections.top', SIDEBAR_SECTIONS_AREA: 'sidebar.sections', SESSION_FILTER_AREA: 'sidebar.filter' })
const T0 = Date.UTC(2026, 10, 27, 0, 0) / 1000
const cell = (i, over = {}) => ({ t: T0 + i * 1800, state: 'empty', running: 0, cap: 4, rejected: 0, rejection_unknown: false, visual: { height: 0, opacity: 1 }, ...over })
const cellsOf = (fn = () => ({})) => Array.from({ length: 48 }, (_, i) => cell(i, fn(i)))
const preview = (affected, now, mark = 'rejecting') => ({ cells: cellsOf(i => i < affected ? { state: mark, rejected: 2, visual: { height: 1, opacity: 1 } } : {}), affected, total: 48, now })
const snapshot = (over = {}, rowOver = {}) => ({ configured: true, fleet_slots: 6, status: { run: 2, you: 1 }, totals: { not_ready: 3 },
  spend: { hour: 1.5, day: 20.25, week: 140 }, sidebar: { cost_today: 20.25, idle_lanes: [] },
  governors: { idle_total: 0, rows: [], counts: { on: 0, off: 0 } },
  lanes: { work: { share: 20, actual: .2, target: .3, status: { run: 2, you: 1 }, governor_held: 0, not_ready: 3, top_reason: 'waiting on a long reason that must truncate',
    limits: { max_workers: 4 }, boards: ['a'], spend: { hour: 0.5, day: 7, week: 49.5 },
    ticks: { window: '24h', bucket_s: 1800, cap: 4, cells: cellsOf(i => i === 1 ? { state: 'partial', running: 1, visual: { height: .4, opacity: .5 } }
      : i === 2 ? { state: 'full', running: 4, visual: { height: 1, opacity: 1 } }
      : i === 3 ? { state: 'rejecting', running: 4, rejected: 3, visual: { height: 1, opacity: 1 } }
      : i === 4 ? { state: 'rejecting', running: 4, rejection_unknown: true, visual: { height: 1, opacity: 1 } } : {}),
    previews: { 0: preview(48, 3), 2: preview(9, 3), 5: preview(1, 3), unlimited: { cells: cellsOf(), affected: 0, total: 48, now: 3 } } },
    ...rowOver } }, ...over })

function setup(body = snapshot(), { failWrite = false } = {}) {
  const writes = [], updates = []
  let push
  const ctx = { rest: async (path, options) => {
    if (options?.method === 'PUT') { writes.push([path, options.body]); if (failWrite) throw Error('boom'); return { ok: true } }
    return body }, onDispose() {}, register() { return () => {} } }
  P.sourceFor(ctx).subscribe = (params, cb) => { updates.push(['subscribe', params]); push = cb; cb(body)
    return Object.assign(() => {}, { update: next => updates.push(['update', next]), refresh() {} }) }
  return { ctx, writes, updates, push: (...args) => push(...args) }
}
const rect = el => { el.getBoundingClientRect = () => ({ left: 100, width: 200, top: 0, height: 18, right: 300, bottom: 18 }) }
const open = async (t) => {
  localStorage.clear(); localStorage.setItem(P.SIDEBAR_OPEN_KEY, '1')
  await P.sourceFor(t.ctx).read(24, '24h', true) // settles the gateway the write goes to
  const pane = await mount(React.createElement(P.SidebarLanes, { ctx: t.ctx }))
  const slider = pane.container.querySelector('[role=slider]')
  if (slider) rect(slider)
  return { pane, slider }
}
const pointer = (pane, el, type, frac) => pane.event(el, type, { clientX: 100 + frac * 200 })

test('compact row renders name, chip slot, 48 ticks, pips per fleet slot, cap cursor and spend', async () => {
  const t = setup(); const { pane, slider } = await open(t)
  try {
    const row = pane.container.querySelector('[data-sidebar-lane="work"]')
    assert.ok(row.querySelector('.lanes-row-name'))
    assert.match(row.querySelector('.lanes-row-chip').title, /long reason/)
    assert.equal(row.querySelectorAll('[data-tick]').length, 48)
    assert.equal(row.querySelectorAll('[data-pip]').length, 6)
    assert.equal(row.querySelectorAll('[data-pip][data-run="1"]').length, 2)
    assert.equal(row.querySelectorAll('[data-pip][data-open="1"]').length, 4)
    assert.equal(row.querySelector('.lanes-cursor').dataset.capCursor, '4')
    assert.equal(row.querySelector('.lanes-inf').textContent, '∞')
    assert.equal(slider.getAttribute('aria-valuemax'), '7')
    assert.equal(slider.getAttribute('aria-valuenow'), '4')
    assert.equal(row.querySelector('.lanes-cost').getAttribute('aria-label'), '$0.50/h · $7.00/d · $49.50/wk')
    assert.equal(row.querySelector('.lanes-cost-d').textContent, '$7.00')
    assert.equal(pane.container.querySelector('[data-fleet-spend]').getAttribute('aria-label'), '$1.50/h · $20.25/d · $140.00/wk')
    assert.deepEqual([...pane.container.querySelector('[data-fleet-spend]').children].map(c => c.textContent), ['$1.50', '$20.25', '$140'])
    // state encoding and tooltips
    const ticks = [...row.querySelectorAll('[data-tick]')]
    assert.equal(ticks[0].querySelector('rect').getAttribute('fill'), '#2f3440')
    assert.equal(ticks[1].querySelectorAll('rect')[1].getAttribute('fill'), '#0ea5e9')
    assert.equal(ticks[1].querySelectorAll('rect')[1].getAttribute('opacity'), '0.5')
    assert.equal(ticks[2].querySelectorAll('rect')[1].getAttribute('fill'), '#38bdf8')
    assert.equal(ticks[3].querySelectorAll('rect')[1].getAttribute('fill'), '#ef4444')
    assert.ok(ticks[3].querySelector('.lanes-tick-notch'))
    assert.equal(ticks[3].dataset.tickUnknown, '0')
    assert.equal(ticks[4].dataset.tickUnknown, '1')
    assert.equal(ticks[1].querySelector('title').textContent, '2026-11-27 00:30–01:00 UTC · running 1 · cap 4 · partial · 0 rejected (ready cards turned away)')
    assert.match(ticks[3].querySelector('title').textContent, /rejecting · 3 rejected \(ready cards turned away\)$/)
    assert.match(ticks[4].querySelector('title').textContent, /rejecting · rejections unknown$/)
    assert.doesNotMatch(ticks[4].querySelector('title').textContent, /0 rejected/)
  } finally { await pane.close(); localStorage.clear() }
})

test('plugin stylesheet carries the 360px container query and keeps the legacy strip rules', async () => {
  const t = setup()
  const registered = []
  const ctx = { ...t.ctx, register: c => registered.push(c), onDispose() {} }
  P.default.register(ctx)
  const css = [...document.head.querySelectorAll('style')].map(s => s.textContent).join('\n')
  assert.match(css, /container-type:inline-size/)
  assert.match(css, /@container lanes-sb \(max-width:360px\)\{[^}]*\.lanes-cost-h,\.lanes-cost-w/)
  assert.match(css, /\.hermes-lanes-strip\{/)
  assert.match(css, /height:38px/)
})

test('window toolbar defaults to 24h, persists, and moves the shared store connection with update()', async () => {
  const t = setup(); const { pane } = await open(t)
  try {
    assert.deepEqual(t.updates[0], ['subscribe', { hours: 24, window: '24h', expanded: true }])
    const labels = [...pane.container.querySelectorAll('[data-window]')]
    assert.deepEqual(labels.map(b => b.dataset.window), ['6h', '24h', '7d'])
    assert.equal(labels[1].getAttribute('aria-pressed'), 'true')
    await pane.click(labels[2])
    assert.deepEqual(t.updates.at(-1), ['update', { hours: 168, window: '7d', expanded: true }])
    assert.equal(localStorage.getItem(P.SIDEBAR_WINDOW_KEY), '168')
    assert.equal(pane.container.querySelector('[data-window="7d"]').getAttribute('aria-pressed'), 'true')
    assert.equal(t.updates.filter(u => u[0] === 'subscribe').length, 1, 'one connection, moved by update')
  } finally { await pane.close() }
  localStorage.setItem(P.SIDEBAR_OPEN_KEY, '1')
  const again = setup(); const second = await mount(React.createElement(P.SidebarLanes, { ctx: again.ctx }))
  try { assert.deepEqual(again.updates[0], ['subscribe', { hours: 168, window: '7d', expanded: true }]) } finally { await second.close(); localStorage.clear() }
  assert.equal(P.loadSidebarWindow({ getItem: () => 'junk' }), 24)
})

test('totals-only subscriber starts with the sidebar window; legacy window key is untouched', async () => {
  localStorage.clear(); localStorage.setItem(P.SIDEBAR_WINDOW_KEY, '6')
  const t = setup(); const pane = await mount(React.createElement(P.TopTotals, { ctx: t.ctx }))
  try {
    assert.deepEqual(t.updates[0], ['subscribe', { hours: 6, window: '6h', expanded: true }])
    assert.equal(P.loadWindow(), 6) // legacy strip default unchanged (no stored value → 6)
    assert.equal(localStorage.getItem(P.STORE_KEY), null)
  } finally { await pane.close(); localStorage.clear() }
})

test('dragging recolours from server previews with caption, commits on release only, and holds draft until hub confirms', async () => {
  const t = setup(); const { pane, slider } = await open(t)
  try {
    const before = pane.container.querySelector('.lanes-ticks').outerHTML
    const calls = t.updates.length
    await pointer(pane, slider, 'pointerdown', 2 / 6 * 0.97) // stop 2
    assert.equal(slider.getAttribute('aria-valuenow'), '2')
    assert.notEqual(pane.container.querySelector('.lanes-ticks').outerHTML, before)
    assert.equal(pane.container.querySelectorAll('[data-tick="rejecting"]').length, 9)
    assert.equal(pane.container.querySelector('[data-caption]').textContent, 'cap 2 would have turned cards away in 9 of 48 periods (now 3) · release to save')
    assert.match(pane.container.querySelector('.lanes-pending').textContent, /cap 2/)
    await pointer(pane, slider, 'pointermove', 5 / 6 * 0.97)
    assert.equal(pane.container.querySelectorAll('[data-tick="rejecting"]').length, 1)
    assert.match(pane.container.querySelector('[data-caption]').textContent, /^cap 5 would have turned cards away in 1 of 48/)
    assert.equal(t.writes.length, 0, 'no write while dragging')
    assert.equal(t.updates.length, calls, 'no forecast or stream request while dragging')
    await pointer(pane, slider, 'pointerup', 2 / 6 * 0.97)
    assert.deepEqual(t.writes, [['/v1/lanes/work', { max_workers: 2 }]])
    // PUT receipt alone does not release the draft
    assert.equal(slider.getAttribute('aria-valuenow'), '2')
    assert.equal(slider.dataset.saving, '1')
    assert.match(pane.container.querySelector('.lanes-pending').textContent, /saving/)
    // a later snapshot that still shows the old cap keeps it
    await React.act(async () => t.push(snapshot()))
    assert.equal(slider.dataset.saving, '1')
    // hub confirmation releases it
    await React.act(async () => t.push(snapshot({}, { limits: { max_workers: 2 } })))
    assert.equal(slider.dataset.saving, '0')
    assert.equal(pane.container.querySelector('[data-caption]'), null)
    assert.equal(slider.getAttribute('aria-valuenow'), '2')
  } finally { await pane.close(); localStorage.clear() }
})

test('far right 3% is infinity and writes null; releasing at the current cap writes nothing', async () => {
  const t = setup(); const { pane, slider } = await open(t)
  try {
    await pointer(pane, slider, 'pointerdown', 0.99)
    assert.equal(slider.getAttribute('aria-valuenow'), '7')
    assert.equal(slider.getAttribute('aria-valuetext'), 'no cap (∞)')
    assert.match(pane.container.querySelector('[data-caption]').textContent, /^cap ∞ would have turned cards away in 0 of 48/)
    await pointer(pane, slider, 'pointerup', 0.99)
    assert.deepEqual(t.writes, [['/v1/lanes/work', { max_workers: null }]])
    await React.act(async () => t.push(snapshot({}, { limits: { max_workers: null } })))
    assert.equal(slider.dataset.saving, '0')
    await pointer(pane, slider, 'pointerdown', 0.5)
    await pointer(pane, slider, 'pointermove', 0.99)
    await pointer(pane, slider, 'pointerup', 0.99)
    assert.equal(t.writes.length, 1)
    // first clicks on a pip: click at zero is a legal cap
    await pointer(pane, slider, 'pointerdown', 0.0)
    await pointer(pane, slider, 'pointerup', 0.0)
    assert.deepEqual(t.writes.at(-1), ['/v1/lanes/work', { max_workers: 0 }])
  } finally { await pane.close(); localStorage.clear() }
})

test('failed write reverts the cap and shows an inline error', async () => {
  const t = setup(snapshot(), { failWrite: true }); const { pane, slider } = await open(t)
  try {
    await pointer(pane, slider, 'pointerdown', 2 / 6 * 0.97)
    await pointer(pane, slider, 'pointerup', 2 / 6 * 0.97)
    assert.equal(t.writes.length, 1)
    assert.equal(slider.getAttribute('aria-valuenow'), '4')
    assert.equal(slider.dataset.pending, '0')
    assert.match(pane.container.querySelector('.lanes-row-error[role=alert]').textContent, /not saved: boom/)
  } finally { await pane.close(); localStorage.clear() }
})

test('keyboard: arrows step, Home is 0, End is infinity, commit on key release, aria slider', async () => {
  const t = setup(); const { pane, slider } = await open(t)
  const key = async (type, k) => React.act(async () => slider.dispatchEvent(new window.KeyboardEvent(type, { key: k, bubbles: true, cancelable: true })))
  try {
    assert.equal(slider.getAttribute('aria-valuemin'), '0')
    await key('keydown', 'ArrowRight'); assert.equal(slider.getAttribute('aria-valuenow'), '5')
    await key('keydown', 'ArrowRight'); assert.equal(slider.getAttribute('aria-valuenow'), '6')
    await key('keydown', 'ArrowRight'); assert.equal(slider.getAttribute('aria-valuenow'), '7')
    await key('keydown', 'ArrowRight'); assert.equal(slider.getAttribute('aria-valuenow'), '7')
    assert.equal(t.writes.length, 0)
    await key('keyup', 'ArrowRight')
    assert.deepEqual(t.writes, [['/v1/lanes/work', { max_workers: null }]])
    await key('keydown', 'Home')
    assert.equal(slider.getAttribute('aria-valuenow'), '7', 'pending write locks editing until hub confirmation')
    await React.act(async () => t.push(snapshot({}, { limits: { max_workers: null } })))
    await key('keydown', 'Home'); assert.equal(slider.getAttribute('aria-valuenow'), '0')
    await key('keydown', 'ArrowLeft'); assert.equal(slider.getAttribute('aria-valuenow'), '0')
    await key('keydown', 'End'); assert.equal(slider.getAttribute('aria-valuenow'), '7')
    await key('keydown', 'ArrowDown'); assert.equal(slider.getAttribute('aria-valuenow'), '6')
    await key('keyup', 'ArrowDown')
    assert.deepEqual(t.writes.at(-1), ['/v1/lanes/work', { max_workers: 6 }])
  } finally { await pane.close(); localStorage.clear() }
})

test('slider is disabled for unknown fleet slots and while offline', async () => {
  for (const [body, error, why] of [[snapshot({ fleet_slots: null }), '', 'null slots'], [snapshot({ fleet_slots: 1.5 }), '', 'non-integer slots']]) {
    const t = setup(body); const { pane, slider } = await open(t)
    try {
      assert.equal(slider.getAttribute('aria-disabled'), 'true', why)
      assert.equal(slider.tabIndex, -1)
      assert.equal(pane.container.querySelectorAll('[data-pip]').length, 0)
      await pointer(pane, slider, 'pointerdown', 0.5); await pointer(pane, slider, 'pointerup', 0.5)
      assert.equal(t.writes.length, 0)
      assert.equal(slider.getAttribute('aria-valuetext'), 'fleet slots unknown')
    } finally { await pane.close(); localStorage.clear() }
  }
  const t = setup(); const { pane, slider } = await open(t)
  try {
    await React.act(async () => t.push(undefined, Error('offline')))
    assert.equal(slider.getAttribute('aria-disabled'), 'true')
    await pointer(pane, slider, 'pointerdown', 0.5); await pointer(pane, slider, 'pointerup', 0.5)
    assert.equal(t.writes.length, 0)
  } finally { await pane.close(); localStorage.clear() }
})

test('rows without server ticks or spend degrade without inventing numbers; row click expands details and controls', async () => {
  const body = snapshot(); delete body.lanes.work.ticks; delete body.lanes.work.spend; delete body.spend
  const t = setup(body); const { pane } = await open(t)
  try {
    const row = pane.container.querySelector('[data-sidebar-lane="work"]')
    assert.equal(row.querySelectorAll('[data-tick]').length, 0)
    assert.ok(row.querySelector('.lanes-ticks-empty'))
    assert.equal(row.querySelector('.lanes-cost').getAttribute('aria-label'), 'spend unknown')
    assert.match(pane.container.querySelector('[data-fleet-spend]').getAttribute('aria-label'), /unknown/)
    assert.equal(row.querySelector('button[type=submit]'), null)
    // clicking the slider never toggles the row; clicking elsewhere on the row does
    const slider = row.querySelector('[role=slider]')
    await pane.click(slider)
    assert.equal(row.dataset.expanded, '0')
    await pane.click(row.querySelector('.lanes-row-chip'))
    assert.equal(row.dataset.expanded, '1')
    assert.ok(row.querySelector('button[type=submit]'))
    await pane.click(row.querySelector('.lanes-row-chip'))
    assert.equal(row.dataset.expanded, '0')
  } finally { await pane.close(); localStorage.clear() }
})

test('GOVERNORS section is unchanged and legacy strip still mounts', async () => {
  const t = setup(); const { pane } = await open(t)
  try {
    assert.equal(pane.container.querySelectorAll('[data-sidebar-section]').length, 2)
    assert.equal(pane.container.querySelector('[aria-label="GOVERNORS"]').getAttribute('aria-expanded'), 'false')
  } finally { await pane.close(); localStorage.clear() }
})

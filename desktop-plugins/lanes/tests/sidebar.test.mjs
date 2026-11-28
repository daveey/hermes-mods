import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount, React } from './sdk-mock.mjs'

const P = await loadPlugin({ SIDEBAR_SECTIONS_TOP_AREA: 'sidebar.sections.top', SIDEBAR_SECTIONS_AREA: 'sidebar.sections', SESSION_FILTER_AREA: 'sidebar.filter' })

// Producer schema: governors { holds: {lane: [chip]}, fleet: [line], stale_sources, held_total, fleet_stop };
// lane { holds, waits, fleet_stop, governor_held, admit_per_day }. No GOVERNORS section exists.
const gate = { id: 'lane_gate', name: 'Lane gate', group: 'lane_dial', count: 64, reading: 10, threshold: 50, clears: 'admit or release 64 held',
  readings: [{ board: 'default', lane: 'hermes', reading: 10, threshold: 50, cards: 64 }] }
const hostSlots = { id: 'host_slots', name: 'Host slots', group: 'fleet_dial', count: 1, reading: 1, threshold: 1, stop: false, clears: 'a worker finishes', readings: [] }
const governors = (extra = {}) => ({ holds: { work: [{ ...gate, count: 31 }] }, fleet: [], stale_sources: [], held_total: 31, fleet_stop: false, ...extra })

const snapshot = () => ({ configured: true, planner: { notes: [] }, status: { run: 7, you: 3 }, totals: { not_ready: 41 },
  spend: { hour: 2.13, day: 12.34, week: 89.12 },
  sidebar: { cost_today: 12.34, idle_lanes: ['idle'] },
  governors: governors(),
  lanes: { work: { share: 20, open: 90, not_ready: 41, status: { run: 7, you: 3 }, actual: .2, target: .3,
    governor_held: 31, holds: [{ ...gate, count: 31 }], waits: { parents: 10, scheduled: 5, deploy_ci: 0, triage: 0 }, fleet_stop: false,
    admit_per_day: 50, steps: [{ key: 'ready', count: 90, piling: true }],
    limits: { max_workers: 4, cards_per_day: 20, usd_per_day: 100 }, boards: ['alpha', 'beta'],
    held_sessions: ['chat'], held_cards: ['card'] }, idle: { open: 0 } } })

test('partial governors keep known holds and name each stale source, not a cascade', async () => {
  const gov = governors({ holds: { hermes: [gate] }, fleet: [hostSlots], held_total: 65,
    stale_sources: [{ board: 'askeve', reason: 'stale dispatcher snapshot' }, { board: 'aborted', reason: 'storage_admission' }] })
  const pane = await mount(React.createElement('div', null, React.createElement(P.FleetLines, { gov }),
    React.createElement(P.LaneHolds, { row: { governor_held: 64, holds: gov.holds.hermes } }),
    React.createElement(P.LaneHolds, { row: { governor_held: null, holds: [] } })))
  try {
    const text = pane.container.textContent
    assert.match(text, /2 sources stale/)
    assert.match(text, /askeve: stale dispatcher snapshot/)
    assert.match(text, /aborted: storage_admission/)
    assert.equal(pane.container.querySelectorAll('[data-stale-sources]').length, 1)
    assert.equal(pane.container.querySelectorAll('[data-fleet-governor]').length, 1)
    assert.match(pane.container.querySelector('[data-fleet-governor="host_slots"]').textContent, /Host slots · 1 waiting/)
    assert.match(pane.container.querySelector('[data-lane-hold="lane_gate"]').textContent, /Lane gate 10\/50 · 64/)
    assert.match(pane.container.querySelector('[data-lane-hold="lane_gate"]').title, /default \/ hermes: 10\/50 · 64 held/)
    assert.match(text, /board reading stale/)
  } finally { await pane.close() }
})

test('unavailable governor source reads unknown, never zero held', async () => {
  const pane = await mount(React.createElement(P.FleetLines, { gov: undefined }))
  try { assert.match(pane.container.textContent, /Governor source unknown/) } finally { await pane.close() }
})

for (const stopped of [true, false]) test(`safety stop ${stopped ? 'shows' : 'clears'}: one red fleet line and fleet stop on every lane`, async () => {
  const stop = { id: 'core_update_drain', name: 'Core update drain', group: 'safety', count: 0, stop: true, reading: null, threshold: null, clears: 'update finishes', readings: [] }
  const base = snapshot()
  const snap = { ...base, governors: governors({ holds: {}, fleet: stopped ? [stop] : [], held_total: 0, fleet_stop: stopped }),
    lanes: { work: { ...base.lanes.work, governor_held: 0, holds: [], fleet_stop: stopped }, other: { open: 1, governor_held: 0, holds: [], fleet_stop: stopped } } }
  const pane = await mount(React.createElement(P.SidebarView, { snap, error: '', open: true, setOpen() {} }))
  try {
    assert.equal(pane.container.querySelectorAll('[data-fleet-governor]').length, stopped ? 1 : 0)
    assert.equal(pane.container.querySelectorAll('[data-lane-hold="fleet_stop"]').length, stopped ? 2 : 0)
    assert.match(pane.container.querySelector('[data-sidebar-totals]').textContent, /0 held/)
    if (stopped) {
      const line = pane.container.querySelector('[data-fleet-governor="core_update_drain"]')
      assert.equal(line.dataset.stop, '1')
      assert.match(line.className, /lanes-fleet-safety/)
      assert.match(line.textContent, /stopped.*clears: update finishes/)
      assert.match(pane.container.textContent, /fleet stop/)
    } else assert.doesNotMatch(pane.container.textContent, /fleet stop/)
  } finally { await pane.close() }
})

test('fault and budget chips are needs-you chips with their own wording', async () => {
  const holds = [{ id: 'unassigned', name: 'Unassigned', group: 'fault', count: 2 }, { id: 'spend_budget', name: 'Spend budget', group: 'budget', count: 1 }]
  const pane = await mount(React.createElement(P.LaneHolds, { row: { governor_held: 0, holds } }))
  try {
    const fault = pane.container.querySelector('[data-lane-hold="unassigned"]')
    assert.match(fault.textContent, /2 can't start/)
    assert.match(fault.className, /lanes-hold-fault/)
    assert.match(pane.container.querySelector('[data-lane-hold="spend_budget"]').textContent, /1 needs decision/)
  } finally { await pane.close() }
})

test('waitsText never presents schedule or parent waits as held; unknown stays unknown', () => {
  assert.equal(P.waitsText({ parents: 2, scheduled: 100, deploy_ci: 0, triage: 1 }), '2 on parents · 100 scheduled · 1 triage')
  assert.equal(P.waitsText({ parents: 0, scheduled: 0, deploy_ci: 0, triage: 0 }), '')
  assert.match(P.waitsText({ parents: null, scheduled: null, deploy_ci: null, triage: null }), /— on parents · — scheduled/)
  assert.equal(P.waitsText(undefined), '')
})

test('replay: gate 64 on hermes + host slots 1 + two stale sources folds to 65 held, one chip, one line, one stale line', async () => {
  const base = snapshot()
  const snap = { ...base, governors: governors({ holds: { hermes: [gate] }, fleet: [hostSlots], held_total: 65,
    stale_sources: [{ board: 'askeve', reason: 'stale dispatcher snapshot' }, { board: 'aborted', reason: 'storage_admission' }] }),
    lanes: { hermes: { ...base.lanes.work, governor_held: 64, holds: [gate] } } }
  const pane = await mount(React.createElement(P.SidebarView, { snap, error: '', open: true, setOpen() {} }))
  try {
    assert.match(pane.container.querySelector('[data-sidebar-totals]').textContent, /65 held/)
    assert.equal(pane.container.querySelectorAll('[data-lane-hold]').length, 1)
    assert.equal(pane.container.querySelectorAll('[data-fleet-governor]').length, 1)
    assert.equal(pane.container.querySelectorAll('[data-stale-sources]').length, 1)
    assert.match(pane.container.querySelector('[data-lane-holds="hermes"]').textContent, /Lane gate 10\/50 · 64/)
    assert.doesNotMatch(pane.container.textContent, /GOVERNORS|governor-held/)
  } finally { await pane.close() }
})

test('there is no GOVERNORS section; fleet lines and lane chips live under LANES and fold with it', async () => {
  localStorage.clear()
  const snap = snapshot(); snap.governors.fleet = [hostSlots]
  const pane = await mount(React.createElement(P.SidebarView, { snap, error: '', open: true, setOpen() {} }))
  try {
    assert.equal(pane.container.querySelector('[aria-label="GOVERNORS"]'), null)
    assert.equal(pane.container.querySelectorAll('.lanes-sb-heading').length, 1)
    assert.ok(pane.container.querySelector('[data-governor]') === null)
    assert.doesNotMatch(pane.container.textContent, /GOVERNORS/)
    assert.ok(pane.container.querySelector('[data-fleet-governor="host_slots"]'))
    assert.match(pane.container.querySelector('[data-lane-holds="work"]').textContent, /Lane gate 10\/50 · 31/)
  } finally { await pane.close() }
  const closed = await mount(React.createElement(P.SidebarView, { snap, error: '', open: false, setOpen() {} }))
  try { assert.ok(closed.container.querySelector('[data-fleet-governor], [data-lane-holds]') === null) } finally { await closed.close() }
})

for (const variant of ['loading', 'unknown', 'offline', 'long']) test(`collapsed section keeps ${variant} truth in its inline summary`, async () => {
  localStorage.clear()
  const snap = variant === 'loading' ? undefined : snapshot()
  if (variant === 'unknown') { snap.flow_complete = false; snap.status.run = null; snap.governors = undefined }
  if (variant === 'long') { snap.status.run = 123456789012345; snap.governors.held_total = 987654321098765 }
  const pane = await mount(React.createElement(P.SidebarView, { snap, error: variant === 'offline' ? 'unreachable' : '', open: false, setOpen() {} }))
  try {
    const headings = [...pane.container.querySelectorAll('.lanes-sb-heading')]
    assert.equal(headings.length, 1)
    assert.ok(pane.container.querySelector('.lanes-share-bar, [data-governor], [data-fleet-governor], details, [data-sidebar-lane]') === null)
    const [heading] = headings
    assert.equal(heading.getAttribute('aria-expanded'), 'false')
    const summary = heading.querySelector('.lanes-sb-summary')
    assert.ok(summary, 'summary is inside heading')
    assert.equal(summary.title, summary.textContent)
    if (variant === 'loading') assert.match(heading.textContent, /Loading lanes/)
    if (variant === 'unknown') assert.match(heading.textContent, /Partial.*— running · — held/)
    if (variant === 'offline') assert.match(heading.textContent, /offline.*last known/i)
    if (variant === 'long') { assert.match(heading.textContent, /123456789012345/); assert.match(heading.textContent, /987654321098765 held/) }
  } finally { await pane.close() }
})

for (const open of [false, true]) test(`offline before any snapshot never claims last-known data (${open ? 'expanded' : 'collapsed'})`, async () => {
  localStorage.clear()
  const pane = await mount(React.createElement(P.SidebarView, { snap: undefined, error: 'unreachable', open, setOpen() {} }))
  try {
    const headings = pane.container.querySelectorAll('.lanes-sb-heading')
    assert.equal(headings.length, 1)
    const [lanes] = headings
    assert.equal(lanes.getAttribute('aria-expanded'), String(open))
    assert.match(lanes.textContent, /Lanes offline - retrying · Loading lanes…/)
    assert.equal(lanes.getAttribute('aria-description'), lanes.querySelector('.lanes-sb-summary').title)
    assert.doesNotMatch(pane.container.textContent, /last known/i)
    assert.ok(pane.container.querySelector('.lanes-last-known') === null, 'nothing is styled as last-known data')
    assert.equal(pane.container.querySelectorAll('[data-sidebar-body]').length, open ? 1 : 0)
    if (open) {
      assert.equal(pane.container.querySelector('[data-sidebar-body="lanes"] [role=status]').textContent, 'Lanes offline - retrying')
      assert.match(pane.container.querySelector('[data-sidebar-body="lanes"]').textContent, /Loading lanes…/)
      assert.ok(pane.container.querySelector('[data-sidebar-body="governors"]') === null)
    }
  } finally { await pane.close(); localStorage.clear() }
})

test('offline after a snapshot labels the expanded body as last known and keeps folded holds', async () => {
  localStorage.clear()
  const pane = await mount(React.createElement(P.SidebarView, { snap: snapshot(), error: 'unreachable', open: true, setOpen() {} }))
  try {
    assert.equal(pane.container.querySelector('[data-sidebar-body="lanes"] [role=status]').textContent, 'Lanes offline - retrying · last known')
    assert.equal(pane.container.querySelectorAll('[data-sidebar-body].lanes-last-known').length, 1)
    assert.ok(pane.container.querySelector('[data-lane-hold="lane_gate"]'))
  } finally { await pane.close(); localStorage.clear() }
})

function setup(body = snapshot()) {
  const contributions = [], writes = []
  let push
  const ctx = { rest: async (path, options) => { if (options?.method === 'PUT') writes.push([path, options]); return body },
    onDispose() {}, register(c) { contributions.push(c); return () => {} } }
  P.sourceFor(ctx).subscribe = (_p, cb) => { push = cb; cb(body)
    return Object.assign(() => {}, { update() {}, refresh() {} }) }
  return { ctx, contributions, writes, push: (...args) => push(...args) }
}

test('sidebar registration is separate from top totals and old SDK keeps original strip', async () => {
  const modern = setup()
  P.default.register(modern.ctx)
  assert.equal(modern.contributions.find(c => c.id === 'lanes-sidebar').area, 'sidebar.sections.top')
  assert.equal(modern.contributions.find(c => c.id === 'strip').render().type, P.TopTotals)
  const old = await loadPlugin()
  const legacy = setup()
  old.default.register(legacy.ctx)
  assert.equal(legacy.contributions.some(c => c.id === 'lanes-sidebar'), false)
  assert.equal(legacy.contributions.find(c => c.id === 'strip').render().type, old.Lanes)
})

test('expanded lane survives unmount and remount', async () => {
  localStorage.clear()
  const t = setup()
  await P.sourceFor(t.ctx).read(6, '6h', true)
  const first = await mount(React.createElement(P.SidebarLanes, { ctx: t.ctx }))
  try {
    await first.click(first.container.querySelector('.lanes-sb-heading'))
    await first.click(first.container.querySelector('[data-sidebar-lane="work"] button'))
  } finally { await first.close() }
  const second = await mount(React.createElement(P.SidebarLanes, { ctx: t.ctx }))
  try {
    assert.equal(second.container.querySelector('.lanes-sb-heading').getAttribute('aria-expanded'), 'true')
    assert.equal(second.container.querySelector('[data-sidebar-lane="work"] button').getAttribute('aria-expanded'), 'true')
    assert.ok(second.container.querySelector('button[type=submit]'))
  } finally { await second.close(); localStorage.clear() }
})

test('minimized counts are hub numbers, expand and lane controls persist; offline retains numbers', async () => {
  localStorage.clear()
  const t = setup()
  await P.sourceFor(t.ctx).read(6, '6h', true)
  const pane = await mount(React.createElement(P.SidebarLanes, { ctx: t.ctx }))
  try {
    assert.match(pane.container.textContent, /7 running · 31 held · 3 you · 41 waiting/)
    assert.doesNotMatch(pane.container.textContent, /\$12.34 today|governor-held/)
    assert.ok(pane.container.querySelector('.lanes-share-bar') === null)
    assert.ok(pane.container.querySelector('[data-governor]') === null)
    assert.ok(pane.container.querySelector('[data-sidebar-lane]') === null)
    await pane.click(pane.container.querySelector('.lanes-sb-heading'))
    assert.equal(pane.container.querySelector('[data-fleet-spend]').getAttribute('aria-label'), '$2.13/h · $12.34/d · $89.12/wk')
    assert.equal(localStorage.getItem(P.SIDEBAR_OPEN_KEY), '1')
    assert.equal(pane.container.querySelector('[data-sidebar-lane="idle"]'), null)
    await pane.click(pane.container.querySelector('[data-sidebar-lane="work"] button'))
    assert.equal(pane.container.querySelector('[data-step="ready"]').dataset.piling, '1')
    assert.match(pane.container.textContent, /boards: alpha, beta/)
    assert.match(pane.container.querySelector('[data-lane-waits="work"]').textContent, /10 on parents · 5 scheduled/)
    // The lane gate holds cards: its field is marked, unrelated fields are not.
    const holdingLabels = [...pane.container.querySelectorAll('label[data-holding="1"]')].map(l => l.textContent.trim())
    assert.equal(holdingLabels.length, 1)
    assert.match(holdingLabels[0], /^admit \/ day holding/)
    const setInput = async (label, value) => {
      const input = pane.container.querySelector(`[aria-label="work ${label}"]`)
      await React.act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value)
        input.dispatchEvent(new window.Event('input', { bubbles: true }))
      })
    }
    assert.equal(pane.container.querySelector('[aria-label="work admit / day"]').value, '50')
    await setInput('share', '30')
    await setInput('max workers', '')
    await setInput('admit / day', '64')
    await setInput('cards / day', '24')
    await setInput('$ / day', '80.25')
    await pane.click(pane.container.querySelector('input[type=checkbox]'))
    await pane.click(pane.container.querySelector('button[type=submit]'))
    assert.deepEqual(t.writes[0], ['/v1/lanes/work', { timeoutMs: P.REQUEST_TIMEOUT_MS, method: 'PUT',
      body: { share: 30, max_workers: null, admit_per_day: 64, cards_per_day: 24, usd_per_day: 80.25, paused: true } }])
    await React.act(async () => t.push(undefined, Error('unreachable')))
    assert.match(pane.container.textContent, /Lanes offline - retrying/)
    assert.match(pane.container.textContent, /7 running · 31 held · 3 you · 41 waiting/)
    assert.ok(pane.container.querySelector('.lanes-last-known'))
    assert.ok(pane.container.querySelector('button[type=submit]').disabled)
  } finally { await pane.close() }
})

const fault = { id: 'fault', name: "can't start", group: 'fault', count: 2, members: [{ id: 'unassigned', name: 'Unassigned', count: 1 }, { id: 'model_pin', name: 'Model pin', count: 1 }] }
const budget = { id: 'spend_budget', name: 'Spend budget', group: 'budget', count: 1 }
const chipIds = pane => [...pane.container.querySelectorAll('[data-lane-hold]')].map(e => e.dataset.laneHold)

test('chip priority is fault > fleet stop > lane dial > budget, never by count', async () => {
  const pane = await mount(React.createElement(P.LaneHolds, { row: { governor_held: 9, fleet_stop: true, holds: [{ ...gate, count: 99 }, budget, fault] } }))
  try {
    // Two chips painted; the hidden remainder is one "+N" badge whose tooltip names them.
    assert.deepEqual(chipIds(pane), ['fault', 'fleet_stop'])
    const wide = pane.container.querySelector('.lanes-hold-more-wide')
    assert.equal(wide.textContent, '+2'); assert.match(wide.title, /Lane gate/); assert.match(wide.title, /needs decision/)
    const narrow = pane.container.querySelector('.lanes-hold-more-narrow')
    assert.equal(narrow.textContent, '+3'); assert.equal(narrow.closest('[data-lane-hold]').dataset.laneHold, 'fault')
    assert.match(narrow.title, /fleet stop/)
  } finally { await pane.close() }
})

test('a fault chip keeps its place ahead of a bigger lane dial, with full text in tooltip and aria-label', async () => {
  const pane = await mount(React.createElement(P.LaneHolds, { row: { governor_held: 9, holds: [{ ...gate, count: 9 }, fault] } }))
  try {
    assert.deepEqual(chipIds(pane), ['fault', 'lane_gate'])
    const chip = pane.container.querySelector('[data-lane-hold="fault"]')
    assert.match(chip.textContent, /2 can't start/)
    assert.match(chip.getAttribute('aria-label'), /2 can't start.*1 Unassigned.*1 Model pin/)
    const dial = pane.container.querySelector('[data-lane-hold="lane_gate"]')
    assert.match(dial.getAttribute('aria-label'), /Lane gate 10\/50 · 64|Lane gate 10\/50 · 9/)
    assert.equal(dial.querySelector('[data-hold-count]').textContent, '9')
  } finally { await pane.close() }
})

const controlRow = { share: 20, admit_per_day: 17, holds: [], limits: {} }

test('lane dials outrank budgets regardless of producer order', async () => {
  const view = await mount(React.createElement(P.LaneHolds, { row: { holds: [budget, gate] } }))
  try { assert.deepEqual(chipIds(view), ['lane_gate', 'spend_budget']) } finally { await view.close() }
})
const typeInto = async (view, label, value) => {
  const input = view.container.querySelector(`[aria-label="${label}"]`)
  await React.act(async () => {
    Object.getOwnPropertyDescriptor(globalThis.HTMLInputElement.prototype, 'value').set.call(input, value)
    input.dispatchEvent(new globalThis.window.Event('input', { bubbles: true }))
  })
}
const submit = async view => { await React.act(async () => { view.container.querySelector('form').dispatchEvent(new globalThis.window.Event('submit', { bubbles: true, cancelable: true })) }); await view.flush() }

test('clearing admit/day writes an explicit null, and Saved shows only after the snapshot reflects it', async () => {
  const writes = []
  let setRow
  const Harness = () => { const [row, set] = React.useState(controlRow); setRow = set
    return React.createElement(P.LaneControls, { name: 'work', row, write: async (_lane, body) => { writes.push(body) } }) }
  const view = await mount(React.createElement(Harness))
  try {
    await typeInto(view, 'work admit / day', '')
    await submit(view)
    assert.deepEqual(writes, [{ admit_per_day: null }])
    assert.doesNotMatch(view.container.textContent, /Saved/, 'snapshot still shows 17')
    assert.match(view.container.textContent, /Saving…/)
    await React.act(async () => setRow({ ...controlRow, admit_per_day: null }))
    await view.flush()
    assert.match(view.container.textContent, /Saved/)
    assert.equal(view.container.querySelector('[aria-label="work admit / day"]').value, '')
  } finally { await view.close() }
})

test('multi-field saving requires every field, excludes unchanged fields, and bounds push-stream waiting', async () => {
  const writes = [], timers = new Map()
  const originalSet = globalThis.setTimeout, originalClear = globalThis.clearTimeout
  globalThis.setTimeout = (fn, ms, ...args) => {
    if (ms !== 8000) return originalSet(fn, ms, ...args)
    const id = {}; timers.set(id, fn); return id
  }
  globalThis.clearTimeout = id => { if (!timers.delete(id)) originalClear(id) }
  let setRow
  const Harness = () => { const [row, set] = React.useState(controlRow); setRow = set
    return React.createElement(P.LaneControls, { name: 'work', row, write: async (_lane, body) => writes.push(body) }) }
  const view = await mount(React.createElement(Harness))
  try {
    await typeInto(view, 'work share', '21'); await typeInto(view, 'work share', '20')
    await typeInto(view, 'work admit / day', '18')
    await typeInto(view, 'work max workers', '3')
    await submit(view)
    assert.deepEqual(writes, [{ admit_per_day: 18, max_workers: 3 }])
    assert.equal(timers.size, 1, 'one bounded write deadline')
    const [deadline] = timers.keys()
    for (let i = 0; i < 4; i++) {
      await React.act(async () => setRow({ ...controlRow, admit_per_day: 18 }))
      await view.flush()
      assert.match(view.container.textContent, /Saving…/, 'one reflected field is not Saved')
      assert.ok(timers.has(deadline), 'new snapshots cannot renew the write deadline')
    }
    await React.act(async () => timers.get(deadline)())
    await view.flush()
    assert.match(view.container.textContent, /Not saved/)
    assert.doesNotMatch(view.container.textContent, /Saved/)
    await submit(view)
    assert.deepEqual(writes[1], { max_workers: 3 }, 'retry only writes the outstanding field')
    await React.act(async () => setRow({ ...controlRow, admit_per_day: 18, limits: { max_workers: 3 } }))
    await view.flush()
    assert.match(view.container.textContent, /Saved/)
    assert.equal(timers.size, 0, 'success cancels the deadline')
  } finally {
    await view.close(); globalThis.setTimeout = originalSet; globalThis.clearTimeout = originalClear
  }
})

test('an unchanged form or a rejected write never shows Saved', async () => {
  const view = await mount(React.createElement(P.LaneControls, { name: 'work', row: controlRow, write: async () => { throw new Error('409 rejected') } }))
  try {
    await submit(view)
    assert.match(view.container.textContent, /No change/); assert.doesNotMatch(view.container.textContent, /Saved/)
    await typeInto(view, 'work admit / day', '')
    await submit(view)
    assert.match(view.container.textContent, /409 rejected/); assert.doesNotMatch(view.container.textContent, /Saved/)
  } finally { await view.close() }
})

import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount, React } from './sdk-mock.mjs'

// Sidebar sessions: s1 has cards in product (on you, running) and infra (on cards); s2 one infra card waiting for a
// worker; s3 and s4 have none; s5 is a compressed continuation whose cards sit on its lineage root s2.
const sidebar = [{ id: 's1' }, { id: 's2' }, { id: 's3' }, { id: 's4' }, { id: 's5', _lineage_root_id: 's2' }]
const sessions = { s1: { product: { you: 1, run: 1 }, infra: { dep: 1 } }, s2: { infra: { worker: 1 } } }
const snap = { configured: true, sessions, lanes: {
  product: { share: 50, strip: { blocked: 1, running: 1, other: 0 }, status: { you: 1, run: 1 } },
  infra: { share: 50, strip: { blocked: 0, running: 0, other: 3 }, status: { dep: 1, worker: 1, gate: 1 } } },
  status: { you: 1, worker: 1, run: 1, dep: 1, gate: 1 },
  gates: [{ lane: 'infra', kind: 'lane_gate', count: 1, linked: 0, unlinked: { 'ci-sweeper': 1 }, admitted_today: 0, admit_per_day: 2 }] }
const P = await loadPlugin({ SESSION_FILTER_AREA: 'sessions.filter', useSidebarSessions: () => sidebar })
const views = []
let filters = []
const ctx = {
  rest: async () => snap,
  register: (c) => { filters.push(c); return () => { filters = filters.filter((f) => f !== c) } }
}
const shown = () => sidebar.filter((s) => filters.every((f) => f.data.match(s))).map((s) => s.id)
async function setup() {
  const view = await mount(React.createElement(P.Lanes, { ctx }))
  views.push(view)
  const q = (sel) => view.container.querySelector(sel)
  return { view, q, cat: (key) => q(`[data-status="${key}"]`), lane: (name) => q(`[data-lane-pill="${name}"]`), all: () => q('[data-status-all]') }
}
test.afterEach(async () => {
  for (const view of views.splice(0)) await view.close()
  localStorage.clear()
  filters = []
})

test('sessionMatches: lane AND category, none selected = any, no-cards for sessions without cards', () => {
  const m = (id, sel) => P.sessionMatches(sidebar.find((s) => s.id === id), sessions, sel)
  assert.equal(m('s3', {}), true, 'nothing selected: no filter')
  assert.equal(m('s1', { status: ['you'] }), true)
  assert.equal(m('s2', { status: ['you'] }), false)
  assert.equal(m('s1', { lanes: ['infra'], status: ['you'] }), false, 'on you is in product, not infra')
  assert.equal(m('s1', { lanes: ['infra'] }), true)
  assert.equal(m('s5', { status: ['worker'] }), true, 'lineage root cards count')
  assert.equal(m('s3', { noCards: true }), true)
  assert.equal(m('s1', { noCards: true }), false)
  assert.equal(m('s3', { noCards: true, status: ['you'] }), true)
  assert.equal(m('s1', { noCards: true, status: ['you'] }), true)
  assert.equal(m('s2', { noCards: true, status: ['you'] }), false)
})

test('category pills multi-select and filter the session list; [all] clears everything', async () => {
  const { view, cat, lane, all } = await setup()
  assert.equal(filters.length, 0, 'no filter until something is picked')
  assert.equal(all().getAttribute('aria-pressed'), 'true')
  await view.click(cat('you'))
  assert.equal(cat('you').getAttribute('aria-pressed'), 'true')
  assert.equal(cat('run').dataset.dimmed, '1')
  assert.deepEqual(shown(), ['s1'])
  await view.click(cat('worker'))
  assert.deepEqual(shown(), ['s1', 's2', 's5'])
  await view.click(lane('product'))
  assert.deepEqual(shown(), ['s1'], 'product lane AND (on you or need worker)')
  assert.equal(filters.length, 1, 're-registered, never stacked')
  assert.match(filters[0].data.label, /product/)
  assert.equal(all().getAttribute('aria-pressed'), 'false')
  await view.click(all())
  assert.equal(filters.length, 0)
  assert.equal(lane('product').getAttribute('aria-pressed'), 'false')
  assert.equal(cat('you').getAttribute('aria-pressed'), 'false')
  assert.deepEqual(shown(), sidebar.map((s) => s.id))
})

test('no-cards pill counts sessions without open cards and filters to them', async () => {
  const { view, cat } = await setup()
  assert.equal(cat('nocards').querySelector('.hermes-lanes-n').textContent, '2')
  await view.click(cat('nocards'))
  assert.deepEqual(shown(), ['s3', 's4'])
  await view.click(cat('run'))
  assert.deepEqual(shown(), ['s1', 's3', 's4'])
  assert.deepEqual(JSON.parse(localStorage.getItem('hermes.lanes.statusSelected')), ['run'])
  assert.equal(localStorage.getItem('hermes.lanes.noCards'), '1')
})

test('the list\'s own clear-filters (onClear) resets the strip', async () => {
  const { view, cat } = await setup()
  await view.click(cat('dep'))
  await React.act(async () => filters[0].data.onClear())
  await view.flush()
  assert.equal(filters.length, 0)
  assert.equal(cat('dep').getAttribute('aria-pressed'), 'false')
})

test('tooltips name the colour and the count', async () => {
  const { cat, lane, all } = await setup()
  assert.match(cat('you').title, /^Red: blocked on you, 1 card\./)
  assert.match(cat('worker').title, /^Green: waiting for a worker/)
  assert.match(cat('gate').title, /^Purple: held at a gate, 1 card — infra lane gate: 1 \(0\/2 admitted today; 1 not linked to a chat: 1 ci-sweeper\)/)
  assert.equal(cat('gate').querySelector('.hermes-lanes-status-word').textContent, 'held at infra lane gate')
  assert.match(cat('run').title, /^Blue: running/)
  assert.match(cat('dep').title, /^Grey: waiting on other cards/)
  assert.match(cat('nocards').title, /^Dashed: sessions with no open cards, 2 sessions/)
  assert.match(lane('product').title, /1 blocked on you\n1 running\n0 waiting/)
  assert.match(all().title, /clear every lane and category selection/)
})

test('older Desktop without the filter hook: pills still toggle, nothing registers, no no-cards pill', async () => {
  const Old = await loadPlugin({ X_OLD: 1 })
  const view = await mount(React.createElement(Old.Lanes, { ctx }))
  views.push(view)
  const you = view.container.querySelector('[data-status="you"]')
  await view.click(you)
  assert.equal(you.getAttribute('aria-pressed'), 'true')
  assert.equal(filters.length, 0)
  assert.equal(view.container.querySelector('[data-status="nocards"]'), null)
})

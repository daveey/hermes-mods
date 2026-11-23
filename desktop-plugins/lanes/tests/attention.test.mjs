import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount, React } from './sdk-mock.mjs'

const P = await loadPlugin({
  SidebarSection: ({ label, count, children }) => React.createElement('section', null,
    React.createElement('h3', null, `${label} · ${count}`), children),
  SidebarSectionRow: ({ label, accentColor, onSelect }) => React.createElement('button',
    { style: { color: accentColor }, onClick: onSelect }, label)
})

test('unlinked red rows share pushed snapshot, exclude linked asks and clear after resolution', async () => {
  const ctx = { rest() { throw Error('sidebar must never start a poller') } }
  const store = P.attentionStore(ctx)
  P.sourceFor(ctx).subscribe = () => Object.assign(() => {}, { update() {}, refresh() {} })
  const rows = [
    { id: 'headless', key: 'default/headless', title: 'Choose route', lane: 'work', unlinked: true },
    { id: 'linked', key: 'default/linked', title: 'Linked ask', lane: 'work', unlinked: false }
  ]
  store.publish({ attention: { available: true, rows }, status: { you: 2 } })
  const view = await mount(React.createElement(P.UnlinkedAsks, { ctx }))
  try {
    assert.equal(view.container.querySelector('h3').textContent, 'Unlinked asks · 1')
    assert.equal(view.container.querySelectorAll('button').length, 1)
    assert.equal(view.container.querySelector('button').textContent, 'Choose route')
    assert.equal(view.container.querySelector('button').style.color, 'rgb(239, 68, 68)')
    await React.act(async () => store.publish({ attention: { available: true, rows: [] }, status: { you: 0 } }))
    assert.ok(view.container.querySelector('[data-unlinked-asks]') === null)
  } finally { await view.close() }
})

test('same snapshot covers linked scheduled and hidden-chat asks exactly once', () => {
  const rows = ['visible', 'scheduled', 'hidden', 'headless'].map(id => ({ id, unlinked: id === 'headless' }))
  const view = { attention: { available: true, rows }, status: { you: 4 } }
  const sessions = [{ needs_you: [{ kind: 'card', ref: 'default/visible' }] },
    { id: 'legacy-device', kanban_blocked: 0 }]
  const missing = P.uncoveredAsks(view, sessions)
  assert.deepEqual(missing.map(row => row.id), ['scheduled', 'hidden', 'headless'])
  const covered = rows.filter(row => !missing.includes(row))
  assert.equal(new Set([...covered, ...missing].map(row => row.id)).size, view.status.you)
  assert.equal(covered.length + missing.length, view.status.you)
})

test('qualified board identity never suppresses a different board ask', () => {
  const view = { attention: { available: true, rows: [{ id: 't_ask', target: { board: 'default' } }] } }
  assert.equal(P.uncoveredAsks(view, [{ needs_you: [{ kind: 'card', ref: 'other/t_ask' }] }]).length, 1)
  assert.equal(P.uncoveredAsks(view, [{ needs_you: [{ kind: 'card', ref: 'default/t_ask' }] }]).length, 0)
})

test('mounted lane filter preserves hidden-chat asks and clearing restores linked coverage without feedback', async () => {
  const sessions = [{ id: 'chat', needs_you: [{ kind: 'card', ref: 'default/t_ask' }] }]
  const Q = await loadPlugin({ SESSION_FILTER_AREA: 'sessions.filter', useSidebarSessions: () => sessions,
    SidebarSection: ({ children }) => React.createElement('section', null, children),
    SidebarSectionRow: ({ label }) => React.createElement('span', { 'data-ask': '1' }, label) })
  const snap = { configured: true, sessions: { chat: { alpha: { you: 1 } } },
    lanes: { alpha: { share: 50, strip: { blocked: 1, running: 0, other: 0 }, status: { you: 1 } },
      beta: { share: 50, strip: { blocked: 0, running: 1, other: 0 }, status: { run: 1 } } },
    status: { you: 1, run: 1 }, attention: { available: true, rows: [
      { id: 't_ask', key: ':default:default:t_ask', title: 'Hidden ask', unlinked: false }] } }
  let registrations = 0
  const filters = new Set()
  const ctx = { rest: async () => snap, register(c) {
    registrations++; filters.add(c); return () => filters.delete(c)
  } }
  const store = Q.attentionStore(ctx)
  let push
  Q.sourceFor(ctx).subscribe = (_params, update) => {
    push = update
    update(snap)
    return Object.assign(() => {}, { update() {}, refresh() {} })
  }
  localStorage.clear()
  const pane = await mount(React.createElement(Q.UnlinkedAsks, { ctx }))
  const strip = await mount(React.createElement(Q.Lanes, { ctx }))
  await React.act(async () => push(snap))
  try {
    const fallback = () => pane.container.querySelectorAll('[data-ask]').length
    const visible = () => sessions.filter(s => [...filters].every(f => f.data.match(s))).length
    assert.equal(fallback(), 0)
    assert.equal(visible(), 1)
    await strip.click(strip.container.querySelector('[data-lane-pill="beta"]'))
    assert.equal(visible(), 0)
    assert.equal(fallback(), 1, 'the pre-plugin SDK chat must not hide its fallback')
    assert.equal(visible() + fallback(), snap.status.you)
    const count = registrations
    await React.act(async () => store.publish({ ...snap }))
    await pane.flush()
    assert.equal(registrations, count, 'attention publication does not re-register the filter')
    await strip.click(strip.container.querySelector('[data-status-all]'))
    assert.equal(visible(), 1)
    assert.equal(fallback(), 0)
    assert.equal(visible() + fallback(), snap.status.you)
    await strip.click(strip.container.querySelector('[data-status="run"]'))
    assert.equal(visible(), 0)
    assert.equal(fallback(), 1, 'category filters also preserve the ask')
    await strip.close()
    assert.equal(fallback(), 0, 'unmount clears only the lanes visibility filter')
  } finally { if (strip.container.isConnected) await strip.close(); await pane.close(); localStorage.clear() }
})

test('sidebar owns the shared subscription without a strip; joining never starts a second reader', async () => {
  const ctx = {}
  let starts = 0, stops = 0, updates = 0
  P.sourceFor(ctx).subscribe = () => {
    starts++
    return Object.assign(() => { stops++ }, { update() { updates++ }, refresh() {} })
  }
  const sidebar = await mount(React.createElement(P.UnlinkedAsks, { ctx }))
  assert.equal(starts, 1)
  const strip = P.attentionStore(ctx).connect({ expanded: true }, () => {}, () => {}, true)
  assert.equal(starts, 1)
  assert.equal(updates, 1)
  strip()
  assert.equal(stops, 0)
  await sidebar.close()
  assert.equal(stops, 1)
})

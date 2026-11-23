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

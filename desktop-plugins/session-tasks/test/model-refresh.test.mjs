import { test } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'

register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,export const COMPOSER_AREAS={top:"composer.top"};export const host={};export const useValue=()=>null', shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useEffect=()=>{};export const useState=v=>[v,()=>{}];export const jsx=()=>null;export const jsxs=()=>null', shortCircuit: true }
  return next(spec, ctx)
}`))
const m = await import('../plugin.js')
const route = { connectionId: 'conn', profile: 'default' }
const task = { id: 't_model', gateway: 'hub', profile: 'default', board: 'board-a', status: 'blocked', title: 'Raw card title' }
const row = { ...task, kind: 'card', title: 'Pick the safe option', detail: 'Human-readable detail' }
const model = r => ({ version: 1, sections: { needs_you: [r] } })
const display = { title: row.title, detail: row.detail }
const scope = session => JSON.stringify(['conn', 'default', session])
function apiFor(session, tasks) {
  globalThis.window = { hermesDesktop: { api: async ({ path }) => {
    if (path.startsWith('/api/sessions/')) return { id: session, parent_session_id: null }
    if (path.startsWith('/api/session-kanban-tasks')) return { tasks: tasks() }
    return null
  } } }
}
function seed(session, tasks = [task], suppliedModel = model(row)) {
  m.ingestFeed({ ...route, session, lineage: [session], tasks, model: suppliedModel })
}

test('model matching requires all owner fields on both sides, never a wildcard', () => {
  const chip = m.sessionTaskChips([task], route)[0]
  assert.equal(m.modelRowFor(model(row), chip), row)
  for (const field of ['gateway', 'profile', 'board']) {
    for (const missing of [undefined, null, '']) {
      assert.equal(m.modelRowFor(model({ ...row, [field]: missing }), chip), null, `row ${field}=${missing}`)
      assert.equal(m.modelRowFor(model(row), { ...chip, [field]: missing }), null, `chip ${field}=${missing}`)
    }
    assert.equal(m.modelRowFor(model({ ...row, [field]: 'other' }), chip), null, field)
  }
  const unknownBoard = { ...row }; delete unknownBoard.board
  assert.equal(m.modelRowFor({ version: 1, sections: { needs_you: [unknownBoard, { ...row, board: 'board-b' }], stuck: [row] } }, chip), row)
})

test('polling and warming preserve model display through repeated identical authoritative reads', async () => {
  const session = 'model-refresh'
  apiFor(session, () => [task])
  seed(session)
  for (let i = 0; i < 2; i++) {
    const chips = await m.loadKanban(new Set([session]), route)
    assert.deepEqual(chips[0].display, display)
    assert.equal(chips[0].label, task.title, 'raw evidence stays intact')
  }
  assert.equal(await m.warm({ ...route, session }), true)
  assert.deepEqual(m.cachedChips(scope(session)).kanban[0].display, display)
})

test('changed evidence invalidates old display and absence cannot resurrect it', async () => {
  const session = 'model-invalidation'
  let tasks = [task]
  apiFor(session, () => tasks)
  seed(session)
  tasks = [{ ...task, status: 'ready' }]
  assert.equal((await m.loadKanban(new Set([session]), route))[0].display, undefined)
  tasks = [task]
  assert.equal((await m.loadKanban(new Set([session]), route))[0].display, undefined)
  seed(session)
  tasks = []
  assert.deepEqual(await m.loadKanban(new Set([session]), route), [])
  tasks = [task]
  assert.equal((await m.loadKanban(new Set([session]), route))[0].display, undefined)
  for (const change of [{ title: 'Changed ask' }, { human_request: { action: 'Approve this instead' } }, { board: 'board-b' }, { gateway: 'elsewhere' }, { profile: 'work' }]) {
    seed(session)
    tasks = [{ ...task, ...change }]
    assert.equal((await m.loadKanban(new Set([session]), route))[0].display, undefined, JSON.stringify(change))
  }
})

test('a new feed model replaces display; a model-less bundle clears it', async () => {
  const session = 'model-replacement'
  apiFor(session, () => [task])
  seed(session)
  seed(session, [task], model({ ...row, title: 'New model title' }))
  assert.equal((await m.loadKanban(new Set([session]), route))[0].display.title, 'New model title')
  seed(session, [task], null)
  assert.equal((await m.loadKanban(new Set([session]), route))[0].display, undefined)
})

test('persisted per-chat display survives a cold read but never crosses route or lineage', async () => {
  const session = 'model-cold'
  apiFor(session, () => [task])
  m.rememberChips(scope(session), m.withModelDisplay(m.sessionTaskChips([task], route), model(row)), [])
  assert.deepEqual((await m.loadKanban(new Set([session]), route))[0].display, display)
  assert.equal((await m.loadKanban(new Set([session]), { ...route, connectionId: 'another' }))[0].display, undefined)
  assert.equal((await m.loadKanban(new Set([session]), { ...route, profile: 'work' }))[0].display, undefined)
  assert.equal((await m.loadKanban(new Set(['unrelated']), route))[0].display, undefined)
})

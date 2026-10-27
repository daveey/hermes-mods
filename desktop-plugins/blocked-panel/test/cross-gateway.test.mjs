import { test } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'
register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,export const COMPOSER_AREAS={top:"composer.top"};export const host={activeConnectionId:()=>"local",connections:async()=>[{id:"local"},{id:"remote"}],state:{focusedStoredSessionId:{get:()=>"s_origin"},focusedSessionOwner:{get:()=>({connectionId:"remote",profile:"ops"})}}};export const connectionForHubGateway=n=>n==="other"?"remote":undefined;export const useValue=()=>null', shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useEffect=()=>{};export const useState=v=>[v,()=>{}];export const jsx=()=>null;export const jsxs=()=>null', shortCircuit: true }
  return next(spec, ctx)
}`))
const m = await import('../plugin.js')
const evidence = { action: 'Choose a window.', why_human: 'Owner scheduling decision.', interaction: 'reply', scope: 'Reviewed deployment.', evidence: 'No window selected.' }
const task = { id: 't_same', board: 'ops', status: 'blocked', assignee: 'operator' }

test('live list, replica discovery, details, snapshot and actions preserve owner end to end', async () => {
  const calls = []
  let closeRemote = false
  globalThis.document = { hidden: false, addEventListener() {}, removeEventListener() {} }
  globalThis.window = { localStorage: { getItem: () => null }, hermesDesktop: { api: async req => {
    calls.push(req)
    if (req.method) return { ok: true }
    if (req.path.startsWith('/api/sessions/')) return { id: 's_origin' }
    if (req.path.startsWith('/api/session-kanban-tasks')) return { tasks: req.connectionId === 'local' ? [task] : [] }
    if (req.path.startsWith('/api/plugins/hub-sync/session-links/')) return { tasks: [
      { ...task, gateway: 'other', local: false },
      { ...task, id: 't_unknown', gateway: 'unregistered', local: false }
    ] }
    if (req.path.startsWith('/api/plugins/kanban/tasks/')) return {
      task: { ...task, status: closeRemote && req.connectionId === 'remote' ? 'ready' : 'blocked' },
      events: [{ id: 1, kind: 'blocked', payload: { kind: 'needs_input', human_request: evidence } }]
    }
    if (req.path.endsWith('/ping')) return { ok: false }
    throw new Error(`unexpected ${req.path}`)
  } } }
  const dispose = m.default.register({ register() {} })
  try {
    assert.equal(window.__blockedPanel.unblockCard, undefined, 'shared adapter must not expose generic approval bypass')
    await m.refresh(true)
    const { items, issues } = window.__blockedPanel.snapshot
    const lineage = calls.find(c => c.path.startsWith('/api/sessions/'))
    assert.equal(lineage.connectionId, 'remote')
    assert.equal(lineage.path, '/api/sessions/s_origin?profile=ops')
    assert.equal(items.length, 2) // identical board/id on two gateways must not collapse
    assert.deepEqual(new Set(items.map(i => i.connectionId)), new Set(['local', 'remote']))
    assert.equal(issues.length, 1)
    assert.equal(issues[0].gateway, 'unregistered')
    assert.equal(issues[0].owner, 'operator')
    const detailCalls = calls.filter(c => c.path.startsWith('/api/plugins/kanban/tasks/'))
    assert.deepEqual(new Set(detailCalls.map(c => c.connectionId)), new Set(['local', 'remote']))
    const remote = items.find(i => i.connectionId === 'remote')
    await m.unblockCard(remote, 'Use the reviewed window.')
    const writes = calls.filter(c => c.method)
    assert.deepEqual(writes.map(c => [c.method, c.connectionId]), [['POST', 'remote'], ['PATCH', 'remote']])
    assert.deepEqual(writes[1].body, { status: 'ready' })
    let opened
    assert.equal(m.openCard(remote, { __hermesKanbanOpenTask: (...args) => { opened = args; return true } }), 'drawer')
    assert.deepEqual(opened, ['t_same', 'ops', 'remote'])
    assert.notEqual(m.detailKey(items[0]), m.detailKey(items[1]))
    assert.notEqual(m.rewriteKey(items[0]), m.rewriteKey(items[1]))
    closeRemote = true
    await m.refresh(true)
    assert.equal(window.__blockedPanel.snapshot.items.length, 1) // stale replica cannot resurrect ready card
  } finally { dispose(); delete globalThis.window; delete globalThis.document }
})

test('unknown owner cannot fall through to active gateway writes or legacy popup', async () => {
  const unknown = { ...task, gateway: 'unregistered' }
  await assert.rejects(m.unblockCard(unknown, 'answer', () => assert.fail('wrong gateway write')), /owning gateway/)
  assert.equal(m.openCard(unknown, { __taskMentionsOpen: () => assert.fail('wrong popup') }), 'unavailable')
  assert.equal(m.openCard({ ...task, connectionId: 'remote' }, { __taskMentionsOpen: () => assert.fail('wrong popup') }), 'unavailable')
})

test('canonical owner evidence wins over historical events equally on each gateway', () => {
  const tasks = ['local', 'remote'].map(connectionId => ({ ...task, connectionId }))
  const details = new Map(tasks.map(t => [m.detailKey(t), {
    task: { ...t, block_kind: 'needs_input', human_request: null },
    events: [{ id: 1, kind: 'blocked', payload: { human_request: evidence } }]
  }]))
  assert.equal(m.blockedItems(tasks, details).length, 0)
  assert.equal(m.agentIssues(tasks, details).length, 2)
  for (const d of details.values()) { d.task.human_request = evidence; d.events = [] }
  assert.equal(m.blockedItems(tasks, details).length, 2)
  assert.equal(m.agentIssues(tasks, details).length, 0)
  for (const d of details.values()) d.task.human_request = { ...evidence, evidence: '' }
  assert.equal(m.blockedItems(tasks, details).length, 0)
  assert.equal(m.agentIssues(tasks, details).length, 2)
})

test('live list wins over replica and partial gateway failure leaves unknown visible', async () => {
  const rows = await m.sessionTasks(new Set(['s_origin']), async (path, opts) => {
    if (path.includes('hub-sync')) return { tasks: [{ ...task, gateway: 'other' }] }
    if (opts.connectionId === 'remote') return { tasks: [{ ...task, status: 'ready' }] }
    throw new Error('offline')
  })
  assert.equal(rows.length, 1)
  assert.equal(rows[0].status, 'ready')
  assert.equal(rows[0].connectionId, 'remote')
  assert.equal(await m.sessionTasks(['s_origin'], async () => { throw new Error('offline') }), null)
})

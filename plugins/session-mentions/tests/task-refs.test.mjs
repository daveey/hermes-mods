// @task refs across gateways: ref format/parse, typeahead rows, and the courier's task_* actions
// (what the agent's cross_task tool gets back).
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { test } from 'node:test'
const source = await fs.readFile(new URL('../../../desktop-plugins/session-mentions/plugin.js', import.meta.url), 'utf8')
Error.stackTraceLimit = 1
async function load(host, api) {
  globalThis.__smHost = host
  globalThis.window = { hermesDesktop: { api } }
  const code = source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'", 'const host = globalThis.__smHost; const COMPOSER_AREAS = {}')
  return import('data:text/javascript;base64,' + Buffer.from(code + '\n//' + Math.random()).toString('base64'))
}
const conns = [
  { id: 'local', label: 'This device', key: 'this-device', kind: 'local', primary: true },
  { id: 'skint-polycentrism', label: 'nous-1', key: 'nous-1', kind: 'cloud', primary: false },
  { id: 'mac-mini', label: 'mac-mini', key: 'mac-mini', kind: 'ssh', primary: false }
]
const state = { connectionId: { get: () => 'local' }, profile: { get: () => 'default' } }

// Fake kanban REST per gateway.
const boards = {
  local: { default: [{ id: 't_aaaa1111', title: 'Fix sidebar badge', status: 'running', assignee: 'default', created_at: 100 }] },
  'mac-mini': {
    default: [],
    bb: [
      { id: 't_bbbb2222', title: 'Wake word retrain', status: 'blocked', assignee: 'bb-dev', created_at: 200 },
      { id: 't_cccc3333', title: 'Old done thing', status: 'done', created_at: 50, completed_at: 60 }
    ]
  },
  'skint-polycentrism': { default: [] }
}
function fakeApi(calls = []) {
  return async req => {
    calls.push(req)
    const url = new URL(req.path, 'http://x')
    const gw = boards[req.connectionId] || {}
    if (url.pathname === '/api/plugins/kanban/boards') return { boards: Object.entries(gw).map(([slug, ts]) => ({ slug, total: ts.length })) }
    if (url.pathname === '/api/plugins/kanban/board') {
      const ts = gw[url.searchParams.get('board') || 'default'] || []
      const cols = {}
      for (const t of ts) (cols[t.status] ||= []).push(t)
      return { columns: Object.entries(cols).map(([name, tasks]) => ({ name, tasks })) }
    }
    const m = /^\/api\/plugins\/kanban\/tasks\/([^/]+)(\/comments)?$/.exec(url.pathname)
    if (m) {
      const board = url.searchParams.get('board') || 'default'
      const t = (gw[board] || []).find(x => x.id === m[1])
      if (!t) throw new Error(`404 Not Found: task ${m[1]}`)
      if (m[2]) return { ok: true }
      return { task: { ...t, body: 'the body' }, comments: [{ author: 'x', body: 'hi', created_at: 300 }], events: [], runs: [], links: { parents: [], children: [] } }
    }
    if (url.pathname.includes('/outbox')) return { requests: [] }
    return { ok: true }
  }
}

test('taskRef qualifies only foreign gateways; parseTaskRef round-trips every form', async () => {
  const m = await load({ state, connections: async () => conns }, fakeApi())
  assert.equal(m.taskRef({ connectionId: 'local', board: 'default', taskId: 't_aaaa1111' }, 'local', conns), '@task:default/t_aaaa1111')
  const remote = m.taskRef({ connectionId: 'mac-mini', board: 'bb', taskId: 't_bbbb2222' }, 'local', conns)
  assert.equal(remote, '@task:bb@mac-mini/t_bbbb2222')
  assert.deepEqual(m.parseTaskRef(remote, 'local', conns), { connectionId: 'mac-mini', board: 'bb', taskId: 't_bbbb2222', anyGateway: false })
  assert.deepEqual(m.parseTaskRef('`@task:default/t_aaaa1111`', 'local', conns), { connectionId: 'local', board: 'default', taskId: 't_aaaa1111', anyGateway: false })
  assert.deepEqual(m.parseTaskRef('nous-1:t_dddd4444', 'local', conns), { connectionId: 'skint-polycentrism', board: undefined, taskId: 't_dddd4444', anyGateway: false })
  assert.equal(m.parseTaskRef('t_aaaa1111', 'local', conns).anyGateway, true)
  assert.match(m.parseTaskRef('@task:bb@nowhere/t_aaaa1111', 'local', conns).error, /unknown gateway "nowhere"/)
  assert.match(m.parseTaskRef('@task:default/hello', 'local', conns).error, /not a kanban task id/)
})

test('typeahead: @task: lists live tasks from every gateway first; plain words match titles; t_ matches ids', async () => {
  const m = await load({ state, connections: async () => conns }, fakeApi())
  const rows = [
    ...m.slimTasks('default', [{ name: 'running', tasks: boards.local.default }], conns[0]),
    ...m.slimTasks('bb', [{ name: 'x', tasks: boards['mac-mini'].bb }], conns[2])
  ]
  const now = 1000 * 1000
  assert.equal(m.taskItems(rows, 'ta', { active: 'local', conns, now })[0].insert, '@task:')
  const scoped = m.taskItems(rows, 'task:', { active: 'local', conns, now })
  assert.deepEqual(scoped.map(i => i.insert), ['@task:bb@mac-mini/t_bbbb2222', '@task:default/t_aaaa1111', '@task:bb@mac-mini/t_cccc3333'])
  assert.match(scoped[0].meta, /Task · blocked · mac-mini · board bb · @bb-dev/)
  assert.deepEqual(m.taskItems(rows, 'wake', { active: 'local', conns, now }).map(i => i.insert), ['@task:bb@mac-mini/t_bbbb2222'])
  assert.deepEqual(m.taskItems(rows, 't_aaa', { active: 'local', conns, now }).map(i => i.insert), ['@task:default/t_aaaa1111'])
  // relative to the mini, its own tasks are unqualified and local ones get `@this-device`
  assert.equal(m.taskItems(rows, 'sidebar', { active: 'mac-mini', conns, now })[0].insert, '@task:default@this-device/t_aaaa1111')
  assert.deepEqual(m.taskItems(rows, 'zz', { active: 'local', conns, now }), [])
})

test('courier task_read resolves a qualified ref on its gateway, a bare id on any gateway', async () => {
  const calls = []
  const m = await load({ state, connections: async () => conns }, fakeApi(calls))
  const r1 = await m.performRequest({ action: 'task_read', args: { target: '@task:bb@mac-mini/t_bbbb2222' } }, 'local', conns)
  assert.equal(r1.ok, true)
  assert.equal(r1.gateway, 'mac-mini')
  assert.equal(r1.task.title, 'Wake word retrain')
  assert.equal(r1.task.comments[0].body, 'hi')
  assert.ok(calls.filter(c => c.path.startsWith('/api/plugins/kanban/tasks/')).every(c => c.connectionId === 'mac-mini'))

  const r2 = await m.performRequest({ action: 'task_read', args: { target: 't_bbbb2222' } }, 'local', conns)
  assert.equal(r2.ok, true)
  assert.equal(r2.ref, '@task:bb@mac-mini/t_bbbb2222')

  const r3 = await m.performRequest({ action: 'task_read', args: { target: '@task:default/t_bbbb2222' } }, 'local', conns)
  assert.equal(r3.ok, false)
  assert.match(r3.error, /not found on this-device/)
})

test('courier task_comment posts to the task gateway as the sender; task_list filters by status', async () => {
  const calls = []
  const m = await load({ state, connections: async () => conns }, fakeApi(calls))
  const res = await m.performRequest({ action: 'task_comment', args: { target: '@task:bb@mac-mini/t_bbbb2222', message: 'ping' }, sender: { title: 'Planner' } }, 'local', conns)
  assert.equal(res.ok, true)
  const post = calls.find(c => c.method === 'POST')
  assert.equal(post.connectionId, 'mac-mini')
  assert.equal(post.path, '/api/plugins/kanban/tasks/t_bbbb2222/comments?board=bb')
  assert.deepEqual(post.body, { body: 'ping', author: 'Planner' })

  const list = await m.performRequest({ action: 'task_list', args: { status: 'running,blocked' } }, 'local', conns)
  assert.deepEqual(list.tasks.map(t => t.ref).sort(), ['@task:bb@mac-mini/t_bbbb2222', '@task:default/t_aaaa1111'])
})

test('courier task_read: a gateway that errors (503 draining) is reported unreachable+retryable, never "not found"', async () => {
  const base = fakeApi()
  const draining = new Set(['mac-mini'])
  const api = async req => {
    if (draining.has(req.connectionId) && req.path.startsWith('/api/plugins/kanban/')) {
      throw new Error('503: {"error":"gateway_not_ready","state":"ready"}')
    }
    return base(req)
  }
  const m = await load({ state, connections: async () => conns }, api)
  const r1 = await m.performRequest({ action: 'task_read', args: { target: '@task:bb@mac-mini/t_bbbb2222' } }, 'local', conns)
  assert.equal(r1.ok, false)
  assert.equal(r1.retryable, true)
  assert.doesNotMatch(r1.error, /not found/)
  assert.match(r1.error, /could not read task t_bbbb2222: lookup failed on mac-mini: 503/)

  // bare id: other gateways answered 404, the draining one errored -> still unknown, not "not found anywhere"
  const r2 = await m.performRequest({ action: 'task_read', args: { target: 't_bbbb2222' } }, 'local', conns)
  assert.equal(r2.ok, false)
  assert.equal(r2.retryable, true)
  assert.doesNotMatch(r2.error, /not found on any connected gateway/)

  // once the gateway is back the same ref reads fine; a real miss on healthy gateways stays "not found"
  draining.clear()
  const r3 = await m.performRequest({ action: 'task_read', args: { target: '@task:bb@mac-mini/t_bbbb2222' } }, 'local', conns)
  assert.equal(r3.ok, true)
  const r4 = await m.performRequest({ action: 'task_read', args: { target: 't_zzzz9999' } }, 'local', conns)
  assert.equal(r4.ok, false)
  assert.equal(r4.retryable, undefined)
  assert.match(r4.error, /not found on any connected gateway/)
})

test('courier task_read: a failed board listing after a default-board miss is unknown, not "not found"', async () => {
  const base = fakeApi()
  const api = async req => {
    if (req.connectionId === 'mac-mini' && req.path === '/api/plugins/kanban/boards') throw new Error('timeout after 15000ms')
    return base(req)
  }
  const m = await load({ state, connections: async () => conns }, api)
  const r = await m.performRequest({ action: 'task_read', args: { target: 'mac-mini:t_bbbb2222' } }, 'local', conns)
  assert.equal(r.ok, false)
  assert.equal(r.retryable, true)
  assert.match(r.error, /board list unavailable/)
})

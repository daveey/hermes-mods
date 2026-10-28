// Links Hermes writes for cards forwarded across gateways name the target by its hermes-hub
// registry name (`@task:default@hub/t_..`, from `self_gateway()`), while this Desktop knows that
// gateway by its connection label (`hub-ec2`). Each gateway reports its registry name on the
// session-mentions /ping; the courier resolves it and drains with it in `reach`.
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
const state = { connectionId: { get: () => 'local' }, profile: { get: () => 'default' } }
const raw = [
  { id: 'local', label: 'This device', kind: 'local', primary: true },
  { id: 'hub-ec2', label: 'hub-ec2', kind: 'ssh' },
  { id: 'mac-mini', label: 'mac-mini', kind: 'ssh' }
]
const TASK = 't_01a182b1a147aa45'
const SID = '20261028_072000_abcdef'

/** Fake backends: the MacBook (local), the hub (registry name `hub`), the mini (older plugin: no name). */
function fakeApi({ outbox = [], results = [], paths = [] } = {}) {
  let served = false
  return async req => {
    paths.push([req.connectionId, req.path])
    const url = new URL(req.path, 'http://x')
    if (url.pathname.endsWith('/session-mentions/ping')) {
      if (req.connectionId === 'local') return { ok: true, device: 'macbook', aliases: ['macbook', 'david-mbp'], gateway: 'macbook' }
      if (req.connectionId === 'hub-ec2') return { ok: true, device: 'ip-172-31-0-1', aliases: [], gateway: 'hub' }
      return { ok: true, home_courier: false, forwarding: true }
    }
    if (url.pathname.endsWith('/session-mentions/outbox')) {
      if (req.connectionId !== 'local' || served) return { requests: [] }
      served = true
      return { requests: outbox }
    }
    if (url.pathname.endsWith('/session-mentions/result')) {
      if (req.body?.id !== 'home-courier') results.push(req.body)
      return { ok: true }
    }
    if (url.pathname === `/api/plugins/kanban/tasks/${TASK}` && req.connectionId === 'hub-ec2') {
      return { task: { id: TASK, title: 'Forwarded card', status: 'done', body: 'b' }, comments: [], events: [], runs: [], links: { parents: [], children: [] } }
    }
    if (url.pathname.startsWith('/api/plugins/kanban/tasks/')) throw new Error(`404 Not Found: task on ${req.connectionId}`)
    if (url.pathname === `/api/sessions/${SID}`) return { title: `chat on ${req.connectionId}` }
    if (url.pathname === `/api/sessions/${SID}/messages`) return { session_id: SID, messages: [{ id: 1, role: 'user', content: `hi from ${req.connectionId}` }] }
    return { ok: true }
  }
}

async function until(fn, ms = 3000) {
  const end = Date.now() + ms
  while (!fn()) {
    if (Date.now() > end) throw new Error('timed out')
    await new Promise(r => setTimeout(r, 10))
  }
}

test('a remote connection reporting registry name `hub` resolves @task and @session refs naming it', async () => {
  const m = await load({ state }, fakeApi())
  const conns = m.gatewayKeys(raw, { device: 'macbook', aliases: ['macbook', 'david-mbp'] }, { local: 'macbook', 'hub-ec2': 'hub' })
  assert.equal(m.findGateway(conns, 'hub')?.id, 'hub-ec2')
  assert.deepEqual(m.parseTaskRef(`@task:default@hub/${TASK}`, 'local', conns), { connectionId: 'hub-ec2', board: 'default', taskId: TASK, anyGateway: false })
  assert.deepEqual(m.parseRef(`@session:default@hub/${SID}`, 'local', conns), { connectionId: 'hub-ec2', profile: 'default', sessionId: SID })
  // Existing names keep working: the connection label and this Mac's device / hostname alias.
  assert.equal(m.parseTaskRef(`@task:default@hub-ec2/${TASK}`, 'local', conns).connectionId, 'hub-ec2')
  assert.equal(m.parseRef(`@session:default@macbook/${SID}`, 'hub-ec2', conns).connectionId, 'local')
  assert.equal(m.parseRef(`@session:default@david-mbp/${SID}`, 'hub-ec2', conns).connectionId, 'local')
  // Refs this Desktop mints still use the connection key; links are not rewritten.
  assert.equal(m.taskRef({ connectionId: 'hub-ec2', board: 'default', taskId: TASK }, 'local', conns), `@task:default@hub-ec2/${TASK}`)
  // The drain reach carries the registry name, so the gateway hands this courier `route=hub`.
  const reach = new URLSearchParams(m.outboxPath(conns[0], conns).split('?')[1]).get('reach').split(',')
  for (const n of ['hub', 'hub-ec2', 'macbook', 'mac-mini']) assert.ok(reach.includes(n), n)
})

test('a registry name never steals a name another connection is labelled with', async () => {
  const m = await load({ state }, fakeApi())
  // The hub reports `mac-mini` (misconfigured): the real mac-mini connection keeps the name.
  const conns = m.gatewayKeys(raw, { device: 'macbook', aliases: ['macbook'] }, { 'hub-ec2': 'mac-mini' })
  assert.equal(m.findGateway(conns, 'mac-mini')?.id, 'mac-mini')
})

test('courier tick: learns each gateway registry name from its /ping and serves refs naming it', async () => {
  const results = []
  const paths = []
  const outbox = [
    { id: 'r1', action: 'task_read', args: { target: `@task:default@hub/${TASK}` } },
    { id: 'r2', action: 'read', args: { target: `@session:default@hub/${SID}` } },
    { id: 'r3', action: 'task_read', args: { target: `@task:default@hub-ec2/${TASK}` } },
    { id: 'r4', action: 'read', args: { target: `@session:default@macbook/${SID}` } }
  ]
  const m = await load({ state, connections: async () => raw }, fakeApi({ outbox, results, paths }))
  await m.courierTick()
  await until(() => results.length === 4)
  const by = Object.fromEntries(results.map(r => [r.id, r.result]))
  assert.equal(by.r1.ok, true, by.r1.error)
  assert.equal(by.r1.task.title, 'Forwarded card')
  assert.equal(by.r2.ok, true, by.r2.error)
  assert.equal(by.r2.messages[0].content, 'hi from hub-ec2')
  assert.equal(by.r3.ok, true, by.r3.error)
  assert.equal(by.r4.ok, true, by.r4.error)
  assert.equal(by.r4.messages[0].content, 'hi from local')
  const drain = paths.find(([c, p]) => c === 'local' && p.includes('/outbox?'))[1]
  const reach = new URLSearchParams(drain.split('?')[1]).get('reach').split(',')
  assert.ok(reach.includes('hub') && reach.includes('hub-ec2'), reach.join(','))
})

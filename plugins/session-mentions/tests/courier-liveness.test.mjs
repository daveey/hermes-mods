// Regression (10-30, hub chat 20261030_201549_7bdc2d): run_in_user_terminal from hermes-hub to the
// MacBook failed twice in a row while its Desktop was up:
//   1. "no Hermes Desktop connected to this gateway": one hub outbox GET timed out on the loaded Mac,
//      the courier backed off 30 s, and the hub calls a courier gone after 20 s without a drain.
//   2. "could not reach the user-terminal plugin on macbook: ... Gateway ticket bootstrap failed":
//      the Mac's own gateway control socket stalled for a few seconds, and the courier made one try.
// These pin the fixes: drain backoff stays under the hub's liveness window, a stalled backend can't
// hold back other gateways' drains, ticket-phase (pre-send) failures are retried, and the result
// POST back to the gateway is retried.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { test } from 'node:test'
const pluginUrl = process.env.SM_PLUGIN_JS ? new URL(`file://${process.env.SM_PLUGIN_JS}`) : new URL('../../../desktop-plugins/session-mentions/plugin.js', import.meta.url)
const source = await fs.readFile(pluginUrl, 'utf8')
const utSource = await fs.readFile(new URL('../../user-terminal/__init__.py', import.meta.url), 'utf8')
Error.stackTraceLimit = 1
async function load(host, api) {
  globalThis.__smHost = host
  globalThis.window = { hermesDesktop: { api } }
  const code = source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'", 'const host = globalThis.__smHost; const COMPOSER_AREAS = {}')
  return import('data:text/javascript;base64,' + Buffer.from(code + '\n//' + Math.random()).toString('base64'))
}
const conns = [
  { id: 'local', label: 'This device', key: 'macbook', kind: 'local', primary: true, device: 'macbook', aliases: ['macbook'] },
  { id: 'hub', label: 'hub-ec2', key: 'hub-ec2', kind: 'ssh', primary: false }
]
const state = { connectionId: { get: () => 'local' }, profile: { get: () => 'default' } }
const sender = { session_id: '20261030_201549_7bdc2d', title: 't', profile: 'default', platform: 'desktop' }
const num = (src, re) => Number(src.match(re)[1].replace(/_/g, ''))
const tick = () => new Promise(r => setTimeout(r, 5))

test('drain backoff + interval + request timeout stay inside the gateway courier liveness window', () => {
  const freshMs = num(utSource, /^COURIER_FRESH_S = ([\d.]+)/m) * 1000
  const backoff = num(source, /const ERROR_BACKOFF_MS = ([\d_]+)/)
  const interval = num(source, /const COURIER_INTERVAL_MS = ([\d_]+)/)
  const drainTimeout = num(source, /outboxPath\(conn, conns\), \{ timeoutMs: ([\d_]+)/)
  // Worst case between two successful drains: one GET times out, back off, wait for the next tick.
  assert.ok(drainTimeout + backoff + interval < freshMs, `${drainTimeout}+${backoff}+${interval} must be < ${freshMs}`)
})

test('a failed hub drain is retried before the hub calls this Mac gone', async () => {
  const realNow = Date.now
  let now = realNow()
  Date.now = () => now
  try {
    let hubDrains = 0
    const m = await load({ state, connections: async () => conns }, async req => {
      if (req.path.includes('/outbox') && req.connectionId === 'hub') {
        hubDrains++
        if (hubDrains === 1) throw Error('Timed out connecting to Hermes backend after 10000ms')
      }
      return req.path.includes('/outbox') ? { requests: [] } : { ok: true }
    })
    await m.courierTick()
    now += 10_000 // 10 s later, well inside the 20 s window
    await m.courierTick()
    assert.equal(hubDrains, 2)
  } finally {
    Date.now = realNow
  }
})

test('a stalled local backend does not hold back the hub drain', async () => {
  let hubDrains = 0
  const m = await load({ state, connections: async () => conns }, req => {
    if (req.path.includes('/outbox') && req.connectionId === 'local') return new Promise(() => {}) // never answers
    if (req.path.includes('/outbox')) hubDrains++
    return Promise.resolve(req.path.includes('/outbox') ? { requests: [] } : { ok: true })
  })
  void m.courierTick()
  await tick()
  void m.courierTick()
  await tick()
  void m.courierTick()
  await tick()
  assert.equal(hubDrains, 3)
})

test('start: a ticket-phase failure (request never sent) is retried and the window opens', async () => {
  const calls = []
  const m = await load({ state, connections: async () => conns }, async req => {
    calls.push(req)
    if (calls.length <= 2) throw Error('Gateway ticket bootstrap failed')
    return { ok: true, job_id: 'j1', status: 'waiting_for_user', terminal: 'Ghostty' }
  })
  const res = await m.performRequest({ action: 'user_terminal', route: 'macbook', args: { command: 'echo ok' }, sender }, 'hub', conns)
  assert.equal(res.ok, true)
  assert.equal(res.job_id, 'j1')
  assert.equal(calls.length, 3)
  assert.ok(calls.every(c => c.path === '/api/plugins/user-terminal/start' && c.connectionId === 'local'))
})

test('start: a failure after the request may have been sent is NOT retried (no double run)', async () => {
  const calls = []
  const m = await load({ state, connections: async () => conns }, async req => {
    calls.push(req)
    throw Error('Timed out connecting to Hermes backend after 40000ms')
  })
  const res = await m.performRequest({ action: 'user_terminal', route: 'macbook', args: { command: 'echo ok' }, sender }, 'hub', conns)
  assert.equal(res.ok, false)
  assert.match(res.error, /could not reach the user-terminal plugin on macbook/)
  assert.equal(calls.length, 1)
})

test('status: a ticket-phase failure is retried', async () => {
  let n = 0
  const m = await load({ state, connections: async () => conns }, async () => {
    if (++n === 1) throw Error('Gateway ticket control socket missing')
    return { ok: true, job_id: 'j1', status: 'ran', exit_code: 0, output: 'ok' }
  })
  const res = await m.performRequest({ action: 'user_terminal', args: { job_id: 'j1', wait_s: 5 }, sender }, 'hub', conns)
  assert.equal(res.status, 'ran')
  assert.equal(n, 2)
})

test('the result POST back to the gateway is retried when it fails once', async () => {
  const results = []
  let failed = false
  const m = await load({ state, connections: async () => conns }, async req => {
    if (req.path.includes('/outbox') && req.connectionId === 'hub') {
      return { requests: [{ id: 'r1', action: 'user_terminal', route: 'macbook', args: { job_id: 'j1', wait_s: 0 }, sender }] }
    }
    if (req.path.includes('/outbox')) return { requests: [] }
    if (req.path.includes('/user-terminal/status')) return { ok: true, job_id: 'j1', status: 'ran', exit_code: 0 }
    if (req.path.endsWith('/result') && req.connectionId === 'hub') {
      if (!failed) {
        failed = true
        throw Error('Timed out connecting to Hermes backend after 30000ms')
      }
      results.push(req.body)
    }
    return { ok: true }
  })
  await m.courierTick()
  for (let i = 0; i < 40 && !results.length; i++) await new Promise(r => setTimeout(r, 100))
  assert.equal(results.length, 1)
  assert.equal(results[0].id, 'r1')
  assert.equal(results[0].result.exit_code, 0)
})

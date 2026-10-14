// Regression: a cross_session send failed with "This chat is open in another Hermes window"
// when the request was drained by ANOTHER Mac's Desktop. That Desktop reaches the target
// gateway through a separate (SSH-isolated) serve process, and core refuses a submit from
// there into a chat the gateway's own Desktop holds open. Fix: each Desktop marks itself as
// its machine's home courier (heartbeat + ?home=1), and a send into a gateway that has a live
// home courier is forwarded to it instead of submitted through the non-owning backend.
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
// The mini's Desktop: its own machine is `local`; the MacBook is an SSH registry connection.
const conns = [
  { id: 'local', label: 'This device', key: 'this-device', kind: 'local', primary: true },
  { id: 'macbook', label: 'macbook', key: 'macbook', kind: 'ssh', primary: false }
]
const state = { connectionId: { get: () => 'local' }, profile: { get: () => 'default' } }
const nonOwning = async () => {
  throw Error('This chat is open in another Hermes window/terminal. Use it there, or start a new chat here.')
}

test('courier marks only its own machine as home: heartbeat + ?home=1; remote drains stay plain', async () => {
  const calls = []
  const m = await load({ state, connections: async () => conns }, async req => {
    calls.push(req)
    return req.path.includes('/outbox') ? { requests: [] } : { ok: true }
  })
  await m.courierTick()
  await new Promise(r => setTimeout(r, 10))
  const outbox = Object.fromEntries(calls.filter(c => c.path.includes('/outbox')).map(c => [c.connectionId, c.path]))
  assert.equal(outbox.local, '/api/plugins/session-mentions/outbox?home=1')
  assert.equal(outbox.macbook, '/api/plugins/session-mentions/outbox')
  const beats = calls.filter(c => c.path.endsWith('/result') && c.body?.id === 'home-courier')
  assert.deepEqual(beats.map(c => c.connectionId), ['local'])
})

test('send into a gateway with a live home courier is forwarded there, never submitted through the non-owning backend', async () => {
  const calls = []
  let polls = 0
  const m = await load(
    { state, connections: async () => conns, profileRoutes: async () => [{ connectionId: 'macbook', profile: 'default', targetProfile: 'default' }], requestProfile: nonOwning, request: nonOwning },
    async req => {
      calls.push(req)
      if (req.path.endsWith('/ping')) return { ok: true, home_courier: true, forwarding: true }
      if (req.path.endsWith('/enqueue')) return { ok: true }
      if (req.path.includes('/status/')) return ++polls < 2 ? { state: 'claimed' } : { state: 'done', result: { ok: true, delivered: false, queued: true, status: 'queued' } }
      throw Error(`unexpected ${req.path}`)
    }
  )
  const res = await m.performRequest(
    { action: 'send', args: { target: '@session:default@macbook/20261012_004204_228c80', message: 'hello' }, sender: { session_id: 's1', title: 'Echo Dot', profile: 'default' } },
    'local',
    conns
  )
  assert.equal(res.ok, true)
  assert.equal(res.status, 'queued')
  assert.equal(res.forwarded, true)
  const enq = calls.find(c => c.path.endsWith('/enqueue'))
  assert.equal(enq.connectionId, 'macbook')
  assert.equal(enq.body.request.args.target, '@session:default/20261012_004204_228c80')
  assert.equal(enq.body.request.prewrapped, true)
  assert.match(enq.body.request.args.message, /^\[Message from another Hermes session "Echo Dot"[^\n]*\]\n\nhello$/)
})

test('home courier delivers a forwarded request as-is through its owning socket (no second header, no re-forward)', async () => {
  const submitted = []
  const m = await load(
    {
      state,
      connections: async () => conns,
      request: async (method, p) => {
        if (method === 'session.resume') return { session_id: 'owner-live', running: false }
        submitted.push(p)
        return { status: 'streaming', user_row_id: 7 }
      }
    },
    async req => {
      throw Error(`no REST expected, got ${req.path}`)
    }
  )
  const res = await m.performRequest({ action: 'send', prewrapped: true, forwarded: true, args: { target: '@session:default/abc', message: '[Message from another Hermes session]\n\nhi' } }, 'local', conns)
  assert.equal(res.ok, true)
  assert.equal(submitted.length, 1)
  assert.equal(submitted[0].text, '[Message from another Hermes session]\n\nhi')
})

test('gateway without a home courier (or predating forwarding) gets the direct send', async () => {
  let resumed = 0
  const m = await load(
    {
      state,
      connections: async () => conns,
      profileRoutes: async () => [{ connectionId: 'macbook', profile: 'default', targetProfile: 'default' }],
      requestProfile: async (_r, method) => (method === 'session.resume' ? (resumed++, { session_id: 'live' }) : { status: 'queued' })
    },
    async req => (req.path.endsWith('/ping') ? { ok: true } : Promise.reject(Error(`unexpected ${req.path}`)))
  )
  const res = await m.performRequest({ action: 'send', args: { target: '@session:default@macbook/x', message: 'hi' } }, 'local', conns)
  assert.equal(res.ok, true)
  assert.equal(resumed, 1)
  assert.equal(res.forwarded, undefined)
})

test('lost enqueue ack: falls back only when the request is provably absent', async () => {
  let direct = 0
  const host = {
    state,
    connections: async () => conns,
    profileRoutes: async () => [{ connectionId: 'macbook', profile: 'default', targetProfile: 'default' }],
    requestProfile: async (_r, method) => (method === 'session.resume' ? { session_id: 'live' } : (direct++, { status: 'queued' }))
  }
  const api = statusReply => async req => {
    if (req.path.endsWith('/ping')) return { ok: true, home_courier: true, forwarding: true }
    if (req.path.endsWith('/enqueue')) throw Error('socket timeout')
    if (req.path.includes('/status/')) return statusReply()
    throw Error(`unexpected ${req.path}`)
  }
  let m = await load(host, api(() => ({ state: 'unknown' })))
  assert.equal((await m.forwardSend({ connectionId: 'macbook', profile: 'default', sessionId: 'x' }, 'hi')), null)

  m = await load(host, api(() => null))
  const r = await m.forwardSend({ connectionId: 'macbook', profile: 'default', sessionId: 'x' }, 'hi')
  assert.equal(r.status, 'unknown')
  assert.equal(r.retry_safe, false)
  assert.equal(direct, 0)
})

test("core's ownership refusal is reported as not delivered (safe to retry), not as unknown", async () => {
  const m = await load({ state, request: async method => (method === 'session.resume' ? { session_id: 'live' } : nonOwning()) }, async () => ({}))
  const r = await m.deliver({ connectionId: 'local', profile: 'default', sessionId: 'stored' }, 'test')
  assert.equal(r.ok, false)
  assert.equal(r.status, 'refused')
  assert.equal(r.delivered, false)
  assert.equal(r.retry_safe, true)
})

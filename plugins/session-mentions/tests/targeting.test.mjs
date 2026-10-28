// Regression tests for two courier bugs found by the Slack follower (Oct 2026):
// 1. cross_session send to `@session:alpha-follow@nous-1/<id>` ran in nous-1's DEFAULT profile: the
//    routed RPCs carried no `profile`, and a cloud gateway serves every profile from one backend.
// 2. `@this-device` refs are ambiguous on a gateway several Desktops drain; each Desktop now keys its
//    own Mac by device name and drains with courier/reach so the gateway can target requests.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { test } from 'node:test'
const source = await fs.readFile(new URL('../../../desktop-plugins/session-mentions/plugin.js', import.meta.url), 'utf8')
Error.stackTraceLimit = 1
async function load(host, api = async () => ({})) {
  globalThis.__smHost = host
  globalThis.window = { hermesDesktop: { api } }
  const code = source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'", 'const host = globalThis.__smHost; const COMPOSER_AREAS = {}')
  return import('data:text/javascript;base64,' + Buffer.from(code + '\n//' + Math.random()).toString('base64'))
}
const state = { connectionId: { get: () => 'local' }, profile: { get: () => 'default' } }

test('send to a named profile on a cloud gateway scopes every routed RPC to that profile', async () => {
  const calls = []
  const route = { connectionId: 'skint-polycentrism', profile: 'alpha-follow', targetProfile: 'alpha-follow' }
  const m = await load({
    state,
    profileRoutes: async () => [{ connectionId: 'skint-polycentrism', profile: 'default', targetProfile: 'default' }, route],
    requestProfile: async (r, method, params) => {
      calls.push({ r, method, params })
      return method === 'session.resume' ? { session_id: 'live-1' } : { status: 'streaming', user_row_id: 655 }
    },
    request: async () => {
      throw Error('primary socket must not be used for another gateway')
    }
  })
  const res = await m.deliver({ connectionId: 'skint-polycentrism', profile: 'alpha-follow', sessionId: '20261019_210732_7010b2' }, 'hi')
  assert.equal(res.ok, true)
  assert.deepEqual(calls.map(c => c.method), ['session.resume', 'prompt.submit'])
  for (const c of calls) {
    assert.equal(c.r, route)
    assert.equal(c.params.profile, 'alpha-follow', `${c.method} must name the profile`)
  }
})

test('routed RPCs use the backend profile name (targetProfile), not the Desktop label', async () => {
  const seen = []
  const m = await load({
    state,
    // The registry's logical profile id differs from the name the backend knows it by.
    profileRoutes: async () => [{ connectionId: 'mac-mini', profile: 'mac-mini::bb', targetProfile: 'bb' }],
    requestProfile: async (_r, method, params) => (seen.push(params.profile), method === 'session.resume' ? { session_id: 'l' } : { status: 'queued' })
  })
  await m.deliver({ connectionId: 'mac-mini', profile: 'bb', sessionId: 's' }, 'x')
  assert.deepEqual(seen, ['bb', 'bb'])
})

const raw = [
  { id: 'local', label: 'This device', kind: 'local', primary: true },
  { id: 'skint-polycentrism', label: 'nous-1', kind: 'cloud' },
  { id: 'mac-mini', label: 'mac-mini', kind: 'ssh' }
]

test('this Mac is keyed by its device name; refs handed to other gateways name it', async () => {
  const m = await load({ state })
  const conns = m.gatewayKeys(raw, { device: 'macbook', aliases: ['macbook', 'david-mbp'] })
  assert.equal(conns[0].key, 'macbook')
  assert.equal(m.sessionRef({ connectionId: 'local', profile: 'default', sessionId: '20261019_132933_0b8012' }, 'skint-polycentrism', conns), '@session:default@macbook/20261019_132933_0b8012')
  // Every spelling still resolves to this Mac: device, hostname alias, and the legacy label.
  for (const name of ['macbook', 'david-mbp', 'this-device', 'local']) assert.equal(m.findGateway(conns, name)?.id, 'local', name)
  // Unknown device name (older backend): legacy behaviour.
  assert.equal(m.gatewayKeys(raw, null)[0].key, 'this-device')
})

test('a device name that collides with a registry label falls back to the connection id', async () => {
  const m = await load({ state })
  const conns = m.gatewayKeys(raw, { device: 'mac-mini', aliases: ['mac-mini'] })
  assert.equal(conns[0].key, 'local')
  assert.equal(conns[2].key, 'mac-mini')
})

test('courier drains name the device, its aliases and every gateway it can reach', async () => {
  const m = await load({ state })
  const conns = m.gatewayKeys(raw, { device: 'macbook', aliases: ['macbook', 'david-mbp'] })
  const home = new URLSearchParams(m.outboxPath(conns[0], conns).split('?')[1])
  assert.equal(home.get('home'), '1')
  assert.equal(home.get('courier'), 'macbook')
  assert.deepEqual(home.get('aliases').split(','), ['macbook', 'david-mbp'])
  const reach = home.get('reach').split(',')
  for (const n of ['macbook', 'david-mbp', 'nous-1', 'skint-polycentrism', 'mac-mini']) assert.ok(reach.includes(n), n)
  assert.ok(!reach.includes('this-device') && !reach.includes('local'))
  const remote = new URLSearchParams(m.outboxPath(conns[1], conns).split('?')[1])
  assert.equal(remote.get('home'), null)
  assert.equal(remote.get('courier'), 'macbook')
  // Device unknown (home backend predates targeting): plain legacy drain.
  const legacy = m.gatewayKeys(raw, null)
  assert.equal(m.outboxPath(legacy[0], legacy), '/outbox?home=1')
  assert.equal(m.outboxPath(legacy[1], legacy), '/outbox')
})

test('courier learns the device name from its own backend ping', async () => {
  const paths = []
  const m = await load({ state, connections: async () => raw }, async req => {
    paths.push([req.connectionId, req.path])
    if (req.path.endsWith('/ping')) return { ok: true, device: 'macbook', aliases: ['macbook', 'david-mbp'] }
    return req.path.includes('/outbox') ? { requests: [] } : { ok: true }
  })
  await m.courierTick()
  const outbox = Object.fromEntries(paths.filter(([, p]) => p.includes('/outbox')).map(([c, p]) => [c, p]))
  assert.match(outbox.local, /outbox\?home=1&courier=macbook&/)
  assert.match(outbox['skint-polycentrism'], /outbox\?courier=macbook&/)
  // The device name comes from the home backend's ping; every other gateway is pinged once too,
  // only for its registry name (registry-names.test.mjs).
  const pinged = paths.filter(([, p]) => p.endsWith('/ping')).map(([c]) => c)
  assert.equal(pinged[0], 'local')
  assert.deepEqual([...pinged].sort(), raw.map(c => c.id).sort())
})

test('backend predating targeting: the device name falls back to its hostname', async () => {
  const m = await load({ state, connections: async () => raw }, async req => {
    if (req.path.endsWith('/ping')) return { ok: true, home_courier: true, forwarding: true }
    if (req.path === '/api/system/stats') return { hostname: 'David-MBP.local' }
    return req.path.includes('/outbox') ? { requests: [] } : { ok: true }
  })
  const paths = []
  const real = globalThis.window.hermesDesktop.api
  globalThis.window.hermesDesktop.api = req => (paths.push(req.path), real(req))
  await m.courierTick()
  assert.ok(paths.some(p => p.startsWith('/api/plugins/session-mentions/outbox?home=1&courier=david-mbp&')))
})

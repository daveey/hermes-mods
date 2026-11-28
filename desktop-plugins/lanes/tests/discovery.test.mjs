import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin } from './sdk-mock.mjs'
const P = await loadPlugin()
const plain = { configured: true, lanes: { project: { share: 20, gated: false } } }
const steering = { configured: true, lanes: { project: { share: 40, gated: true } } }

test('cheap authority selects the hub even when its full projection only warms', async () => {
  const calls = []
  let ready = false
  const source = P.createSource({ rest: async (path, opts) => {
    calls.push([opts.connectionId, path, opts.method || 'GET'])
    if (opts.connectionId === 'mac') return plain
    if (path === '/v1/lanes/steering') return { configured: true, steering: true }
    return ready ? steering : { configured: true, warming: true }
  } }, { profileRoutes: async () => ['mac', 'hub'].map(connectionId => ({ connectionId, targetProfile: 'default' })) })
  assert.equal((await source.read(6)).warming, true)
  assert.equal(source.target().connectionId, 'hub')
  await source.read(6)
  await source.write('project', { share: 40 })
  assert.equal(calls.at(-1)[0], 'hub')
  assert.equal(calls.at(-1)[2], 'PUT')
  ready = true
  assert.deepEqual(await source.read(6), steering)
})

test('only the cheap hub probe responds: it still owns writes', async () => {
  const calls = []
  const source = P.createSource({ rest: async (path, opts) => {
    if (opts.method === 'PUT') { calls.push(opts.connectionId); return {} }
    if (opts.connectionId === 'mac') return plain
    if (path === '/v1/lanes/steering') return { configured: true, steering: true }
    throw Error('full projection unavailable')
  } }, { profileRoutes: async () => ['mac', 'hub'].map(connectionId => ({ connectionId, targetProfile: 'default' })) })
  assert.equal((await source.read(6)).warming, true)
  assert.equal(source.target().connectionId, 'hub')
  await source.write('project', { share: 40 })
  assert.deepEqual(calls, ['hub'])
})

test('a display frame during a still-warming discovery cannot consume its next retry', async () => {
  const oldBridge = window.hermesDesktop, oldWS = globalThis.WebSocket
  const deadlines = new Map(), sockets = []; let serial = 0, hold = false, release
  const timers = { setTimeout(fn, ms) { const id = ++serial; deadlines.set(id, { fn, ms }); return id },
    clearTimeout(id) { deadlines.delete(id) }, setInterval() { return 1 }, clearInterval() {} }
  window.hermesDesktop = { getConnectionFor: async () => ({ authMode: 'native' }), getPluginWsUrl: async () => 'ws://example.com/ticket' }
  globalThis.WebSocket = class { constructor() { this.readyState = 1; sockets.push(this) } send() {} close() { this.readyState = 3 } }
  const source = P.createSource({ rest: async (path, opts) => {
    if (path === '/v1/lanes/steering') throw Error('old server')
    if (opts.connectionId === 'mac') return plain
    if (hold) { hold = false; await new Promise(resolve => { release = resolve }) }
    return { configured: true, warming: true }
  } }, { profileRoutes: async () => ['mac', 'hub'].map(connectionId => ({ connectionId, targetProfile: 'default' })) })
  const flush = () => new Promise(resolve => setTimeout(resolve, 10))
  const stop = source.subscribe({ hours: 6, window: '6h', expanded: false }, () => {}, () => {}, timers)
  try {
    await flush()
    const retry = [...deadlines.entries()].find(([, t]) => t.ms === 1000)
    assert.ok(retry)
    hold = true; deadlines.delete(retry[0]); retry[1].fn(); await flush()
    sockets[0].onmessage({ data: JSON.stringify({ version: 1, view: plain }) })
    release(); await flush()
    assert.ok([...deadlines.values()].some(t => t.ms === 1000), 'discovery must rearm independently of display frame freshness')
  } finally { stop(); window.hermesDesktop = oldBridge; globalThis.WebSocket = oldWS }
})
for (const ids of [['hub', 'mac'], ['mac', 'hub']]) {
  const routes = { profileRoutes: async () => ids.map(connectionId => ({ connectionId, targetProfile: 'default' })) }
  for (const initial of ['warming', 'timeout']) test(`${ids}: ${initial} hub is re-probed before fallback`, async () => {
    let probes = 0; const calls = []
    const source = P.createSource({ rest: async (path, opts) => {
      calls.push([opts.connectionId, opts.method || 'GET'])
      if (opts.connectionId === 'mac') return plain
      if (++probes === 1) { if (initial === 'timeout') throw Error('timed out'); return { configured: true, warming: true } }
      return steering
    } }, routes)
    assert.deepEqual(await source.read(6), steering)
    assert.equal(source.target().connectionId, 'hub')
    await source.read(6)
    await source.write('project', { share: 41 })
    assert.deepEqual(calls.slice(-2), [['hub', 'GET'], ['hub', 'PUT']])
  })
  test(`${ids}: non-steering display source rejects writes`, async () => {
    const calls = []
    const source = P.createSource({ rest: async (path, opts) => { calls.push(opts.method || 'GET'); return plain } }, routes)
    assert.deepEqual(await source.read(6), plain)
    await assert.rejects(source.write('project', { share: 41 }), /steering gateway.*reset by sync/i)
    assert.ok(!calls.includes('PUT'))
  })
  test(`${ids}: live display stream cannot cancel steering discovery`, async () => {
    const oldBridge = window.hermesDesktop, oldWS = globalThis.WebSocket
    const sockets = [], deadlines = new Map(); let serial = 0, ready = false, release
    const timers = { setTimeout(fn, ms) { const id = ++serial; deadlines.set(id, { fn, ms }); return id },
      clearTimeout(id) { deadlines.delete(id) }, setInterval() { return 1 }, clearInterval() {} }
    window.hermesDesktop = { getConnectionFor: async () => ({ authMode: 'native' }), getPluginWsUrl: async () => 'ws://example.com/ticket' }
    globalThis.WebSocket = class { constructor() { this.readyState = 1; sockets.push(this) } send() {} close() { this.readyState = 3; this.onclose?.() } }
    const views = []
    const source = P.createSource({ rest: async (path, opts) => {
      if (opts.connectionId === 'mac') return plain
      if (ready) return new Promise(resolve => { release = () => resolve(steering) })
      return { configured: true, warming: true }
    } }, routes)
    const stop = source.subscribe({ hours: 6, window: '6h', expanded: false }, view => views.push(view), () => {}, timers)
    const flush = () => new Promise(resolve => setTimeout(resolve, 10))
    try {
      await flush()
      assert.equal(source.target().connectionId, 'mac')
      const display = sockets[0]
      display.onmessage({ data: JSON.stringify({ version: 1, view: plain }) })
      const retry = [...deadlines.entries()].find(([, t]) => t.ms === 1000)
      assert.ok(retry, 'a live display frame leaves discovery armed')
      ready = true; deadlines.delete(retry[0]); retry[1].fn(); await flush()
      display.onmessage({ data: JSON.stringify({ version: 2, view: plain }) })
      release(); await flush()
      assert.equal(source.target().connectionId, 'hub')
      assert.equal(display.readyState, 3)
      assert.equal(sockets.length, 2)
      assert.deepEqual(views.at(-1), steering)
    } finally { stop(); window.hermesDesktop = oldBridge; globalThis.WebSocket = oldWS }
    assert.equal(deadlines.size, 0)
  })
  test(`${ids}: display fallback keeps searching while hub warms`, async () => {
    let ready = false; const calls = []
    const source = P.createSource({ rest: async (path, opts) => {
      calls.push([opts.connectionId, opts.method || 'GET'])
      return opts.connectionId === 'mac' ? plain : ready ? steering : { configured: true, warming: true }
    } }, routes)
    assert.deepEqual(await source.read(6), plain)
    const fallback = source.target()
    await assert.rejects(source.write('project', { share: 41 }), /reset by sync/)
    ready = true
    assert.deepEqual(await source.read(6), steering)
    await assert.rejects(source.write('project', { share: 41 }, fallback), /reset by sync/)
    await source.write('project', { share: 41 })
    assert.deepEqual(calls.filter(c => c[1] === 'PUT'), [['hub', 'PUT']])
  })
}

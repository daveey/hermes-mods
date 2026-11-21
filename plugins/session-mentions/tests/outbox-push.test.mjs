// Outbox push (2026-11-20): the courier drained every gateway every 3 s (~70 requests/min on three connections)
// even with the window hidden. Now the gateway pushes `plugin.session-mentions.outbox` {feed, seq} when its outbox
// gets a request (and a 20 s heartbeat); a connection whose pushes arrive drains on the push, at once, plus a 60 s
// safety poll, and keeps its courier record fresh with an 8 s /ping. A connection whose pushes never arrive keeps
// the 3 s drain. These pin: a push drains the right connection, a burst coalesces into one extra drain, the safety
// poll / ping / fallback cadences, and a failed push-driven drain retried after the backoff.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { test } from 'node:test'
const source = await fs.readFile(new URL('../../../desktop-plugins/session-mentions/plugin.js', import.meta.url), 'utf8')
Error.stackTraceLimit = 1

const conns = [
  { id: 'local', label: 'This device', key: 'macbook', kind: 'local', primary: true, device: 'macbook', aliases: ['macbook'] },
  { id: 'hub', label: 'hub-ec2', key: 'hub-ec2', kind: 'ssh', primary: false }
]
const FEEDS = { local: 'macbook:aaaaaaaaaaaa', hub: 'hub:bbbbbbbbbbbb' }
const state = { connectionId: { get: () => 'local' }, profile: { get: () => 'default' } }
const tick = () => new Promise(r => setTimeout(r, 5))

async function load(api, hostExtra = {}) {
  globalThis.__smHost = { state, connections: async () => conns, onEvent: () => () => {}, ...hostExtra }
  globalThis.window = { hermesDesktop: { api } }
  const code = source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'", 'const host = globalThis.__smHost; const COMPOSER_AREAS = { middleware: "mw", atCompletions: "at" }')
  return import('data:text/javascript;base64,' + Buffer.from(code + '\n//' + Math.random()).toString('base64'))
}

/** A fake bridge that counts drains / pings per connection and answers with each gateway's feed. */
function backend(over = {}) {
  const calls = { outbox: { local: 0, hub: 0 }, ping: { local: 0, hub: 0 }, beat: 0, pingPaths: [] }
  const api = async req => {
    const id = req.connectionId
    if (req.path.includes('/session-mentions/outbox')) {
      calls.outbox[id]++
      if (over.outbox) return over.outbox(id, calls)
      return { requests: [], feed: FEEDS[id] }
    }
    if (req.path.includes('/session-mentions/ping')) {
      if (req.path.includes('courier=')) { // the courier's liveness ping (a bare /ping is connection discovery)
        calls.ping[id]++
        calls.pingPaths.push(req.path)
      }
      return { ok: true, feed: FEEDS[id], push: true, device: id === 'local' ? 'macbook' : 'hub' }
    }
    if (req.path.endsWith('/session-mentions/result')) {
      calls.beat++
      return { ok: true }
    }
    return { ok: true }
  }
  return { api, calls }
}

function clock() {
  const realNow = Date.now
  let now = realNow()
  Date.now = () => now
  return { advance: ms => (now += ms), restore: () => (Date.now = realNow) }
}

test('a push drains the connection whose feed it names, and only that one', async () => {
  const { api, calls } = backend()
  const m = await load(api)
  await m.courierTick() // fallback drains learn each connection's feed
  assert.deepEqual(calls.outbox, { local: 1, hub: 1 })
  await m.onOutboxEvent({ payload: { feed: FEEDS.hub, seq: 1 } })
  assert.deepEqual(calls.outbox, { local: 1, hub: 2 })
  assert.equal(m.pushLive('hub'), true)
  assert.equal(m.pushLive('local'), false)
  await m.onOutboxEvent({ payload: { feed: 'other:cccccccccccc', seq: 2 } }) // a feed no connection answered with
  await m.onOutboxEvent({ payload: { seq: 3 } })
  assert.deepEqual(calls.outbox, { local: 1, hub: 2 })
})

test('a heartbeat marks the connection push-live without draining it', async () => {
  const { api, calls } = backend()
  const m = await load(api)
  await m.courierTick()
  await m.onOutboxEvent({ payload: { feed: FEEDS.hub, seq: 7, heartbeat: true } })
  assert.equal(m.pushLive('hub'), true)
  assert.equal(calls.outbox.hub, 1)
})

test('a burst of pushes during a drain coalesces into one more drain', async () => {
  let release
  const gate = new Promise(r => (release = r))
  const { api, calls } = backend({
    outbox: async (id, c) => {
      if (id === 'hub' && c.outbox.hub === 2) await gate // the first push-driven drain stalls
      return { requests: [], feed: FEEDS[id] }
    }
  })
  const m = await load(api)
  await m.courierTick()
  const first = m.onOutboxEvent({ payload: { feed: FEEDS.hub, seq: 1 } })
  await tick()
  const burst = [2, 3, 4, 5, 6].map(seq => m.onOutboxEvent({ payload: { feed: FEEDS.hub, seq } }))
  await tick()
  assert.equal(calls.outbox.hub, 2) // one in flight; the burst did not start more
  release()
  await Promise.all([first, ...burst])
  assert.equal(calls.outbox.hub, 3) // exactly one rerun, which sees everything the burst announced
  assert.equal(calls.outbox.local, 1)
})

test('a push-driven drain performs the requests it claims', async () => {
  const results = []
  const { api } = backend({
    outbox: (id, c) => (id === 'hub' && c.outbox.hub === 2
      ? { requests: [{ id: 'r1', action: 'nope' }], feed: FEEDS.hub }
      : { requests: [], feed: FEEDS[id] })
  })
  const m = await load(async req => {
    if (req.path.endsWith('/session-mentions/result') && req.body?.id === 'r1') results.push(req)
    return api(req)
  })
  await m.courierTick()
  await m.onOutboxEvent({ payload: { feed: FEEDS.hub, seq: 1 } })
  for (let i = 0; i < 40 && !results.length; i++) await tick()
  assert.equal(results.length, 1)
  assert.equal(results[0].connectionId, 'hub')
})

test('push-live: no 3 s drains; a courier ping every 8 s; a safety drain every 60 s; stale pushes fall back', async () => {
  const c = clock()
  try {
    const { api, calls } = backend()
    const m = await load(api)
    await m.courierTick()
    await m.onOutboxEvent({ payload: { feed: FEEDS.hub, seq: 1, heartbeat: true } })
    const drains0 = calls.outbox.hub
    for (let t = 0; t < 57; t += 3) {
      c.advance(3_000)
      await m.courierTick()
      await tick()
      if (t % 18 === 0) await m.onOutboxEvent({ payload: { feed: FEEDS.hub, seq: 2 + t, heartbeat: true } }) // keeps it live
    }
    // 57 s: the safety poll is not due yet; the ping kept the courier record fresh instead.
    assert.equal(calls.outbox.hub, drains0)
    assert.ok(calls.ping.hub >= 6 && calls.ping.hub <= 8, `pings in 57 s: ${calls.ping.hub}`)
    assert.ok(calls.pingPaths.length && calls.pingPaths.every(p => p.includes('courier=macbook') && p.includes('reach=')), 'the ping carries the drain parameters')
    c.advance(3_000)
    await m.courierTick()
    assert.equal(calls.outbox.hub, drains0 + 1, 'the 60 s safety drain')
    // Meanwhile the local connection never got a push: it drained on every 3 s tick, as before.
    assert.equal(calls.outbox.local, 1 + 20)
    // Pushes stop (socket closed, backend restarted without the push): after PUSH_STALE_MS, back to 3 s drains.
    c.advance(m.PUSH_STALE_MS + 1)
    const before = calls.outbox.hub
    await m.courierTick()
    c.advance(3_000)
    await m.courierTick()
    assert.equal(calls.outbox.hub, before + 2)
  } finally {
    c.restore()
  }
})

test('a failed push-driven drain is retried after the backoff, not at the next safety poll', async () => {
  const c = clock()
  try {
    let fail = false
    const { api, calls } = backend({
      outbox: id => {
        if (id === 'hub' && fail) {
          fail = false
          throw Error('Timed out connecting to Hermes backend after 10000ms')
        }
        return { requests: [], feed: FEEDS[id] }
      }
    })
    const m = await load(api)
    await m.courierTick()
    fail = true
    await m.onOutboxEvent({ payload: { feed: FEEDS.hub, seq: 1 } })
    assert.equal(calls.outbox.hub, 2)
    c.advance(3_000)
    await m.courierTick()
    assert.equal(calls.outbox.hub, 2) // inside the 5 s backoff
    c.advance(3_000)
    await m.courierTick()
    assert.equal(calls.outbox.hub, 3) // retried well inside the pickup timeouts (15 s / 30 s)
  } finally {
    c.restore()
  }
})

test('the safety poll and home beat cadences', async () => {
  const m = await load(backend().api)
  assert.equal(m.SAFETY_POLL_MS, 60_000)
  assert.equal(m.PUSH_STALE_MS, 60_000) // three missed 20 s heartbeats (plugin_api.HEARTBEAT_S)
  assert.equal(m.OUTBOX_EVENT, 'plugin.session-mentions.outbox')
  assert.equal(m.HOME_BEAT_MS, 8_000)
})

test('register subscribes to the outbox push and unsubscribes on dispose', async () => {
  const subs = []
  const offs = []
  const onEvent = (name, fn) => {
    subs.push([name, fn])
    return () => offs.push(name)
  }
  const { api, calls } = backend()
  const m = await load(api, { onEvent })
  const disposers = []
  m.default.register({ register: () => {}, onDispose: fn => disposers.push(fn), setInterval: () => 0, onEvent })
  const sub = subs.find(([name]) => name === m.OUTBOX_EVENT)
  assert.ok(sub, 'subscribed to plugin.session-mentions.outbox')
  await m.courierTick()
  sub[1]({ payload: { feed: FEEDS.hub, seq: 1 } })
  for (let i = 0; i < 20 && calls.outbox.hub < 2; i++) await tick()
  assert.equal(calls.outbox.hub, 2)
  disposers.forEach(fn => fn())
  assert.ok(offs.includes(m.OUTBOX_EVENT))
})

test('a push lost while push-live: the gateway re-push (every 5 s) or a pending heartbeat drains it within 15 s', async () => {
  const c = clock()
  try {
    let waiting = false // a request sits in the hub outbox
    const drainedAt = []
    const start = Date.now()
    const { api, calls } = backend({
      outbox: id => {
        if (id === 'hub' && waiting) {
          waiting = false
          drainedAt.push(Date.now() - start)
        }
        return { requests: [], feed: FEEDS[id] }
      }
    })
    const m = await load(api)
    await m.courierTick()
    await m.onOutboxEvent({ payload: { feed: FEEDS.hub, seq: 1, heartbeat: true } })
    const before = calls.outbox.hub
    waiting = true // ... and its push never reached this window
    // The gateway's watcher (plugin_api.watch_once, pinned in test_outbox_push.py) re-pushes 5 s later;
    // meanwhile the courier's ticks only ping, they do not drain a push-live connection.
    for (let t = 3_000; t <= 15_000 && waiting; t += 1_000) {
      c.advance(1_000)
      if (t % 3_000 === 0) await m.courierTick()
      if (t === 5_000) await m.onOutboxEvent({ payload: { feed: FEEDS.hub, seq: 2 } })
    }
    assert.equal(waiting, false)
    assert.ok(drainedAt[0] <= 15_000, `drained after ${drainedAt[0]} ms`)
    assert.equal(calls.outbox.hub, before + 1)
  } finally {
    c.restore()
  }
})

test('a heartbeat with pending drains; one without does not', async () => {
  const { api, calls } = backend()
  const m = await load(api)
  await m.courierTick()
  await m.onOutboxEvent({ payload: { feed: FEEDS.hub, seq: 1, heartbeat: true, pending: false } })
  assert.equal(calls.outbox.hub, 1)
  await m.onOutboxEvent({ payload: { feed: FEEDS.hub, seq: 2, heartbeat: true, pending: true } })
  assert.equal(calls.outbox.hub, 2)
  assert.equal(calls.outbox.local, 1)
})

test('a tag that agrees with the connection\'s own feed is exact', async () => {
  const { api, calls } = backend()
  const m = await load(api)
  await m.courierTick()
  await m.onOutboxEvent({ connectionId: 'hub', payload: { feed: FEEDS.hub, seq: 1 } })
  assert.deepEqual(calls.outbox, { local: 1, hub: 2 })
  assert.equal(m.pushLive('hub'), true)
  assert.equal(m.pushLive('local'), false)
})

test('mis-tagged: core stamps the local gateway push with the ACTIVE (hub) connection id; feed wins', async () => {
  const { api, calls } = backend()
  const m = await load(api)
  await m.courierTick()
  await m.onOutboxEvent({ connectionId: 'hub', payload: { feed: FEEDS.local, seq: 1 } })
  assert.deepEqual(calls.outbox, { local: 2, hub: 1 }) // local drained, hub not
  assert.equal(m.pushLive('local'), true)
  assert.equal(m.pushLive('hub'), false)
  // The event taught nothing: hub still answers for its own feed.
  await m.onOutboxEvent({ payload: { feed: FEEDS.hub, seq: 2 } })
  assert.deepEqual(calls.outbox, { local: 2, hub: 2 })
  // A pending heartbeat mis-tagged the same way also goes to local.
  await m.onOutboxEvent({ connectionId: 'hub', payload: { feed: FEEDS.local, seq: 3, heartbeat: true, pending: true } })
  assert.deepEqual(calls.outbox, { local: 3, hub: 2 })
})

test('a tag naming a connection whose feed is not known yet drains it but does not mark it push-live', async () => {
  const { api, calls } = backend()
  const m = await load(api)
  await m.onOutboxEvent({ connectionId: 'hub', payload: { feed: FEEDS.hub, seq: 1 } }) // before any drain answered
  assert.equal(calls.outbox.hub, 1)
  assert.equal(m.pushLive('hub'), false)
})

test('two gateways sharing a feed id (cloned image): an untagged event drains both and marks neither push-live', async () => {
  const shared = 'clone:dddddddddddd'
  const { api, calls } = backend({ outbox: () => ({ requests: [], feed: shared }) })
  const m = await load(api)
  await m.courierTick()
  await m.onOutboxEvent({ payload: { feed: shared, seq: 1 } })
  assert.deepEqual(calls.outbox, { local: 2, hub: 2 })
  assert.equal(m.pushLive('local'), false) // both keep their 3 s fallback drain
  assert.equal(m.pushLive('hub'), false)
  await m.onOutboxEvent({ connectionId: 'hub', payload: { feed: shared, seq: 2, heartbeat: true } })
  assert.equal(m.pushLive('hub'), true) // a tag that agrees with its own feed is exact
  assert.equal(m.pushLive('local'), false)
})

test('an agreeing tag drains every connection on that outbox but vouches only for the tagged one', async () => {
  // Two connections to one gateway outbox (or a cloned image's twin): both answered with the same feed.
  const shared = 'gw:eeeeeeeeeeee'
  const { api, calls } = backend({ outbox: () => ({ requests: [], feed: shared }) })
  const m = await load(api)
  await m.courierTick()
  await m.onOutboxEvent({ connectionId: 'hub', payload: { feed: shared, seq: 1 } })
  assert.deepEqual(calls.outbox, { local: 2, hub: 2 })
  assert.equal(m.pushLive('hub'), true)
  assert.equal(m.pushLive('local'), false)
})

test('a removed connection no longer makes a feed look shared', async () => {
  const c = clock()
  try {
    const shared = 'gw:ffffffffffff'
    let list = conns
    const { api, calls } = backend({ outbox: () => ({ requests: [], feed: shared }) })
    const m = await load(api, { connections: async () => list })
    await m.courierTick() // both connections answered with the same feed
    await m.onOutboxEvent({ payload: { feed: shared, seq: 1 } })
    assert.equal(m.pushLive('local'), false) // shared: nobody vouched for
    list = conns.filter(x => x.id === 'local') // hub removed from the registry
    c.advance(61_000) // past the plugin's 60 s connection-list cache
    const hubBefore = calls.outbox.hub
    await m.onOutboxEvent({ payload: { feed: shared, seq: 2 } })
    assert.equal(m.pushLive('local'), true) // the feed is local's alone now
    assert.equal(calls.outbox.hub, hubBefore)
    assert.equal(m.courierStateForTests().feedConns.get(shared).size, 1)
    assert.equal(m.courierStateForTests().pushAt.has('hub'), false)
  } finally {
    c.restore()
  }
})

// Pushes: plugin.runners.fleet replaces the 15 s fleet poll while it arrives (safety read every SAFETY_MS), the
// polls resume when it stops; plugin.throughput.snapshot carries the throttle.
import test from 'node:test'
import assert from 'node:assert/strict'
import { React, loadPlugin, mount, host, route } from './sdk-mock.mjs'

const P = await loadPlugin()
const NOW = Date.parse('2026-11-20T06:00:00Z')
const FEED = 'hub:4242'
const fleet = (busy = 0, over = {}) => ({
  contract_version: 1, profile: 'example', generated_at: '2026-11-20T06:00:00Z', feed: FEED, hash: `h${busy}`,
  gateways: { state: 'ready', machines: [{ name: 'worker-1', state: 'active', reason: null, labels: [], pinned_only: false,
    slots: 3, slots_source: 'registry',
    running: Array.from({ length: busy }, (_, i) => ({ board: 'default', task: `t_${i}aaaa111`, title: 'Card', started_at: NOW / 1000 })) }] },
  ci: { state: 'ready', machines: [] },
  ...over
})
const push = (busy, over = {}) => {
  const { feed, hash, ...body } = fleet(busy)
  return { feed: FEED, epoch: 'e1', seq: 1, at: NOW / 1000, profile: 'example', hash, fleet: body, ...over }
}
function ctxWith(body = fleet()) {
  const calls = []
  return { calls, rest: async (path, opts) => { calls.push([path, opts]); return structuredClone(body) } }
}
const busy = (view) => view.gateways.machines.find((m) => m.name === 'worker-1').running.length

test('a live push replaces the poll: no request until the safety read; polls resume when pushes stop', async () => {
  let t = NOW
  const ctx = ctxWith()
  const transport = P.createTransport(ctx, { profileRoutes: async () => [route] }, () => t)
  await transport.poll()
  assert.equal(ctx.calls.length, 1)
  assert.equal(transport.applyPush(push(2)), true)
  assert.equal(busy(transport.view()), 2, 'the pushed fleet is what the panel shows')
  for (let i = 0; i < 7; i++) { t += P.POLL_OPEN_MS; await transport.poll() } // up to 105 s, inside PUSH_TRUST_MS
  assert.equal(ctx.calls.length, 1, 'no requests while pushes are live')
  // Heartbeats keep it live; the safety read still happens every SAFETY_MS.
  t = NOW + P.SAFETY_MS
  transport.applyPush({ feed: FEED, profile: 'example', heartbeat: true, hash: 'h2' })
  await transport.poll()
  assert.equal(ctx.calls.length, 2, 'one safety read')
  t += P.POLL_OPEN_MS
  await transport.poll()
  assert.equal(ctx.calls.length, 2)
  // No push for PUSH_TRUST_MS: back to one request per 15 s poll.
  t += P.PUSH_TRUST_MS
  await transport.poll(); t += P.POLL_OPEN_MS; await transport.poll()
  assert.equal(ctx.calls.length, 4, 'polling resumed')
})

test('pushes from another gateway or profile are ignored; a heartbeat confirms only a matching hash', async () => {
  let t = NOW
  const ctx = ctxWith(fleet(1))
  const transport = P.createTransport(ctx, { profileRoutes: async () => [route] }, () => t)
  await transport.poll()
  assert.equal(transport.applyPush(push(3, { feed: 'other:1' })), false)
  assert.equal(transport.applyPush(push(3, { profile: 'default' })), false)
  assert.equal(busy(transport.view()), 1)
  t += 60000
  assert.equal(transport.applyPush({ feed: FEED, profile: 'example', heartbeat: true, hash: 'nope' }), false)
  assert.equal(transport.routes.values().next().value.at, NOW, 'an unmatched heartbeat does not vouch for the data')
  assert.equal(transport.routes.values().next().value.dirty, true, 'an unmatched heartbeat means a missed change')
  const before = ctx.calls.length
  t += P.POLL_OPEN_MS
  await transport.poll()
  assert.equal(ctx.calls.length, before + 1, 'so the next poll reads it although pushes are live')
  assert.equal(transport.routes.values().next().value.dirty, false)
  assert.equal(transport.applyPush({ feed: FEED, profile: 'example', heartbeat: true, hash: 'h1' }), true)
  assert.equal(transport.routes.values().next().value.at, t, 'same hash as our answer: current again')
})

test('a push too big to carry the fleet makes the next poll read it', async () => {
  let t = NOW
  const ctx = ctxWith()
  const transport = P.createTransport(ctx, { profileRoutes: async () => [route] }, () => t)
  await transport.poll()
  transport.applyPush(push(1))
  t += P.POLL_OPEN_MS
  await transport.poll()
  assert.equal(ctx.calls.length, 1)
  transport.applyPush({ feed: FEED, profile: 'example', fleet: null, changed: true, hash: 'big' })
  t += P.POLL_OPEN_MS
  await transport.poll()
  assert.equal(ctx.calls.length, 2)
})

test('Runners: fleet and throttle pushes update the chip; the throttle poll slows to the safety read, stops hidden', async () => {
  const listeners = new Map()
  const oldApi = window.hermesDesktop
  window.hermesDesktop = { api: async () => ({ percent: 100, cap_per_s: null, feed: FEED, slots: { gateways: {} } }) }
  const ctx = { ...ctxWith(fleet(0)), onEvent: (type, fn) => { listeners.set(type, fn); return () => listeners.delete(type) } }
  const ui = await mount(React.createElement(P.Runners, { ctx }))
  try {
    const chip = () => document.querySelector('[data-slot="runners-chip"]')
    assert.equal(chip().querySelectorAll('[data-pip-machine="worker-1"] .is-busy').length, 0)
    await React.act(async () => listeners.get(P.FLEET_EVENT)({ payload: push(2) }))
    await ui.flush()
    assert.equal(chip().querySelectorAll('[data-pip-machine="worker-1"] .is-busy').length, 2, 'pushed fleet drawn')
    const tq = () => ui.client.getQueryCache().find({ queryKey: ['runners', 'throttle'] })
    assert.equal(tq().observers[0].options.refetchInterval, P.POLL_OPEN_MS)
    const throttle = { percent: 100, cap_per_s: 60, feed: FEED, slots: { gateways: { 'worker-1': { allowed: 1, cap_per_s: 60, tokens_per_slot_per_s: 60 } } } }
    await React.act(async () => listeners.get(P.SNAPSHOT_EVENT)({ payload: { feed: 'other:1', throttle: { ...throttle, percent: 5 } } }))
    assert.notEqual(tq().state.data.percent, 5, 'another gateway\'s throttle is ignored')
    await React.act(async () => listeners.get(P.SNAPSHOT_EVENT)({ payload: { feed: FEED, throttle } }))
    await ui.flush()
    assert.equal(tq().state.data.cap_per_s, 60)
    assert.equal(chip().querySelectorAll('[data-pip-machine="worker-1"] .is-thr').length, 1, 'the free slot beyond the pushed limit hatches')
    assert.equal(tq().observers[0].options.refetchInterval, P.SAFETY_MS, 'live: only the safety read')
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true })
    await React.act(async () => document.dispatchEvent(new window.Event('visibilitychange')))
    await ui.flush()
    assert.equal(tq().observers[0].options.refetchInterval, false)
    assert.equal(ui.client.getQueryCache().find({ queryKey: ['runners', 'fleet'] }).observers[0].options.refetchInterval, false)
  } finally {
    delete document.visibilityState
    window.hermesDesktop = oldApi
    await ui.close()
  }
  assert.equal(listeners.size, 0, 'unsubscribed on unmount')
})

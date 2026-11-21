// node --test: core-update chip reads this machine's gateway (the local connection), polls only while the window is
// visible, and reads once when it is shown again.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'

register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,' + encodeURIComponent(
    'export const host = globalThis.__host = { state: {} };' +
    'export const atom = v => { let x = v; return { get: () => x, set: n => { x = n } } };' +
    'export const STATUSBAR_AREAS = { right: "statusbar.right" }; export const useValue = a => a.get();'), shortCircuit: true }
  if (spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const jsx=(type,props,key)=>({type,props,key});export const jsxs=(type,props,key)=>({type,props,key})', shortCircuit: true }
  return next(spec, ctx)
}`))
const m = await import('../plugin.js')
const host = globalThis.__host
const tick = () => new Promise(r => setTimeout(r, 5))

function fakeDocument() {
  const listeners = new Set()
  return {
    hidden: false,
    addEventListener: (t, f) => { if (t === 'visibilitychange') listeners.add(f) },
    removeEventListener: (t, f) => listeners.delete(f),
    show() { this.hidden = false; for (const f of listeners) f() },
    listeners
  }
}

test('localRoute: nothing to add when the local gateway is routed, else pin its connection id', () => {
  const gws = [{ id: 'hub', kind: 'remote' }, { id: 'mbp', kind: 'local' }]
  assert.deepEqual(m.localRoute(gws, 'mbp'), {})
  assert.deepEqual(m.localRoute(gws, 'hub'), { connectionId: 'mbp' })
  assert.deepEqual(m.localRoute([{ id: 'hub', kind: 'remote' }], 'hub'), { connectionId: 'local' })
  assert.deepEqual(m.localRoute(null, null), {})
  assert.deepEqual(m.localRoute([], 'local'), {})
})

test('with hermes-hub active, /status and the action go to the local connection', async () => {
  host.state.gateways = { get: () => [{ id: 'hub', kind: 'remote' }, { id: 'local', kind: 'local' }] }
  host.activeConnectionId = () => 'hub'
  host.notify = () => {}
  const calls = []
  const store = m.createStore({ rest: async (path, opts = {}) => { calls.push({ path, ...opts }); return { ok: true } } })
  await store.refresh()
  await store.restart()
  await tick()
  assert.deepEqual(calls.slice(0, 2).map(c => [c.path, c.connectionId]), [['/status', 'local'], ['/restart', 'local']])
  assert.ok(calls.every(c => c.connectionId === 'local'), 'every call, including the re-read after the action, is pinned')
  host.activeConnectionId = () => 'local'
  calls.length = 0
  await store.refresh()
  assert.deepEqual(calls, [{ path: '/status' }])
})

test('register: 30000 ms poll skipped while hidden, one read when shown, listener disposed', async () => {
  const doc = globalThis.document = fakeDocument()
  host.activeConnectionId = () => 'local'
  try {
    const intervals = [], disposers = []
    let reads = 0
    m.default.register({
      rest: async () => { reads++; return {} },
      setInterval: (fn, ms) => intervals.push({ fn, ms }),
      register() {},
      onDispose: fn => disposers.push(fn)
    })
    await tick()
    assert.equal(reads, 1)
    assert.deepEqual(intervals.map(i => i.ms), [30000])
    doc.hidden = true
    intervals[0].fn()
    await tick()
    assert.equal(reads, 1, 'no read while hidden')
    doc.show()
    await tick()
    assert.equal(reads, 2, 'shown again: one read')
    intervals[0].fn()
    await tick()
    assert.equal(reads, 3, 'visible: the poll reads')
    for (const d of disposers) d()
    assert.equal(doc.listeners.size, 0)
  } finally { delete globalThis.document }
})

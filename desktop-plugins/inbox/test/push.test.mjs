// node --test: push-driven refresh (plugin.inbox.changed), per-connection reads, hidden pause, 5-minute safety poll.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'

register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,' + encodeURIComponent(
    'export const host = globalThis.__host = { state: {} };' +
    'export const atom = v => { let x = v; return { get: () => x, set: n => { x = n } } };' +
    'export const COMPOSER_AREAS = { top: "composer.top" }; export const SIDEBAR_SECTIONS_AREA = "sidebar.sections"; export const TRANSCRIPT_DIRECTIVE_AREA = "transcript.directives";' +
    'export const SidebarSection = () => null; export const SidebarSectionRow = () => null; export const useValue = a => a.get();'), shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useState=v=>[v,()=>{}];export const useEffect=()=>{};export const jsx=(type,props,key)=>({type,props,key});export const jsxs=(type,props,key)=>({type,props,key})', shortCircuit: true }
  return next(spec, ctx)
}`))
const m = await import('../plugin.js')
const host = globalThis.__host
host.activeConnectionId = () => 'local'
host.state.gateways = { get: () => [{ id: 'local', kind: 'local' }, { id: 'mini', kind: 'ssh' }] }
const sleep = ms => new Promise(r => setTimeout(r, ms))

function fakeDocument() {
  const listeners = new Set()
  return {
    hidden: false,
    addEventListener: (t, f) => { if (t === 'visibilitychange') listeners.add(f) },
    removeEventListener: (t, f) => listeners.delete(f),
    show() { this.hidden = false; for (const f of listeners) f() },
    hide() { this.hidden = true; for (const f of listeners) f() },
    listeners
  }
}

function setup() {
  const calls = []
  const items = { local: [{ session_id: 'a', updated_at: 1 }], mini: [{ session_id: 'b', updated_at: 2 }] }
  const ctx = {
    storage: { get: (k, f) => f, set() {} },
    hubRest: async () => null,
    hubGatewayName: () => '',
    rest: async (path, opts = {}) => {
      const gw = opts.connectionId || 'local'
      calls.push(gw)
      return { items: items[gw], profile: 'default', feed: `feed-${gw}` }
    }
  }
  return { ctx, calls, items, store: m.createStore(ctx) }
}

test('a push re-reads only the connection whose /items carried that feed; a burst is one read', async () => {
  globalThis.document = fakeDocument()
  try {
    const { store, calls, items } = setup()
    await store.refresh()
    assert.deepEqual(calls.sort(), ['local', 'mini'])
    calls.length = 0
    items.mini = [{ session_id: 'b', updated_at: 2 }, { session_id: 'c', updated_at: 9, unread: true }]
    store.onChanged({ type: m.CHANGED_EVENT, payload: { feed: 'feed-mini', seq: 1 } })
    store.onChanged({ type: m.CHANGED_EVENT, payload: { feed: 'feed-mini', seq: 2 } })
    await sleep(500)
    assert.deepEqual(calls, ['mini'], 'coalesced into one read of that connection only')
    assert.deepEqual(store.$items.get().map(i => `${i.gateway}:${i.session_id}`), ['mini:c', 'mini:b', 'local:a'])
  } finally { delete globalThis.document }
})

test('a push from an unknown feed reads every connection', async () => {
  globalThis.document = fakeDocument()
  try {
    const { store, calls } = setup()
    store.onChanged({ payload: { feed: 'feed-elsewhere', seq: 1 } })
    await sleep(500)
    assert.deepEqual(calls.sort(), ['local', 'mini'])
  } finally { delete globalThis.document }
})

test('while hidden a push reads nothing; showing the window runs one full pass', async () => {
  const doc = globalThis.document = fakeDocument()
  try {
    const { store, calls } = setup()
    await store.refresh()
    calls.length = 0
    doc.hidden = true
    store.onChanged({ payload: { feed: 'feed-mini', seq: 3 } })
    await sleep(500)
    assert.deepEqual(calls, [])
    doc.hidden = false
    await store.onShow()
    assert.deepEqual(calls.sort(), ['local', 'mini'])
  } finally { delete globalThis.document }
})

test('register: subscribes to the push, polls every 5 min only while visible, refreshes when shown', async () => {
  const doc = globalThis.document = fakeDocument()
  try {
    const intervals = []
    const subs = []
    const disposers = []
    let reads = 0
    m.default.register({
      storage: { get: (k, f) => f, set() {} },
      hubRest: async () => null,
      rest: async () => { reads++; return { items: [], feed: 'f' } },
      setInterval: (fn, ms) => intervals.push({ fn, ms }),
      onEvent: (name, fn) => { subs.push(name); return () => subs.splice(subs.indexOf(name), 1) },
      register() {},
      onDispose: fn => disposers.push(fn)
    })
    await sleep(10)
    assert.deepEqual(subs, [m.CHANGED_EVENT])
    assert.deepEqual(intervals.map(i => i.ms), [5 * 60_000])
    const base = reads
    doc.hidden = true
    intervals[0].fn()
    await sleep(10)
    assert.equal(reads, base, 'no poll while hidden')
    doc.show()
    await sleep(10)
    assert.ok(reads > base, 'shown again: one refresh')
    for (const d of disposers) d()
    assert.deepEqual(subs, [])
    assert.equal(doc.listeners.size, 0)
  } finally { delete globalThis.document }
})

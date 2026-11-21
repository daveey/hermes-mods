// node --test: account-usage reads on gateway pushes (plugin.account-usage.changed), a 5-minute safety poll,
// nothing while the window is hidden, and once when shown again.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'

register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,' + encodeURIComponent(
    'export const host = globalThis.__host = { state: {} };' +
    'export const atom = v => { let x = v; return { get: () => x, set: n => { x = n } } };' +
    'export const cn = (...a) => a.join(" "); export const Popover = () => null; export const PopoverContent = () => null; export const PopoverTrigger = () => null;' +
    'export const STATUSBAR_AREAS = { right: "statusbar.right" }; export const useValue = a => a.get();'), shortCircuit: true }
  if (spec === 'react' || spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const useState=v=>[v,()=>{}];export const useEffect=()=>{};export const useRef=v=>({current:v});export const jsx=(type,props,key)=>({type,props,key});export const jsxs=(type,props,key)=>({type,props,key})', shortCircuit: true }
  return next(spec, ctx)
}`))
const m = await import('../plugin.js')
const host = globalThis.__host

function fakeDocument() {
  const listeners = new Set()
  return {
    hidden: false,
    head: { append() {} },
    createElement: () => ({ remove() {} }),
    addEventListener: (t, f) => { if (t === 'visibilitychange') listeners.add(f) },
    removeEventListener: (t, f) => listeners.delete(f),
    show() { this.hidden = false; for (const f of listeners) f() },
    listeners
  }
}

function clockedRefresher() {
  const t = { now: 0, timers: [] }
  let reads = 0
  const r = m.createRefresher(() => { reads++ }, {
    now: () => t.now,
    schedule: (fn, ms) => { const h = { fn, at: t.now + ms }; t.timers.push(h); return h },
    cancel: h => { t.timers = t.timers.filter(x => x !== h) }
  })
  const advance = ms => {
    t.now += ms
    for (const h of [...t.timers]) if (h.at <= t.now) { t.timers = t.timers.filter(x => x !== h); h.fn() }
  }
  return { r, t, advance, reads: () => reads }
}

test('a push reads at once; a burst inside EVENT_GAP_MS is one more read at the end of the gap', () => {
  globalThis.document = fakeDocument()
  try {
    const { r, advance, reads } = clockedRefresher()
    r.onChanged()
    assert.equal(reads(), 1)
    r.onChanged(); r.onChanged(); r.onChanged()
    assert.equal(reads(), 1)
    advance(m.EVENT_GAP_MS)
    assert.equal(reads(), 2)
    advance(m.EVENT_GAP_MS)
    assert.equal(reads(), 2, 'nothing pending: no extra read')
  } finally { delete globalThis.document }
})

test('hidden: pushes and the safety tick read nothing; shown reads once if a push came meanwhile', () => {
  const doc = globalThis.document = fakeDocument()
  try {
    const { r, advance, reads } = clockedRefresher()
    r.tick()
    assert.equal(reads(), 1)
    doc.hidden = true
    r.onChanged()
    r.tick()
    advance(10 * 60_000)
    assert.equal(reads(), 1)
    assert.equal(r.isStale(), true)
    doc.hidden = false
    r.onShow()
    assert.equal(reads(), 2)
    r.onShow()
    assert.equal(reads(), 2, 'alt-tab right after a read costs nothing')
  } finally { delete globalThis.document }
})

test('shown with no push meanwhile reads only when the last read is older than EVENT_GAP_MS', () => {
  globalThis.document = fakeDocument()
  try {
    const { r, advance, reads } = clockedRefresher()
    r.tick()
    advance(m.EVENT_GAP_MS - 1)
    r.onShow()
    assert.equal(reads(), 1)
    advance(1)
    r.onShow()
    assert.equal(reads(), 2)
  } finally { delete globalThis.document }
})

test('register: subscribes to the push, 5-minute safety poll, visibility listener, all disposed', async () => {
  const doc = globalThis.document = fakeDocument()
  host.state = { profile: { get: () => 'default', subscribe: () => () => {} } }
  let rpc = 0
  host.request = async () => { rpc++; return { providers: [] } }
  try {
    const intervals = [], subs = [], disposers = []
    m.default.register({
      rest: async () => ({}),
      setTimeout() {},
      setInterval: (fn, ms) => intervals.push({ fn, ms }),
      onEvent: (name, fn) => { subs.push({ name, fn }); return () => subs.splice(0) },
      register() {},
      onDispose: fn => disposers.push(fn)
    })
    await new Promise(r => setTimeout(r, 5))
    assert.deepEqual(intervals.map(i => i.ms), [5 * 60_000])
    assert.deepEqual(subs.map(s => s.name), [m.CHANGED_EVENT])
    assert.equal(doc.listeners.size, 1)
    const base = rpc
    doc.hidden = true
    intervals[0].fn()
    subs[0].fn({ payload: { provider: 'anthropic' } })
    await new Promise(r => setTimeout(r, 5))
    assert.equal(rpc, base, 'no model.options while hidden')
    doc.show()
    await new Promise(r => setTimeout(r, 5))
    assert.equal(rpc, base + 1, 'shown again with a pending push: one read')
    for (const d of disposers) d()
    assert.equal(subs.length, 0)
    assert.equal(doc.listeners.size, 0)
  } finally { delete globalThis.document; delete host.request }
})

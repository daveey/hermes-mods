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
    const end = t.now + ms
    for (;;) {
      const due = t.timers.filter(h => h.at <= end).sort((a, b) => a.at - b.at)[0]
      if (!due) break
      t.timers = t.timers.filter(x => x !== due)
      t.now = due.at
      due.fn()
    }
    t.now = end
  }
  return { r, t, advance, reads: () => reads }
}

test('a push reads at once plus one follow-up FOLLOWUP_MS later; a burst inside EVENT_GAP_MS is one more read', () => {
  globalThis.document = fakeDocument()
  try {
    const { r, advance, reads } = clockedRefresher()
    r.onChanged()
    assert.equal(reads(), 1)
    r.onChanged(); r.onChanged(); r.onChanged()
    assert.equal(reads(), 1)
    advance(m.FOLLOWUP_MS)
    assert.equal(reads(), 2, 'the follow-up shows the provider fetch the first read started')
    advance(m.EVENT_GAP_MS - m.FOLLOWUP_MS)
    assert.equal(reads(), 3, 'the burst: one read at the end of the gap')
    advance(m.FOLLOWUP_MS)
    assert.equal(reads(), 4, 'and its follow-up')
    advance(10 * m.EVENT_GAP_MS)
    assert.equal(reads(), 4, 'nothing pending: no extra read')
  } finally { delete globalThis.document }
})

test('the safety tick has no follow-up; a follow-up due while hidden is skipped', () => {
  const doc = globalThis.document = fakeDocument()
  try {
    const { r, advance, reads } = clockedRefresher()
    r.tick()
    advance(m.FOLLOWUP_MS * 2)
    assert.equal(reads(), 1)
    advance(m.EVENT_GAP_MS)
    r.onChanged()
    assert.equal(reads(), 2)
    doc.hidden = true
    advance(m.FOLLOWUP_MS)
    assert.equal(reads(), 2)
  } finally { delete globalThis.document }
})

test('hidden: pushes and the safety tick read nothing; shown reads once (+ follow-up) if a push came meanwhile', () => {
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
    advance(m.FOLLOWUP_MS)
    assert.equal(reads(), 3)
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

test('eventForActive: untagged = local primary; a push from another connection is not for the active one', () => {
  const gws = [{ id: 'mbp', kind: 'local' }, { id: 'hub', kind: 'remote' }]
  assert.equal(m.eventForActive({}, null, gws), true)
  assert.equal(m.eventForActive({}, 'mbp', gws), true)
  assert.equal(m.eventForActive({ connectionId: 'mbp' }, null, gws), true)
  assert.equal(m.eventForActive({ connectionId: 'hub' }, 'hub', gws), true)
  assert.equal(m.eventForActive({ connectionId: 'hub' }, 'mbp', gws), false)
  assert.equal(m.eventForActive({}, 'hub', gws), false)
  assert.equal(m.eventForActive({ connectionId: 'local' }, undefined, undefined), true)
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
    host.state.connectionId = { get: () => 'local', subscribe: () => () => {} }
    const before = rpc
    subs[0].fn({ connectionId: 'hub', payload: { provider: 'anthropic' } })
    await new Promise(r => setTimeout(r, 5))
    assert.equal(rpc, before, 'a push from a non-active connection costs no read')
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

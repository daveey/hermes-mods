// node --test: hub-status chip — routing, state/counter, unreachable spans, strip slots, timeline segments,
// and the register() wiring (polls the hub connection, goes red after two misses, recovers).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'

register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return { url: 'data:text/javascript,' + encodeURIComponent(
    'export const host = globalThis.__host = { state: {} };' +
    'export const atom = v => { let x = v; return { get: () => x, set: n => { x = n } } };' +
    'export const cn = (...a) => a.filter(Boolean).join(" ");' +
    'export const Popover = "Popover", PopoverTrigger = "PopoverTrigger", PopoverContent = "PopoverContent";' +
    'export const STATUSBAR_AREAS = { right: "statusbar.right" }; export const useValue = a => a.get();'), shortCircuit: true }
  if (spec === 'react/jsx-runtime') return { url: 'data:text/javascript,export const jsx=(type,props,key)=>({type,props,key});export const jsxs=(type,props,key)=>({type,props,key})', shortCircuit: true }
  return next(spec, ctx)
}`))
const m = await import('../plugin.js')
const host = globalThis.__host
const tick = () => new Promise(r => setTimeout(r, 5))
const H = 3_600_000
const NOW = Date.UTC(2026, 10, 26, 20, 0, 0)

test('hubConnection: hermes-hub, else a remote whose name says hub, else none', () => {
  assert.equal(m.hubConnection([{ id: 'local', kind: 'local' }, { id: 'mac-mini', kind: 'ssh' }, { id: 'hermes-hub', kind: 'ssh' }]), 'hermes-hub')
  assert.equal(m.hubConnection([{ id: 'x1', kind: 'ssh', label: 'hub (EC2)' }]), 'x1')
  assert.equal(m.hubConnection([{ id: 'local', kind: 'local', label: 'hub' }, { id: 'mac-mini', kind: 'ssh' }]), null)
  assert.equal(m.hubConnection(null), null)
})

test('counters: span for up/down, running clock for restarting', () => {
  assert.equal(m.fmtSpan(45_000), '45s')
  assert.equal(m.fmtSpan(12 * 60_000), '12m')
  assert.equal(m.fmtSpan(6 * H + 36 * 60_000), '6h 36m')
  assert.equal(m.fmtSpan(76 * H), '3d 4h')
  assert.equal(m.chipCounter('restarting', 42_000), '0:42')
  assert.equal(m.chipCounter('restarting', 12 * 60_000 + 5_000), '12:05')
  assert.equal(m.chipCounter('up', 42_000), '42s')
})

test('deriveState: hub answer wins; two misses → unreachable since the last good poll', () => {
  const status = { state: 'up', since: (NOW - 2 * H) / 1000, reason: 'running' }
  assert.deepEqual(m.deriveState({ status, fails: 0, lastOkAt: NOW }, NOW), { state: 'up', since: NOW - 2 * H, reason: 'running', unreachable: false })
  // one miss after a good answer: keep showing the last answer
  assert.equal(m.deriveState({ status, fails: 1, lastOkAt: NOW - 10_000 }, NOW).state, 'up')
  const d = m.deriveState({ status, fails: 2, lastOkAt: NOW - 20_000, firstFailAt: NOW - 10_000 }, NOW)
  assert.deepEqual([d.state, d.since, d.unreachable], ['down', NOW - 20_000, true])
  // never reached: one miss is not an outage, two are (red since the first miss)
  assert.equal(m.deriveState({ status: null, fails: 1, lastOkAt: 0, firstFailAt: NOW - 5000 }, NOW), null)
  assert.equal(m.deriveState({ status: null, fails: 2, lastOkAt: 0, firstFailAt: NOW - 5000 }, NOW).since, NOW - 5000)
  assert.equal(m.deriveState({ status: null, fails: 0 }, NOW), null)
  assert.equal(m.deriveState({ status: { state: 'restarting', since: (NOW - 42_000) / 1000 }, fails: 0 }, NOW).state, 'restarting')
  assert.equal(m.deriveState({ status: { state: 'weird' }, fails: 0 }, NOW).state, 'down')
})

test('updateSpans opens once, closes on recovery, drops spans older than 30 days', () => {
  let s = m.updateSpans([], true, NOW - 20_000, NOW)
  s = m.updateSpans(s, true, NOW - 20_000, NOW + 10_000)
  assert.deepEqual(s, [{ start: NOW - 20_000, end: null }])
  s = m.updateSpans(s, false, 0, NOW + 30_000)
  assert.deepEqual(s, [{ start: NOW - 20_000, end: NOW + 30_000 }])
  const old = [{ start: NOW - 40 * 24 * H, end: NOW - 39 * 24 * H }, ...s]
  assert.equal(m.updateSpans(old, false, 0, NOW + 40_000).length, 1)
})

test('buildSegments merges hub events with local unreachable spans; stripSlots takes the worst per slot', () => {
  const start = NOW - 24 * H
  const ev = [
    { ts: (start - H) / 1000, state: 'up', clipped: true },
    { ts: (NOW - 10 * H) / 1000, state: 'restarting', reason: 'draining' },
    { ts: (NOW - 10 * H + 20_000) / 1000, state: 'up', reason: 'running' }
  ]
  const spans = [{ start: NOW - 3 * H, end: NOW - 2.5 * H }]
  const segs = m.buildSegments(ev, spans, start, NOW)
  assert.deepEqual(segs.map(s => s.state), ['up', 'restarting', 'up', 'down', 'up'])
  assert.equal(segs[0].start, start)
  assert.equal(segs.at(-1).end, NOW)
  assert.equal(segs[3].reason, 'unreachable from this Mac')
  const slots = m.stripSlots(segs, start, NOW)
  assert.equal(slots.length, 12)
  assert.equal(slots[7], 'restarting') // NOW-10h falls in slot 7 (14h..16h after start)
  assert.equal(slots[10], 'down') // NOW-3h..NOW-2.5h
  assert.equal(slots.filter(x => x === 'up').length, 10)
  const sum = m.summarize(segs, start, NOW)
  assert.equal(sum.restarts, 1)
  assert.equal(sum.outages, 1)
  assert.ok(sum.pct > 97 && sum.pct < 98)
  // no history at all: blank slots, no uptime figure
  assert.deepEqual(m.stripSlots([], start, NOW), Array(12).fill('none'))
  assert.equal(m.fmtPct(m.summarize([], start, NOW).pct), '—')
})

test('chipModel: newest slot always shows the live state; label and tooltip', () => {
  const poll = { status: { state: 'restarting', since: (NOW - 42_000) / 1000, reason: 'draining' }, fails: 0, lastOkAt: NOW }
  const cm = m.chipModel(poll, { events: [] }, [], NOW)
  assert.equal(cm.slots[11], 'restarting')
  assert.equal(cm.counter, '0:42')
  assert.match(cm.title, /^Hub restarting 0:42\ndraining · since /)
  const up = m.chipModel({ status: { state: 'up', since: (NOW - 6.6 * H) / 1000 }, fails: 0, lastOkAt: NOW }, null, [], NOW)
  assert.equal(up.label, 'Hub up 6h 36m')
})

test('register: polls the hub connection, red after two misses, green again on recovery', async () => {
  const calls = []
  let fail = false
  const timers = []
  const store = {}
  const regs = []
  host.state.gateways = { get: () => [{ id: 'local', kind: 'local' }, { id: 'hermes-hub', kind: 'ssh' }] }
  const ctx = {
    rest: async (path, opts) => {
      calls.push([path, opts.connectionId, opts.passive])
      if (fail) throw new Error('ECONNREFUSED')
      if (path === '/status') return { state: 'up', since: Date.now() / 1000 - 60, reason: 'running' }
      return { events: [] }
    },
    setInterval: (fn, ms) => { timers.push([fn, ms]); return () => {} },
    storage: { get: (k, f) => (k in store ? store[k] : f), set: (k, v) => { store[k] = v } },
    register: c => regs.push(c),
    onDispose: () => {}
  }
  m.default.register(ctx)
  await tick()
  assert.equal(regs.length, 1)
  assert.equal(regs[0].id, 'chip')
  // titlebar, right of llm-split (order 50): just left of the settings gear
  assert.equal(regs[0].area, 'titleBar.tools.right')
  assert.equal(regs[0].order, 60)
  assert.ok(regs[0].order > 50)
  assert.equal(regs[0].data.span, m.TITLEBAR_SPAN)
  assert.deepEqual(calls[0], ['/status', 'hermes-hub', true])
  assert.ok(calls.some(c => c[0] === '/events?range=24h'))
  const poll = timers.find(t => t[1] === m.POLL_MS)[0]
  const chip = () => regs[0].data.icon.type ? regs[0].data.icon : null
  assert.ok(chip())
  const el = () => {
    const tree = regs[0].data.icon
    const out = tree.type({ ...tree.props })
    return out.props.children[0].props.children // trigger button
  }
  assert.equal(el().props['data-state'], 'up')
  fail = true
  poll(); await tick()
  assert.equal(el().props['data-state'], 'up') // one miss: not yet
  poll(); await tick()
  assert.equal(el().props['data-state'], 'down')
  assert.equal(store.spans.length, 1)
  assert.equal(store.spans[0].end, null)
  fail = false
  poll(); await tick()
  assert.equal(el().props['data-state'], 'up')
  assert.ok(store.spans[0].end > 0)
})

test('the strip keeps 24 h hub history while the popover shows 7 d', () => {
  const day = { events: [
    { ts: (NOW - 25 * H) / 1000, state: 'up' },
    { ts: (NOW - 3 * H) / 1000, state: 'down', reason: 'x' },
    { ts: (NOW - 2.5 * H) / 1000, state: 'up' }] }
  const cm = m.chipModel({ status: { state: 'up', since: (NOW - 2.5 * H) / 1000 }, fails: 0, lastOkAt: NOW }, day, [], NOW)
  assert.equal(cm.slots[10], 'down')
})

test('one down spell whose reason changes is one outage', () => {
  const start = NOW - 24 * H
  const ev = [
    { ts: (start - H) / 1000, state: 'up' },
    { ts: (NOW - 4 * H) / 1000, state: 'down', reason: 'restart did not finish' },
    { ts: (NOW - 3.5 * H) / 1000, state: 'down', reason: 'startup_failed' },
    { ts: (NOW - 3 * H) / 1000, state: 'up' }]
  const segs = m.buildSegments(ev, [{ start: NOW - 3.2 * H, end: NOW - 3 * H }], start, NOW)
  assert.deepEqual(segs.map(s => s.state), ['up', 'down', 'up'])
  assert.equal(segs[1].reason, 'restart did not finish → startup_failed → unreachable from this Mac')
  assert.equal(m.summarize(segs, start, NOW).outages, 1)
})

test("today's 7 d row keeps the rest of the day as empty future space", () => {
  const dayStart = NOW - 20 * H, dayEnd = NOW + 4 * H
  const parts = m.bandParts([{ start: dayStart, end: NOW, state: 'up' }], dayStart, dayEnd, NOW)
  assert.deepEqual(parts.map(p => [p.state, (p.end - p.start) / H]), [['up', 20], ['future', 4]])
  assert.deepEqual(m.bandParts([{ start: dayStart, end: dayEnd, state: 'up' }], dayStart, dayEnd, dayEnd).map(p => p.state), ['up'])
})

test('minute refresh keeps both the popover range and the strip current', async () => {
  host.state.gateways = { get: () => [{ id: 'hermes-hub', kind: 'ssh' }] }
  const calls = []
  const store = m.createStore({ rest: async p => { calls.push(p); return { events: [] } }, storage: { get: (k, f) => f, set: () => {} } })
  await store.loadEvents('7d'); calls.length = 0
  await m.refreshHistory(store); await tick()
  assert.deepEqual(calls.sort(), ['/events?range=24h', '/events?range=7d'])
  await store.loadEvents('24h'); calls.length = 0
  await m.refreshHistory(store); await tick()
  assert.deepEqual(calls, ['/events?range=24h'])
  assert.ok(store.$day.get())
})

test('no hub connection: no requests, no chip', async () => {
  host.state.gateways = { get: () => [{ id: 'local', kind: 'local' }] }
  const calls = []
  const regs = []
  m.default.register({
    rest: async p => { calls.push(p); return {} },
    setInterval: () => () => {},
    storage: { get: (k, f) => f, set: () => {} },
    register: c => regs.push(c)
  })
  await tick()
  assert.equal(calls.length, 0)
  const tree = regs[0].data.icon
  assert.equal(tree.type({ ...tree.props }), null)
})

test('titlebar: keyboard select toggles the popover, an outside click closes it, inside clicks do not', () => {
  const loads = []
  const tool = m.titlebarTool({ loadEvents: () => loads.push(1) })
  const open = () => m.isOpenForTest()
  tool.onSelect({ detail: 0 })
  assert.equal(open(), true)
  assert.equal(loads.length, 1)
  const at = sel => ({ closest: s => (s === sel ? {} : null) })
  m.closeOnOutsideClick(at('[data-slot="hub-status-chip"]'))
  assert.equal(open(), true)
  m.closeOnOutsideClick(at('.hs-panel'))
  assert.equal(open(), true)
  m.closeOnOutsideClick(at('.elsewhere'))
  assert.equal(open(), false)
  // a mouse click on the chip itself (detail 1, target inside) is left to the Radix trigger
  tool.onSelect({ detail: 1, target: {}, currentTarget: {} })
  assert.equal(open(), false)
})

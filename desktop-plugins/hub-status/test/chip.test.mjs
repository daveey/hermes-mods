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

test('Hub arrow follows pending build commits, not stale or current records', () => {
  for (const state of ['ready', 'building']) {
    assert.equal(m.updatePending({ state, commits: 4 }), true)
    assert.equal(m.updateTooltip({ state, commits: 4 }), 'Update ready · 4 commits')
    assert.equal(m.updatePending({ state, commits: 0 }), false)
  }
  assert.equal(m.updatePending({ state: 'idle', commits: 4 }), false)
  assert.equal(m.updatePending(null), false)
})

test('Update now uses local API, confirms busy or unknown turns, cancel never writes', async () => {
  for (const running_turns of [3, null, 0]) {
    const calls = []
    const store = m.createVersionStore(async o => {
      calls.push(o)
      if (o.path.endsWith('/ping')) return { api: 2 }
      if (o.path.endsWith('/update')) return { ok: true }
      return { running_turns, running: 'abc1234567', force_request: { by: 'hub panel' } }
    })
    await store.updateNow()
    assert.ok(calls.every(c => c.connectionId === 'local'))
    if (running_turns !== 0) {
      assert.equal(calls.filter(c => c.method === 'POST').length, 0)
      assert.ok(store.$confirm.get() !== null)
      store.cancel()
      assert.equal(store.$confirm.get(), null)
      await store.updateNow()
      await store.apply()
    }
    assert.equal(calls.filter(c => c.method === 'POST').length, 1)
    assert.equal(store.$message.get(), 'Waiting for turns')
  }
})

test('progress preserves real deferrals and reports applying then completed sha', async () => {
  let v = { running_turns: 0, running: 'abc1234567', force_request: {} }
  const store = m.createVersionStore(async o => o.path.endsWith('/ping') ? { api: 2 } : o.method === 'POST' ? { ok: true } : v)
  await store.updateNow()
  v = { ...v, progress: { state: 'applying' } }
  await store.refresh()
  assert.equal(store.$message.get(), 'Updating')
  v = { ...v, deferral_reason: 'drain cooldown' }
  await store.refresh()
  assert.equal(store.$message.get(), 'drain cooldown')
  v = { running: 'def1234567', running_turns: 0, operation: { phase: 'updated', sha: 'def1234567' } }
  await store.refresh()
  assert.equal(store.$message.get(), 'Updated to def1234567')
})

test('current operation failure beats live force marker, older failure does not', async () => {
  let v = { running: 'abc1234567', force_request: { requested_at: 200 }, operation: { phase: 'failed', requested_at: 100, reason: 'old failure' } }
  const store = m.createVersionStore(async o => o.path.endsWith('/ping') ? { api: 2 } : v)
  await store.refresh()
  assert.equal(store.$message.get(), 'Waiting for turns')
  v.operation = { ...v.operation, requested_at: 200, reason: 'attested updater refused' }
  await store.refresh()
  assert.equal(store.$message.get(), 'attested updater refused')
})

test('terminal records always release Update now and passive polls stay passive', async () => {
  for (const operation of [{ phase: 'updated', sha: null }, { phase: 'failed', reason: 'failure' }, { phase: 'deferred', reason: 'cooldown' }]) {
    let posts = 0
    let v = { running_turns: 0, force_request: { requested_at: 1 } }
    const store = m.createVersionStore(async o => {
      if (o.method === 'POST') { posts++; return { ok: true } }
      assert.equal(o.passive, true)
      return o.path.endsWith('/ping') ? { api: 2 } : v
    })
    await store.updateNow()
    v = { running_turns: 0, operation }
    await store.refresh()
    assert.equal(store.$message.get(), operation.phase === 'updated' ? 'Update completed; revision unavailable' : operation.reason)
    await store.updateNow()
    assert.equal(posts, 2)
  }
})

test('activation clears obsolete status messages', async () => {
  let ready = false
  const store = m.createVersionStore(async o => o.path.endsWith('/ping') ? { api: ready ? 2 : 1 } : {})
  await store.refresh()
  assert.match(store.$message.get(), /activation/)
  ready = true
  await store.refresh()
  assert.equal(store.$message.get(), '')
})

test('old backend cannot receive the new update mutation', async () => {
  const calls = []
  const store = m.createVersionStore(async o => { calls.push(o); return { api: 1 } })
  await store.updateNow()
  assert.equal(calls.length, 1)
  assert.match(store.$message.get(), /activation/)
})

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

test('titlebar keyboard: capture runs before onSelect on the tool button, and Enter closes an open popover', () => {
  const tool = m.titlebarTool({ loadEvents: () => {} })
  const button = { closest: s => (s === '.hs-tool' ? button : null) }
  const press = () => { m.closeOnOutsideClick(button); tool.onSelect({ detail: 0, target: button, currentTarget: button }) }
  press()
  assert.equal(m.isOpenForTest(), true)
  press()
  assert.equal(m.isOpenForTest(), false)
})

const findAll = (node, pred, out = []) => {
  if (!node || typeof node !== 'object') return out
  if (Array.isArray(node)) { node.forEach(n => findAll(n, pred, out)); return out }
  if (pred(node)) out.push(node)
  findAll(node.props?.children, pred, out)
  return out
}

test('updateProgress maps updater stages onto a monotonic bar', () => {
  assert.deepEqual(m.updateProgress(null), { phase: 'idle', pct: 0, label: 'Update now', indeterminate: false })
  assert.equal(m.updateProgress({}, true).label, 'Starting…')
  const waitNoCap = m.updateProgress({ force_request: { requested_at: 1 } })
  assert.equal(waitNoCap.phase, 'active'); assert.equal(waitNoCap.indeterminate, true)
  const half = m.updateProgress({ progress: { state: 'draining', state_elapsed_s: 50, cap_s: 100 } })
  const full = m.updateProgress({ progress: { state: 'draining', state_elapsed_s: 500, cap_s: 100 } })
  assert.equal(half.indeterminate, false); assert.ok(half.pct > 8 && half.pct < full.pct && full.pct === 45)
  const installing = m.updateProgress({ progress: { state: 'applying' } })
  const restarting = m.updateProgress({ operation: { phase: 'restarting', requested_at: 1 } })
  assert.equal(installing.label, 'Installing…'); assert.equal(restarting.label, 'Restarting…')
  assert.ok(full.pct < installing.pct && installing.pct < restarting.pct && restarting.pct < 100)
  assert.deepEqual(m.updateProgress({ operation: { phase: 'updated', sha: 'def1234567abc' } }),
    { phase: 'done', pct: 100, label: 'Updated to def1234567', indeterminate: false })
  // A newer batch arriving after the update: offer it again rather than showing done.
  assert.equal(m.updateProgress({ operation: { phase: 'updated', sha: 'x' }, changes: [{}] }).phase, 'idle')
  assert.equal(m.updateProgress({ operation: { phase: 'failed', reason: 'r' } }).label, 'Retry update')
  // An older failure does not hide a live force request.
  assert.equal(m.updateProgress({ force_request: { requested_at: 2 }, operation: { phase: 'failed', requested_at: 1 } }).phase, 'active')
})

test('What is in the update lists every commit in one scroll box; button carries the bar', async () => {
  const changes = Array.from({ length: 37 }, (_, i) => ({ sha: `sha${i}`.padEnd(12, '0'), subject: `c${i}`, at: '2026-11-27T23:44:00Z' }))
  let v = { running_turns: 0, changes }
  const store = m.createVersionStore(async o => o.path.endsWith('/ping') ? { api: 2 } : o.method === 'POST' ? { ok: true } : v)
  await store.refresh()
  let tree = m.VersionSection({ store })
  const box = findAll(tree, n => n.props?.className === 'hs-changes')
  assert.equal(box.length, 1)
  assert.equal(findAll(box[0], n => n.props?.className === 'hs-commit').length, 37)
  assert.equal(findAll(tree, n => n.type === 'details').length, 0)
  let btn = findAll(tree, n => n.props?.className === 'hs-upd')[0]
  assert.equal(btn.props.disabled, false)
  assert.equal(findAll(btn, n => n.props?.className === 'hs-upd-label')[0].props.children, 'Update now')
  assert.equal(store.active(), false)
  v = { ...v, progress: { state: 'draining', state_elapsed_s: 30, cap_s: 60 }, force_request: { requested_at: 1 } }
  await store.updateNow()
  assert.equal(store.active(), true)
  tree = m.VersionSection({ store })
  btn = findAll(tree, n => n.props?.className === 'hs-upd')[0]
  const fill = findAll(btn, n => n.props?.role === 'progressbar')[0]
  assert.equal(btn.props.disabled, true)
  assert.equal(fill.props.style.width, `${m.updateProgress(v).pct}%`)
  assert.equal(fill.props['aria-valuenow'], m.updateProgress(v).pct)
  // The stage message duplicates the button label, so no separate status line.
  assert.equal(findAll(tree, n => n.props?.role === 'status').length, 0)
  v = { running_turns: 0, operation: { phase: 'failed', reason: 'attested updater refused' } }
  await store.refresh()
  tree = m.VersionSection({ store })
  assert.equal(findAll(tree, n => n.props?.role === 'status')[0].props.children, 'attested updater refused')
  assert.equal(store.active(), false)
})

// Lanes panel: target math under a draft, drift wording, cost coverage, and the rendered panel
// (unconfigured reason, slider + pause produce the right PUTs, window switch refetches).
import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount as rawMount, button, React, host } from './sdk-mock.mjs'

const P = await loadPlugin()

// What the Desktop's task pill does on click: true = nobody took it over, so the pill opens the board.
const pillOpensBoard = () => window.dispatchEvent(new window.Event(P.KANBAN_CHIP_EVENT, { cancelable: true }))

// Every mounted view is closed after its test, pass or fail: a leaked panel keeps its poll timer and its DOM.
const open = []
async function mount(...args) { const view = await rawMount(...args); open.push(view); return view }
test.afterEach(async () => {
  for (const view of open.splice(0)) await view.close().catch(() => {})
  localStorage.clear()
  host.routes = []
  host.navigated.length = 0
})

function lane(over = {}) {
  return { share: 10, paused: false, gated: false, admit_per_day: null, admitted_today: 0, describe: '', target: 0, actual: 0,
    run_seconds: 0, runs: 0, running: 0, ready: 0, held: 0, open: 0, done: 0, oldest_ready_s: null, cost_usd: 0, tokens: 0, usage_runs: 0, ...over }
}

function snap(over = {}) {
  return {
    configured: true, window_hours: 6, scope: 'fleet', planner: { notes: [{ lane: 'product', text: 'nothing ready' }] },
    lanes: {
      product: lane({ share: 30, target: 0.5, actual: 0.03, open: 18 }),
      infra: lane({ share: 30, target: 0.5, actual: 0.97, gated: true, admit_per_day: 50, admitted_today: 12, ready: 120, held: 40, open: 253, running: 9,
        run_seconds: 3600 * 250, runs: 400, done: 60, cost_usd: 47.651, tokens: 12_500_000, usage_runs: 140, oldest_ready_s: 7200 })
    },
    ...over
  }
}

test('targets follow the draft, and a paused lane gets none', () => {
  const base = P.rows(snap())
  assert.deepEqual(base.map((r) => [r.name, r.target]), [['product', 0.5], ['infra', 0.5]])
  const drafted = P.rows(snap(), { product: { share: 90 } })
  assert.equal(drafted[0].target, 0.75)
  assert.equal(drafted[0].dirty, true)
  const paused = P.rows(snap(), { infra: { paused: true } })
  assert.deepEqual(paused.map((r) => r.target), [1, 0])
})

test('drift says why a lane is off target', () => {
  const [product, infra] = P.rows(snap())
  assert.equal(P.drift(product), 'under: nothing ready')
  assert.equal(P.drift({ ...product, ready: 4 }), 'under: waiting for slots')
  assert.equal(P.drift(infra), 'over: using idle slots')
  assert.equal(P.drift({ ...product, actual: 0.45 }), '')
  assert.equal(P.drift({ ...infra, paused: true, target: 0 }), 'paused')
})

test('cost shows coverage, and a dash when nothing reported', () => {
  const [product, infra] = P.rows(snap())
  assert.equal(P.cost(product).text, '–')
  assert.equal(P.cost(infra).text, '$47.65')
  assert.match(P.cost(infra).title, /140 of 400 runs/)
})

test('changes holds only what differs from the server', () => {
  assert.deepEqual(P.changes(snap(), { product: { share: 30 }, infra: { share: 5, paused: true } }), [['infra', { share: 5, paused: true }]])
})

test('formatters', () => {
  assert.equal(P.pct(0.347), '35%')
  assert.equal(P.compact(12_500_000), '12.5M')
  assert.equal(P.hours(3600 * 250), '250')
  assert.equal(P.age(7200), '2h')
})

function ctxWith(calls, respond) {
  return { rest: async (path, opts = {}) => { calls.push([path, opts.method || 'GET', opts.body]); return respond(path, opts) } }
}

test('unconfigured lanes show the server reason', async () => {
  const view = await mount(React.createElement(P.Panel, { snap: { configured: false, reason: 'no lanes are configured (kanban.admission.lanes)' }, windowHours: 6, onWindow() {}, onApply() {} }))
  assert.match(view.container.textContent, /no lanes are configured/)
  await view.close()
  const shadow = await mount(React.createElement(P.Panel, { snap: snap({ shadow: true }), windowHours: 6, onWindow() {}, onApply() {} }))
  assert.match(shadow.container.textContent, /Shadow mode: reporting only/)
  await shadow.close()
})

test('slider and pause become one PUT per changed lane; Reset drops the draft', async () => {
  const applied = []
  const view = await mount(React.createElement(P.Panel, { snap: snap(), windowHours: 6, onWindow() {}, onApply: (p) => { applied.push(p); return false } }))
  assert.match(view.container.textContent, /gated · 12\/50 admitted today/)
  assert.match(view.container.textContent, /120 ready · oldest 2h/)
  assert.match(view.container.textContent, /product: nothing ready/)
  assert.equal(button('Apply').disabled, true)
  const slider = view.container.querySelector('[data-lane="product"] input[type=range]')
  await React.act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(slider, '90')
    slider.dispatchEvent(new window.Event('input', { bubbles: true }))
  })
  await view.flush()
  assert.match(view.container.querySelector('[data-lane="product"]').textContent, /75% target/)
  await view.click(view.container.querySelector('[data-lane="infra"] .hermes-lanes-volume button'))
  await view.click(button('Apply'))
  assert.deepEqual(applied, [[['product', { share: 90 }], ['infra', { paused: true }]]])
  assert.equal(button('Apply').disabled, false, 'a failed or unconfirmed Apply keeps the draft')
  await view.click(button('Reset'))
  assert.equal(button('Apply').disabled, true)
  await view.close()
})

test('the event opens the panel, fetches the saved window, and Apply PUTs', async () => {
  const calls = []
  const ctx = ctxWith(calls, (path, opts) => (opts.method === 'PUT' ? snap({ lanes: { ...snap().lanes, product: lane({ share: 30, paused: true }) } }) : snap()))
  const view = await mount(React.createElement(P.Lanes, { ctx }))
  assert.deepEqual(calls, [['/v1/lanes?hours=6', 'GET', undefined]]) // one probe while closed
  assert.equal(document.querySelector('[data-lane="product"]'), null, 'closed until the pill is clicked')
  let opensBoard
  await React.act(async () => { opensBoard = pillOpensBoard() })
  await view.flush()
  assert.equal(opensBoard, false, 'configured lanes: the task pill opens this panel instead of the board')
  await view.click(document.querySelector('[data-lane="product"] .hermes-lanes-volume button'))
  // A poll that brings a new snapshot object must not drop the unsaved edit.
  await React.act(async () => view.client.refetchQueries({ queryKey: ['lanes', 6] }))
  await view.flush()
  assert.equal(button('Apply').disabled, false, 'draft survives a poll')
  await view.click(button('Apply'))
  assert.deepEqual(calls.filter((c) => c[1] === 'PUT'), [['/v1/lanes/product', 'PUT', { paused: true }]])
  assert.equal(calls.at(-1)[1], 'GET', 'a successful Apply refetches')
  assert.equal(button('Apply').disabled, true, 'and clears the draft')
  await view.click(button('24h'))
  assert.equal(calls.at(-1)[0], '/v1/lanes?hours=24')
  await view.click(button('Board'))
  assert.deepEqual(host.navigated, ['/kanban'], 'the panel still leads to the board')
  assert.equal(document.querySelector('[data-lane="product"]'), null, 'and closes')
  await view.close()
  assert.equal(pillOpensBoard(), true, 'unmounted: the pill opens the board again')
})

test('without working lanes the task pill is left alone and opens the board', async () => {
  const view = await mount(React.createElement(P.Lanes, { ctx: ctxWith([], () => ({ configured: false, reason: 'this Hermes core has no lanes support yet' })) }))
  assert.equal(pillOpensBoard(), true)
  await view.close()
  const broken = await mount(React.createElement(P.Lanes, { ctx: { rest: async () => { throw Error('404') } } }))
  assert.equal(pillOpensBoard(), true)
  await broken.close()
})

test('a failed PUT names the lane, keeps the draft and shows what was written', async () => {
  const calls = []
  const ctx = ctxWith(calls, (path, opts) => {
    if (opts.method === 'PUT' && path.endsWith('/infra')) throw Error('unknown lane: infra')
    return snap()
  })
  const view = await mount(React.createElement(P.Lanes, { ctx }))
  await React.act(async () => window.dispatchEvent(new window.Event('hermes-show-lanes')))
  await view.flush()
  await view.click(document.querySelector('[data-lane="product"] .hermes-lanes-volume button'))
  await view.click(document.querySelector('[data-lane="infra"] .hermes-lanes-volume button'))
  await view.click(button('Apply'))
  assert.deepEqual(calls.filter((c) => c[1] === 'PUT').map((c) => c[0]), ['/v1/lanes/product', '/v1/lanes/infra'])
  assert.match(document.body.textContent, /infra: unknown lane: infra/)
  assert.equal(calls.at(-1)[1], 'GET', 'refetched after the failure')
  assert.equal(button('Apply').disabled, false, 'draft kept for a retry')
  await view.close()
})

test('row controls are locked while an Apply is in flight', async () => {
  let release
  const ctx = ctxWith([], (path, opts) => (opts.method === 'PUT' ? new Promise((resolve) => { release = () => resolve(snap()) }) : snap()))
  const view = await mount(React.createElement(P.Lanes, { ctx }))
  await React.act(async () => window.dispatchEvent(new window.Event('hermes-show-lanes')))
  await view.flush()
  await view.click(document.querySelector('[data-lane="product"] .hermes-lanes-volume button'))
  await view.click(button('Apply'))
  const other = document.querySelector('[data-lane="infra"] .hermes-lanes-volume button')
  assert.equal(other.disabled, true)
  assert.equal(document.querySelector('[data-lane="infra"] input[type=range]').disabled, true)
  await React.act(async () => release())
  await view.flush()
  assert.equal(document.querySelector('[data-lane="infra"] .hermes-lanes-volume button').disabled, false)
})

// ── which gateway ──
const HUB = { connectionId: 'hub', targetProfile: 'default' }
const HUB_ALT = { connectionId: 'hub', targetProfile: 'other-profile' }
const MINI = { connectionId: 'mini', targetProfile: 'default' }
const where = (opts) => opts.connectionId || 'local'

const MAC = { connectionId: 'this-mac', mode: 'local', primary: true, profile: 'default', targetProfile: 'default' }
const MAC_WORK = { connectionId: 'this-mac', mode: 'local', primary: true, profile: 'work', targetProfile: 'work' }

test('the panel reads and writes, by id, on the gateway that has lanes', async () => {
  const calls = []
  const ctx = { rest: async (path, opts = {}) => {
    calls.push([where(opts), opts.method || 'GET', path])
    if (where(opts) === 'this-mac') return { configured: false, reason: 'no lanes are configured (kanban.admission.lanes)' }
    if (where(opts) === 'mini') throw Error('unreachable')
    return snap()
  } }
  const source = P.createSource(ctx, { profileRoutes: async () => [MAC, MAC_WORK, MINI, HUB_ALT, HUB] })
  const body = await source.read(6)
  assert.equal(body.configured, true)
  assert.deepEqual(calls.map((c) => c[0]), ['this-mac', 'mini', 'hub'], 'each connection once, by id, never untagged')
  await source.read(24)
  assert.deepEqual(calls.at(-1), ['hub', 'GET', '/v1/lanes?hours=24'], 'settled: no further discovery')
  await source.write('product', { paused: true })
  assert.deepEqual(calls.at(-1), ['hub', 'PUT', '/v1/lanes/product'])
})

test('with lanes nowhere it shows the reason, names unreachable gateways, does not write, and looks again after UNCONFIGURED_MS', async () => {
  let now = 0
  const calls = []
  const ctx = { rest: async (path, opts = {}) => {
    calls.push(where(opts))
    if (where(opts) === 'mini') throw Error('plugin not installed')
    return { configured: false, reason: 'no lanes here' }
  } }
  const source = P.createSource(ctx, { profileRoutes: async () => [HUB, MINI] }, () => now)
  const body = await source.read(6)
  assert.match(body.reason, /^no lanes here; 1 other gateway\(s\) could not be reached \(plugin not installed\)$/)
  assert.equal(calls.length, 2)
  now = 1000
  await source.read(6)
  assert.equal(calls.length, 2, 'cached unconfigured answer')
  await assert.rejects(source.write('product', { share: 1 }), /No gateway with lanes/)
  now = P.UNCONFIGURED_MS + 1
  await source.read(6)
  assert.equal(calls.length, 4)
})

test('a failed read keeps the chosen gateway and is an error, not "no lanes"', async () => {
  const calls = []
  let hubUp = true
  const ctx = { rest: async (path, opts = {}) => {
    calls.push(where(opts))
    if (where(opts) === 'hub') { if (!hubUp) throw Error('hub restarting'); return snap() }
    return { configured: false, reason: 'no lanes here' }
  } }
  const source = P.createSource(ctx, { profileRoutes: async () => [MAC, HUB] })
  await source.read(6)
  hubUp = false
  await assert.rejects(source.read(6), /hub restarting/)
  await assert.rejects(source.read(6), /hub restarting/)
  assert.equal(source.target().connectionId, 'hub', 'still the hub after two failures')
  hubUp = true
  assert.equal((await source.read(6)).configured, true)
  assert.deepEqual(calls.slice(2), ['hub', 'hub', 'hub'], 'no search while it was down')
  hubUp = false  // a success in between resets the count: two more failures still do not search
  await assert.rejects(source.read(6))
  await assert.rejects(source.read(6))
  assert.equal(source.target().connectionId, 'hub')
})

test('after repeated failures, or when the gateway says lanes are gone, it looks again', async () => {
  let hub = 'up'
  const ctx = { rest: async (path, opts = {}) => {
    if (where(opts) === 'hub') {
      if (hub === 'down') throw Error('gone')
      return hub === 'up' ? snap() : { configured: false, reason: 'removed' }
    }
    if (where(opts) === 'mini') return snap()
    return { configured: false, reason: 'no lanes here' }
  } }
  const source = P.createSource(ctx, { profileRoutes: async () => [HUB, MINI] })
  await source.read(6)
  assert.equal(source.target().connectionId, 'hub')
  hub = 'down'
  for (let i = 1; i < P.FAILURES_BEFORE_REDISCOVERY; i++) await assert.rejects(source.read(6))
  assert.equal((await source.read(6)).configured, true)
  assert.equal(source.target().connectionId, 'mini')
  const again = P.createSource(ctx, { profileRoutes: async () => [HUB, MINI] })
  hub = 'up'
  await again.read(6)
  hub = 'unconfigured'
  await again.read(6)
  assert.equal(again.target().connectionId, 'mini')
})

test('the gateway with lanes is found whichever gateway is in front, and a missing route list falls back to an untagged request', async () => {
  const calls = []
  // Lanes live on this Mac's own connection; the window has another gateway in front, so an untagged request
  // would land on the hub and miss them.
  const ctx = { rest: async (path, opts = {}) => {
    calls.push(where(opts))
    return where(opts) === 'this-mac' ? snap() : { configured: false, reason: 'no lanes here' }
  } }
  const source = P.createSource(ctx, { profileRoutes: async () => [HUB, MAC, MAC_WORK, { connectionId: '', targetProfile: 'x' }] })
  assert.equal((await source.read(6)).configured, true)
  assert.deepEqual(calls, ['hub', 'this-mac'])
  assert.equal(source.target().connectionId, 'this-mac')
  calls.length = 0
  await P.createSource(ctx, { profileRoutes: async () => { throw Error('no bridge') } }).read(6)
  assert.deepEqual(calls, ['local'])
})

test('a search in which nothing answers throws, and the next read searches again', async () => {
  let up = true
  const calls = []
  const ctx = { rest: async (path, opts = {}) => { calls.push(where(opts)); if (!up) throw Error('offline'); return { configured: false, reason: 'no lanes here' } } }
  let now = 0
  const source = P.createSource(ctx, { profileRoutes: async () => [HUB] }, () => now)
  await source.read(6)
  up = false
  now = P.UNCONFIGURED_MS + 1
  await assert.rejects(source.read(6), /offline/)
  now += 1000
  await assert.rejects(source.read(6), /offline/)
  assert.equal(calls.length, 3, 'no stale "no lanes" answer served after a failed search')
})

test('a write goes to the gateway it is given, whatever is chosen now', async () => {
  const calls = []
  const ctx = { rest: async (path, opts = {}) => { calls.push([where(opts), opts.method || 'GET']); return snap() } }
  const source = P.createSource(ctx, { profileRoutes: async () => [MAC, HUB] })
  await source.read(6) // settles on this Mac
  const target = source.target()
  await source.write('product', { share: 1 }, { connectionId: 'hub' })
  await source.write('infra', { share: 2 }, target)
  assert.deepEqual(calls.slice(1), [['hub', 'PUT'], ['this-mac', 'PUT']])
})

test('mounted: the task pill follows a remote gateway that has lanes', async () => {
  host.routes = [MAC, HUB]
  const calls = []
  const ctx = { rest: async (path, opts = {}) => {
    calls.push([where(opts), opts.method || 'GET', path])
    return where(opts) === 'hub' ? snap() : { configured: false, reason: 'no lanes here' }
  } }
  const view = await mount(React.createElement(P.Lanes, { ctx }))
  assert.equal(pillOpensBoard(), false, 'lanes on a remote gateway: the pill opens this panel')
  await React.act(async () => window.dispatchEvent(new window.Event('hermes-show-lanes')))
  await view.flush()
  await view.click(document.querySelector('[data-lane="product"] .hermes-lanes-volume button'))
  await view.click(document.querySelector('[data-lane="infra"] .hermes-lanes-volume button'))
  await view.click(button('Apply'))
  assert.deepEqual(calls.filter((c) => c[1] === 'PUT'), [['hub', 'PUT', '/v1/lanes/product'], ['hub', 'PUT', '/v1/lanes/infra']])
})

test('with lanes defined on every gateway, the panel settles on the one that steers them', async () => {
  const calls = []
  const plain = () => snap({ planner: null })                    // definitions only, as shared config gives every host
  const steering = () => snap()                                  // the fixture's infra lane is gated and it has planner notes
  assert.equal(P.steers(plain()), true, 'a gated lane alone marks a steering gateway')
  const noGate = () => { const b = plain(); for (const l of Object.values(b.lanes)) l.gated = false; return b }
  assert.equal(P.steers(noGate()), false)
  const ctx = { rest: async (path, opts = {}) => { calls.push([where(opts), opts.method || 'GET']); return where(opts) === 'hub' ? steering() : noGate() } }
  const source = P.createSource(ctx, { profileRoutes: async () => [MAC, MINI, HUB] })
  assert.equal((await source.read(6)).planner.notes.length, 1)
  assert.equal(source.target().connectionId, 'hub', 'not the first configured gateway')
  await source.write('product', { share: 50 })
  assert.deepEqual(calls.at(-1), ['hub', 'PUT'])
})

test('with no steering gateway it still uses the first configured one', async () => {
  const noGate = () => { const b = snap({ planner: null }); for (const l of Object.values(b.lanes)) l.gated = false; return b }
  const ctx = { rest: async () => noGate() }
  const source = P.createSource(ctx, { profileRoutes: async () => [MAC, HUB] })
  await source.read(6)
  assert.equal(source.target().connectionId, 'this-mac')
})

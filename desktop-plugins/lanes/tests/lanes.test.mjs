// Lanes panel: target math under a draft, drift wording, cost coverage, and the rendered panel
// (unconfigured reason, slider + pause produce the right PUTs, window switch refetches).
import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount as rawMount, button, React, host } from './sdk-mock.mjs'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
// Feed actual Python producer output to the Desktop renderer.
const producer = fileURLToPath(new URL('../../../plugins/lanes/dashboard/view.py', import.meta.url))
const project = (snap, raw) => JSON.parse(execFileSync(process.env.HERMES_MODS_PYTHON || 'python3', ['-c',
  'import runpy,json,sys; s,r=json.load(sys.stdin); print(json.dumps(runpy.run_path(sys.argv[1])["project"](s,r,True)))', producer],
  { input: JSON.stringify([snap, raw]), encoding: 'utf8' }))
const damHint = (reason, cards) => JSON.parse(execFileSync(process.env.HERMES_MODS_PYTHON || 'python3', ['-c',
  'import runpy,json,sys; r,c=json.load(sys.stdin); print(json.dumps(runpy.run_path(sys.argv[1])["dam_hint"](r,c)))', producer],
  { input: JSON.stringify([reason, cards]), encoding: 'utf8' }))

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
    run_seconds: 0, runs: 0, running: 0, ready: 0, held: 0, blocked_on_you: 0, open: 0, done: 0, oldest_ready_s: null, cost_usd: 0, tokens: 0, usage_runs: 0, ...over }
}

function snap(over = {}) {
  return {
    configured: true, window_hours: 6, scope: 'fleet', planner: { notes: [{ lane: 'product', text: 'nothing ready' }] },
    lanes: {
      product: lane({ share: 30, target: 0.5, actual: 0.03, open: 18, running: 2, blocked_on_you: 3 }),
      infra: lane({ share: 30, target: 0.5, actual: 0.97, gated: true, admit_per_day: 50, admitted_today: 12, ready: 120, held: 40, open: 253, running: 9, blocked_on_you: 4,
        run_seconds: 3600 * 250, runs: 400, done: 60, cost_usd: 47.651, tokens: 12_500_000, usage_runs: 140, oldest_ready_s: 7200 })
    },
    ...over
  }
}

test('targets follow the draft, and a paused lane gets none', () => {
  const base = P.rows(snap())
  assert.deepEqual(base.map((r) => [r.name, r.target]), [['product', 0.5], ['infra', 0.5]])
  const drafted = P.rows(snap(), { product: { share: 90 } })
  assert.equal(drafted[0].target, 0.5)
  assert.equal(drafted[0].dirty, true)
  const paused = P.rows(snap(), { infra: { paused: true } })
  assert.deepEqual(paused.map((r) => r.target), [0.5, 0.5])
})

test('drift says why a lane is off target', () => {
  const [product, infra] = P.rows(project(snap(), flowSnap()))
  assert.equal(P.drift(product), 'under: nothing ready')
  assert.equal(P.drift({ ...product, ready: 4 }), 'under: nothing ready')
  assert.equal(P.drift(infra), 'over: using idle slots')
  assert.equal(P.drift({ ...product, actual: 0.45 }), 'under: nothing ready')
  assert.equal(P.drift({ ...infra, paused: true, target: 0 }), 'over: using idle slots')
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
  assert.match(view.container.textContent, /product: nothing ready/)
  assert.equal(button('Apply').disabled, true)
  const slider = view.container.querySelector('[data-lane="product"] input[type=range]')
  await React.act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(slider, '90')
    slider.dispatchEvent(new window.Event('input', { bubbles: true }))
  })
  await view.flush()
  assert.match(view.container.querySelector('[data-lane="product"]').textContent, /50% target/)
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
  assert.deepEqual(calls, [['/v1/lanes?hours=6&window=6h&expanded=false', 'GET', undefined]])
  assert.ok(document.querySelector('[data-lane-pill="product"]'), 'collapsed pills are visible')
  assert.equal(document.querySelector('[data-lane="product"]'), null, 'collapsed until the pill is clicked')
  let opensBoard
  await React.act(async () => { opensBoard = pillOpensBoard() })
  await view.flush()
  assert.equal(opensBoard, false, 'configured lanes: the task pill expands this strip instead of the board')
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
  assert.ok(calls.some((c) => c[0] === '/v1/lanes?hours=24&window=24h&expanded=true'), 'expanded window is fetched')
  await view.click(button('Board'))
  assert.deepEqual(host.navigated, ['/kanban'], 'the panel still leads to the board')
  assert.ok(document.querySelector('[data-lane="product"]'), 'body navigation leaves the persistent strip open')
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
    if (path === '/v1/lanes/steering') throw Error('old server has no authority probe')
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
    if (path === '/v1/lanes/steering') throw Error('old server has no authority probe')
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
    if (path === '/v1/lanes/steering') throw Error('old server has no authority probe')
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
    if (path === '/v1/lanes/steering') throw Error('old server has no authority probe')
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
  const ctx = { rest: async (path, opts = {}) => { if (path === '/v1/lanes/steering') throw Error('old server has no authority probe'); calls.push(where(opts)); if (!up) throw Error('offline'); return { configured: false, reason: 'no lanes here' } } }
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
  assert.deepEqual(calls.slice(1), [['hub', 'GET'], ['hub', 'PUT'], ['this-mac', 'PUT']])
})

test('mounted: the task pill follows a remote gateway that has lanes', async () => {
  host.routes = [MAC, HUB]
  const calls = []
  const ctx = { rest: async (path, opts = {}) => {
    if (path === '/v1/lanes/steering') throw Error('old server has no authority probe')
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

test('meter is blue running, yellow not ready, red blocked on you; missing blocked_on_you is zero', () => {
  assert.deepEqual(P.meter({ running: 2, open: 18, not_ready: 13, blocked_on_you: 3 }), { running: 2, notReady: 13, blocked: 3, total: 18 })
  assert.deepEqual(P.meter({ running: 2, open: 18, not_ready: 16 }), { running: 2, notReady: 16, blocked: 0, total: 18 })
  assert.deepEqual(P.meter({}), { running: 0, notReady: 0, blocked: 0, total: 0 })
})

test('older SDKs without TOP_STRIP_AREA still activate at shell.topStrip', () => {
  assert.equal(P.TOP_STRIP_AREA, 'shell.topStrip')
  assert.equal('TOP_STRIP_AREA' in globalThis.__lanesSdk, false)
})

test('registers a top strip, not a status-bar popup', () => {
  const areas = []
  const dispose = []
  P.default.register({ register: (c) => areas.push(c), onDispose: (fn) => dispose.push(fn) })
  assert.equal(areas.find((a) => a.id === 'strip')?.area, 'shell.topStrip')
  assert.equal(areas.some((a) => String(a.area).startsWith('statusBar')), false)
  assert.ok(areas.some((a) => a.id === 'show-lanes' && a.area === 'palette'))
  for (const fn of dispose) fn()
})

test('open/closed is persisted; collapsed is the default', async () => {
  const ctx = ctxWith([], () => snap())
  const first = await mount(React.createElement(P.Lanes, { ctx }))
  assert.equal(first.container.getAttribute('data-open') || document.querySelector('[data-slot=lanes-strip]')?.getAttribute('data-open'), '0')
  assert.equal(document.querySelector('[data-lane="product"]'), null)
  await first.click(button('Expand lanes'))
  assert.equal(localStorage.getItem(P.OPEN_KEY), '1')
  assert.ok(document.querySelector('[data-lane="product"]'))
  await first.close()
  const again = await mount(React.createElement(P.Lanes, { ctx }))
  assert.ok(document.querySelector('[data-lane="product"]'), 'remount restores expanded')
  assert.equal(document.querySelector('[data-slot=lanes-strip]').getAttribute('data-open'), '1')
  await again.click(button('Collapse lanes'))
  assert.equal(localStorage.getItem(P.OPEN_KEY), '0')
  assert.equal(document.querySelector('[data-lane="product"]'), null)
})

test('a lane pill selects the lane without expanding; the arrow expands the body', async () => {
  const view = await mount(React.createElement(P.Lanes, { ctx: ctxWith([], () => snap()) }))
  await view.click(document.querySelector('[data-lane-pill="infra"]'))
  assert.equal(document.querySelector('[data-lane-pill="infra"]').getAttribute('aria-pressed'), 'true')
  assert.equal(document.querySelector('[data-slot=lanes-strip]').getAttribute('data-open'), '0')
  assert.equal(document.querySelector('[data-lane="infra"]'), null)
  await view.click(document.querySelector('.hermes-lanes-toggle'))
  assert.ok(document.querySelector('[data-lane="infra"]'))
  const body = document.querySelector('.hermes-lanes-body')
  assert.equal(body.style.maxHeight, '60vh')
  assert.equal(getComputedStyle(body).overflow, 'auto')
})

// ── Floor and Flow ─────────────────────────────────────────────────────────
const card = (id, lane, step, reason, over = {}) => ({ id, title: `card ${id}`, lane, step, reason, age_h: 2, pr: null, source: { kind: 'session', id: 'sess-a', project: 'Hermes' }, ...over })

function flowSnap(over = {}) {
  const cards = [
    card('t1', 'product', 'intake', 'triage', { source: { kind: 'automation', id: 'bb-tuner', project: null } }),
    card('t2', 'product', 'waiting', 'dep', { pr: 'https://example.com/o/r/pull/12' }),
    card('t3', 'product', 'waiting', 'you', { age_h: 30 }),
    card('t4', 'product', 'ready', 'slot'),
    // pr is each card's own PR, not its blocking parent's PR.
    card('t5', 'infra', 'waiting', 'dep', { pr: 'https://example.com/o/r/pull/13' }),
    card('t6', 'infra', 'waiting', 'dep', { pr: 'https://example.com/o/r/pull/14' }),
    card('t7', 'infra', 'waiting', 'dep'),
    card('t8', 'infra', 'waiting', 'dep'),
    card('t9', 'infra', 'running', 'run'),
    card('t10', 'infra', 'ci', 'review', { source: { kind: 'user', id: 'user', project: 'Baby Bear' } }),
    card('t11', 'infra', 'intake', 'limit:cards_per_day')
  ]
  const raw = {
    window: '6h', configured: true, cards,
    lanes: {
      product: { done: 4, run_hours: 1.8, cost_usd: 15.04, limits: { cards_per_day: 20, usd_per_day: null }, usage: { cards_today: 3, usd_today: 1.5 } },
      infra: { done: 33, run_hours: 4.2, cost_usd: 45.2, limits: { cards_per_day: 50, usd_per_day: 40 }, usage: { cards_today: 50, usd_today: 45.21 } }
    },
    ...over
  }
  const view = project(snap(), raw)
  return { ...view.flow, floor: view.floor }
}
const withUnclassified = () => flowSnap({ cards: [...flowSnap().cards, card('tu', 'infra', 'landing_unclassified', 'review')] })
const panelProps = (over = {}) => ({ snap: snap(), flow: flowSnap(), windowHours: 6, onWindow() {}, onApply() {}, ...over })
const cell = (lane, step) => document.querySelector(`[data-lane="${lane}"] [data-step="${step}"]`)

test('tab switch shows Floor or Flow and the last tab is remembered', async () => {
  const view = await mount(React.createElement(P.Panel, panelProps()))
  assert.ok(document.querySelector('[data-view=floor]'), 'Floor is the default')
  assert.equal(document.querySelector('[data-view=flow]'), null)
  await view.click(button('Flow'))
  assert.ok(document.querySelector('[data-view=flow]'))
  assert.equal(document.querySelector('[data-view=floor]'), null)
  assert.equal(localStorage.getItem(P.TAB_KEY), 'flow')
  assert.equal(button('Flow').getAttribute('aria-selected'), 'true')
  await view.close()
  const again = await mount(React.createElement(P.Panel, panelProps()))
  assert.ok(document.querySelector('[data-view=flow]'), 'a remount restores the Flow tab')
  await again.click(button('Floor'))
  assert.equal(localStorage.getItem(P.TAB_KEY), 'floor')
  localStorage.setItem(P.TAB_KEY, 'nonsense')
  assert.equal(P.loadTab(), 'floor')
})

test('Floor: cell counts, box per card, and hatching only where cards pile up', async () => {
  await mount(React.createElement(P.Panel, panelProps()))
  const count = (lane, step) => Number(cell(lane, step).getAttribute('data-count'))
  assert.equal(count('product', 'waiting'), 2)
  assert.equal(count('infra', 'waiting'), 4)
  assert.equal(count('infra', 'running'), 1)
  assert.equal(count('product', 'ci'), 0)
  assert.equal(cell('infra', 'waiting').querySelectorAll('.hermes-lanes-box').length, 4)
  assert.equal(cell('infra', 'waiting').getAttribute('data-piling'), '1', '4 cards pile up')
  assert.equal(cell('product', 'waiting').getAttribute('data-piling'), '1', 'any card that needs you piles up')
  assert.equal(cell('product', 'intake').getAttribute('data-piling'), '0')
  assert.equal(cell('infra', 'running').getAttribute('data-piling'), '0')
  assert.ok(cell('infra', 'waiting').classList.contains('is-piling'))
  assert.equal(cell('infra', 'done').textContent.includes('33'), true, 'Done shows the window throughput')
  assert.equal(document.querySelectorAll('.hermes-lanes-box').length, flowSnap().cards.length, 'one box per open card')
  const box = cell('product', 'waiting').querySelector('[data-reason=you]')
  assert.match(box.getAttribute('title'), /card t3\nneeds you · 30h old/)
  assert.equal(cell('product', 'waiting').querySelector('.hermes-lanes-box').getAttribute('data-reason'), 'you', 'needs-you boxes come first')
})

test('Floor: crowded cells expose every card title and click target beyond sixteen', async () => {
  const cards = Array.from({ length: 37 }, (_, i) => card(`crowded-${i}`, 'product', 'waiting', 'dep'))
  const opened = []
  const view = await mount(React.createElement(P.Panel, panelProps({
    flow: flowSnap({ cards }), onOpenCard: (id) => opened.push(id)
  })))
  const waiting = cell('product', 'waiting')
  assert.equal(waiting.getAttribute('data-count'), String(cards.length))
  assert.equal(waiting.querySelector('.hermes-lanes-count').textContent, String(cards.length))
  assert.equal(waiting.getAttribute('data-piling'), '1')
  assert.ok(waiting.classList.contains('is-piling'))
  const boxes = [...waiting.querySelectorAll('button[data-card]')]
  assert.equal(boxes.length, cards.length)
  assert.deepEqual(boxes.map((box) => box.dataset.card).sort(), cards.map((c) => c.id).sort())
  for (const c of cards) {
    const box = boxes.find((b) => b.dataset.card === c.id)
    assert.ok(box.title.startsWith(`${c.title}\n`), `hover title for ${c.id}`)
    assert.ok(box.getAttribute('aria-label').startsWith(c.title), `accessible name for ${c.id}`)
    await view.click(box)
  }
  assert.deepEqual(opened, cards.map((c) => c.id))
})

test('Floor: the unclassified column exists only while a card is in it', async () => {
  const view = await mount(React.createElement(P.Panel, panelProps()))
  assert.equal(document.querySelector('[data-column=landing_unclassified]'), null)
  assert.equal(document.querySelectorAll('[data-column]').length, 10)
  assert.equal(flowSnap().floor.columns.some(c => c.key === 'landing_unclassified'), false)
  await view.close()
  await mount(React.createElement(P.Panel, panelProps({ flow: withUnclassified() })))
  assert.ok(document.querySelector('[data-column=landing_unclassified]'))
  assert.equal(document.querySelectorAll('[data-column]').length, 11)
  assert.equal(cell('infra', 'landing_unclassified').getAttribute('data-count'), '1')
  assert.equal(cell('product', 'landing_unclassified').getAttribute('data-count'), '0')
  // A step this panel has no column for is not dropped: it lands in the unclassified column.
  assert.equal(flowSnap({ cards: [card('unknown', 'infra', 'mystery', 'review')] }).floor.columns.at(-1).key, 'landing_unclassified')
})

test('clicking a box opens that card', async () => {
  const opened = []
  await mount(React.createElement(P.Panel, panelProps({ onOpenCard: (id) => opened.push(id) })))
  await React.act(async () => cell('infra', 'running').querySelector('.hermes-lanes-box').click())
  assert.deepEqual(opened, ['t9'])
  const hooks = []
  assert.equal(P.openCard('t9', 'hub', { __hermesKanbanOpenTask: (...a) => (hooks.push(a), true) }), 'drawer')
  assert.deepEqual(hooks, [['t9', undefined, 'hub']])
  const nav = []
  assert.equal(P.openCard('t9', undefined, {}, (p) => nav.push(p)), 'page')
  assert.deepEqual(nav, ['/kanban'])
})

test('valve Apply calls lanes set with the changed share and daily caps, per lane', async () => {
  const applied = []
  const view = await mount(React.createElement(P.Panel, panelProps({ onApply: (p) => { applied.push(p); return false } })))
  assert.equal(button('Apply').disabled, true)
  const type = async (selector, value) => {
    const input = document.querySelector(selector)
    await React.act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value)
      input.dispatchEvent(new window.Event('input', { bubbles: true }))
    })
    await view.flush()
  }
  assert.equal(document.querySelector('[data-lane=infra] [data-valve=cards_per_day] input').value, '50', 'saved cap shown')
  assert.equal(document.querySelector('[data-lane=product] [data-valve=usd_per_day] input').value, '', 'unset cap is empty')
  assert.match(document.querySelector('[data-lane=infra] [data-valve=cards_per_day]').textContent, /50 today/)
  await type('[data-lane=infra] [data-valve=cards_per_day] input', '60')
  await type('[data-lane=product] [data-valve=usd_per_day] input', '12.5')
  await type('[data-lane=product] [data-valve=cards_per_day] input', '20') // equals the saved value: no change
  await view.click(button('Apply'))
  assert.deepEqual(applied, [[['infra', { cards_per_day: 60 }], ['product', { usd_per_day: 12.5 }]]])
  await view.click(button('Reset'))
  assert.equal(button('Apply').disabled, true)
  assert.equal(document.querySelector('[data-lane=infra] [data-valve=cards_per_day] input').value, '50', 'Reset restores the saved cap')
  assert.deepEqual(P.changes(snap(), { infra: { cards_per_day: 50, usd_per_day: null } }, flowSnap()), [['infra', { usd_per_day: null }]])
})

test('worker valve and blank caps Apply explicit null; Reset restores values; old core stays read-only', async () => {
  const flow = flowSnap()
  flow.lanes.product.limits.max_workers = 4
  const applied = []
  const view = await mount(React.createElement(P.Panel, panelProps({ flow, onApply: (p) => { applied.push(p); return false } })))
  const type = async (selector, value) => {
    const input = document.querySelector(selector)
    assert.ok(input, selector)
    await React.act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value)
      input.dispatchEvent(new window.Event('input', { bubbles: true }))
    })
    await view.flush()
  }
  assert.equal(document.querySelector('[data-lane=infra] [data-valve=workers] input'), null, 'older core worker gauge')
  await type('[data-lane=product] [data-valve=max_workers] input', '2')
  await type('[data-lane=product] [data-valve=cards_per_day] input', '')
  await view.click(button('Apply'))
  assert.deepEqual(applied, [[['product', { max_workers: 2, cards_per_day: null }]]])
  await view.click(button('Reset'))
  assert.equal(document.querySelector('[data-lane=product] [data-valve=max_workers] input').value, '4')
  assert.equal(document.querySelector('[data-lane=product] [data-valve=cards_per_day] input').value, '20')
  assert.equal(button('Apply').disabled, true)
  assert.deepEqual(P.changes(snap(), { product: { max_workers: null } }, flow), [['product', { max_workers: null }]])
})

test('dependency hints group actual blockers, never own PR; multiple parents do not promise full release', () => {
  const b = { id: 'parent', pr: 'https://github.com/example/repo/pull/42' }
  const cards = [{ id: 'c1', blockers: [b] }, { id: 'c2', blocker: b }]
  assert.match(damHint('dep', cards), /land PR #42 → frees 2/)
  assert.match(damHint('dep', [{ id: 'c1', blockers: [b, { id: 'p2', pr: null }] }]), /unblocks 1 dependency/)
  assert.match(damHint('dep', [{ id: 'legacy', pr: b.pr }]), /1 card waits on an open parent/)
  assert.doesNotMatch(damHint('dep', [{ id: 'legacy', pr: b.pr }]), /#42/)
})

test('Apply from the expanded strip PUTs the caps to the lanes API', async () => {
  const calls = []
  const ctx = ctxWith(calls, (path) => (path.startsWith('/v1/lanes/flow') ? flowSnap() : snap()))
  const view = await mount(React.createElement(P.Lanes, { ctx }))
  await React.act(async () => window.dispatchEvent(new window.Event('hermes-show-lanes')))
  await view.flush()
  assert.ok(calls.some((c) => c[0].includes('expanded=true')), 'expanded view is read once the strip is open')
  const input = document.querySelector('[data-lane=product] [data-valve=usd_per_day] input')
  await React.act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '9')
    input.dispatchEvent(new window.Event('input', { bubbles: true }))
  })
  await view.flush()
  await view.click(button('Apply'))
  assert.deepEqual(calls.filter((c) => c[1] === 'PUT'), [['/v1/lanes/product', 'PUT', { usd_per_day: 9 }]])
})

test('Flow: node and edge totals in every layer equal the card count', async () => {
  const flow = withUnclassified()
  for (const by of ['project', 'source']) {
    const g = P.flowGraph(flow, by)
    const sum = (list) => list.reduce((s, n) => s + n.count, 0)
    assert.equal(g.total, flow.cards.length)
    assert.equal(sum(g.sources), flow.cards.length, `${by}: sources`)
    assert.equal(sum(g.lanes), flow.cards.length, `${by}: lanes`)
    assert.equal(sum(g.sinks), flow.cards.length, `${by}: sinks`)
    assert.equal(sum(g.edges.sourceLane), flow.cards.length, `${by}: source→lane edges`)
    assert.equal(sum(g.edges.laneSink), flow.cards.length, `${by}: lane→sink edges`)
    const into = (edges, key) => sum(edges.filter((e) => e.to === key))
    for (const n of g.lanes) assert.equal(into(g.edges.sourceLane, n.key), n.count)
    for (const n of g.sinks) assert.equal(into(g.edges.laneSink, n.key), n.count)
    const geo = P.sankey(g)
    assert.equal(geo.links.reduce((s, l) => s + l.count, 0), 2 * flow.cards.length)
    assert.ok(geo.columns.flat().every((n) => Number.isFinite(n.y) && n.h > 0))
  }
  assert.deepEqual(P.flowGraph(flow, 'project').sources.map((s) => s.key).sort(), ['Baby Bear', 'Hermes', 'automations'])
  assert.ok(P.flowGraph(flow, 'source').sources.some((s) => s.key === 'you (manual)'))
  const view = await mount(React.createElement(P.Panel, panelProps({ flow })))
  await view.click(button('Flow'))
  for (const kind of ['source', 'lane', 'sink']) {
    const nodes = [...document.querySelectorAll(`[data-flow-node=${kind}]`)]
    assert.equal(nodes.reduce((s, n) => s + Number(n.getAttribute('data-count')), 0), flow.cards.length, `${kind} nodes in the DOM`)
  }
  const edges = [...document.querySelectorAll('[data-edge]')]
  assert.equal(edges.reduce((s, e) => s + Number(e.getAttribute('data-count')), 0), 2 * flow.cards.length)
})

test('Flow: flowing steps vs dams, what clears each dam, the project/source toggle and the limits line', async () => {
  const view = await mount(React.createElement(P.Panel, panelProps()))
  await view.click(button('Flow'))
  const sink = (key) => document.querySelector(`[data-flow-node=sink][data-key="${key}"]`)
  assert.ok(sink('step:running') && sink('step:ci'), 'running and CI work flows')
  assert.ok(sink('dam:dep') && sink('dam:you') && sink('dam:limit:cards_per_day'), 'waiting work is a dam named by its reason')
  assert.equal(sink('dam:dep').getAttribute('data-count'), '5')
  const hint = (key) => document.querySelector(`[data-hint="${key}"]`).textContent
  assert.equal(hint('dam:dep'), '5 cards wait on an open parent · oldest 2h')
  assert.equal(hint('dam:you'), 'answer 1 ask · oldest 30h')
  assert.match(hint('dam:limit:cards_per_day'), /raise cards\/day, or wait for local midnight/)
  assert.match(hint('dam:slot'), /1 card needs a free worker/)
  assert.equal(document.querySelector('[data-hint="step:running"]'), null, 'flowing work has nothing to clear')
  assert.ok(document.querySelector('[data-flow-node=source][data-key=Hermes]'))
  await view.click(button('by source'))
  assert.ok(document.querySelector('[data-flow-node=source][data-key="session sess-a"]'))
  assert.equal(localStorage.getItem(P.BY_KEY), 'source')
  assert.match(document.querySelector('[data-limit-lane=infra]').textContent, /50\/50 cards.*\$45\.21\/\$40/)
  assert.ok(document.querySelector('[data-limit-lane=infra] .hermes-lanes-warn'), 'a cap that is reached is flagged')
  assert.equal(document.querySelector('[data-limit-lane=product] .hermes-lanes-warn'), null)
  await view.click(button('Edit limits'))
  assert.ok(document.querySelector('[data-view=floor]'), 'Edit limits goes to the valves')
})

test('an unavailable flow says so on both tabs and the lane rows still show their valves', async () => {
  const view = await mount(React.createElement(P.Panel, panelProps({ flow: undefined, flowError: 'this Hermes core has no lanes flow support yet' })))
  assert.match(view.container.textContent, /Cards unavailable: this Hermes core has no lanes flow support yet/)
  assert.ok(document.querySelector('[data-lane=product] input[type=range]'))
  await view.click(button('Flow'))
  assert.match(view.container.textContent, /Flow unavailable: this Hermes core has no lanes flow support yet/)
})

test('cards are read only while the strip is open, from the window picked', async () => {
  const calls = []
  const view = await mount(React.createElement(P.Lanes, { ctx: ctxWith(calls, (path) => (path.startsWith('/v1/lanes/flow') ? flowSnap() : snap())) }))
  assert.equal(calls.filter((c) => c[0].startsWith('/v1/lanes/flow')).length, 0, 'collapsed: no flow request')
  await view.click(button('Expand lanes'))
  await view.click(button('7d'))
  assert.ok(calls.some((c) => c[0] === '/v1/lanes?hours=168&window=7d&expanded=true'))
  assert.equal(P.flowWindow(1), '1h')
})

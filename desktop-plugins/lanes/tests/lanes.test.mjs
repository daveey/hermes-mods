// Lanes panel: target math under a draft, drift wording, cost coverage, and the rendered panel
// (unconfigured reason, slider + pause produce the right PUTs, window switch refetches).
import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount as rawMount, button, React } from './sdk-mock.mjs'

const P = await loadPlugin()

// Every mounted view is closed after its test, pass or fail: a leaked panel keeps its poll timer and its DOM.
const open = []
async function mount(...args) { const view = await rawMount(...args); open.push(view); return view }
test.afterEach(async () => {
  for (const view of open.splice(0)) await view.close().catch(() => {})
  delete window[P.LOADED_FLAG]
  localStorage.clear()
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
  assert.equal(window[P.LOADED_FLAG], true, 'configured lanes: the runners chip may open this panel')
  await React.act(async () => window.dispatchEvent(new window.Event('hermes-show-lanes')))
  await view.flush()
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
  await view.close()
  assert.equal(window[P.LOADED_FLAG], undefined)
})

test('without working lanes the runners chip is left alone', async () => {
  const view = await mount(React.createElement(P.Lanes, { ctx: ctxWith([], () => ({ configured: false, reason: 'this Hermes core has no lanes support yet' })) }))
  assert.equal(window[P.LOADED_FLAG], undefined)
  await view.close()
  const broken = await mount(React.createElement(P.Lanes, { ctx: { rest: async () => { throw Error('404') } } }))
  assert.equal(window[P.LOADED_FLAG], undefined)
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

import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount, React } from './sdk-mock.mjs'

const P = await loadPlugin()
const views = []
const snap = { configured: true, lanes: { product: { share: 50 }, infra: { share: 50 } } }
async function setup(zoneHeight) {
  const view = await mount(React.createElement('div', {
    'data-tree-group': zoneHeight === undefined ? undefined : 'main',
    ref: el => { if (el && zoneHeight !== undefined) el.getBoundingClientRect = () => ({ height: zoneHeight }) }
  }, React.createElement(P.Lanes, { ctx: { rest: async () => snap } })))
  views.push(view)
  return { view, line: view.container.querySelector('.hermes-lanes-mini'),
    pill: name => view.container.querySelector(`[data-lane-pill="${name}"]`),
    expanded: () => view.container.querySelector('[data-slot=lanes-strip]').dataset.open === '1' }
}
test.afterEach(async () => {
  for (const view of views.splice(0)) await view.close()
  localStorage.clear()
})

test('registers exactly once in the main area on new hosts', async () => {
  const modern = await loadPlugin({ MAIN_PANE_TOP_AREA: 'workspace.mainTop', TOP_STRIP_AREA: 'shell.topStrip' })
  const contributions = [], disposers = []
  modern.default.register({ register: c => contributions.push(c), onDispose: fn => disposers.push(fn) })
  try { assert.deepEqual(contributions.filter(c => c.id === 'strip').map(c => c.area), ['workspace.mainTop']) }
  finally { disposers.forEach(fn => fn()) }
})
test('registers exactly once in the shell area on older hosts', () => {
  const contributions = [], disposers = []
  P.default.register({ register: c => contributions.push(c), onDispose: fn => disposers.push(fn) })
  try { assert.deepEqual(contributions.filter(c => c.id === 'strip').map(c => c.area), ['shell.topStrip']) }
  finally { disposers.forEach(fn => fn()) }
})

test('blank line space toggles in both directions with truthful aria-expanded', async () => {
  const { view, line, expanded } = await setup()
  assert.equal(expanded(), false)
  assert.equal(line.getAttribute('aria-expanded'), 'false')
  await view.click(line)
  assert.equal(expanded(), true)
  assert.equal(line.getAttribute('aria-expanded'), 'true')
  await view.click(line)
  assert.equal(expanded(), false)
})

test('closed pill opens, another pill switches focus, focused pill collapses', async () => {
  const seen = []
  const original = HTMLElement.prototype.scrollIntoView
  HTMLElement.prototype.scrollIntoView = function () { seen.push(this.dataset.lane) }
  try {
    const { view, pill, expanded } = await setup()
    await view.click(pill('product'))
    assert.equal(expanded(), true)
    assert.equal(pill('product').getAttribute('aria-expanded'), 'true')
    await view.click(pill('infra'))
    assert.equal(expanded(), true)
    assert.deepEqual(seen, ['product', 'infra'])
    assert.equal(pill('product').getAttribute('aria-expanded'), 'false')
    assert.equal(pill('infra').getAttribute('aria-expanded'), 'true')
    await view.click(pill('infra'))
    assert.equal(expanded(), false)
    assert.equal(pill('infra').getAttribute('aria-expanded'), 'false')
  } finally { HTMLElement.prototype.scrollIntoView = original }
})

test('toggle button acts once and expanded body controls never collapse', async () => {
  const { view, expanded } = await setup()
  const toggle = view.container.querySelector('.hermes-lanes-toggle')
  await view.click(toggle)
  assert.equal(expanded(), true)
  const body = view.container.querySelector('.hermes-lanes-body')
  await view.click(body)
  assert.equal(expanded(), true)
  for (const control of body.querySelectorAll('button,input')) {
    if (control.textContent === 'Open board' || control.textContent === 'Apply') continue
    await view.click(control)
    assert.equal(expanded(), true)
  }
  await view.click(toggle)
  assert.equal(expanded(), false)
})

test('expanded body measures and follows 60 percent of its zone, not the window', async () => {
  const observers = []
  const original = globalThis.ResizeObserver
  globalThis.ResizeObserver = class {
    constructor(callback) { this.callback = callback; observers.push(this) }
    observe(target) { this.target = target }
    disconnect() { this.disconnected = true }
  }
  try {
    const { view, line } = await setup(400)
    await view.click(line)
    const body = view.container.querySelector('.hermes-lanes-body')
    assert.ok(body, 'line opens the body')
    assert.equal(body.style.maxHeight, '240px')
    const observer = observers.find(o => o.target?.hasAttribute('data-tree-group'))
    assert.ok(observer, 'observes main zone size')
    observer.target.getBoundingClientRect = () => ({ height: 300 })
    await React.act(async () => observer.callback([]))
    assert.equal(body.style.maxHeight, '180px')
    assert.equal(body.style.overflow, 'auto')
    await view.close()
    views.splice(views.indexOf(view), 1)
    assert.equal(observer.disconnected, true)
  } finally { globalThis.ResizeObserver = original }
})

test('Enter and Space on the line toggle without catching child keyboard events', async () => {
  const { view, line, pill, expanded } = await setup()
  assert.equal(line.getAttribute('role'), 'button')
  assert.equal(line.tabIndex, 0)
  for (const key of ['Enter', ' ']) {
    await React.act(async () => line.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true })))
    await view.flush()
    assert.equal(expanded(), key === 'Enter')
  }
  // Native buttons synthesize click on keyboard activation; bubbling keydown must not toggle the parent too.
  await React.act(async () => pill('product').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))
  assert.equal(expanded(), false)
  await view.click(pill('product'), { detail: 0 })
  assert.equal(expanded(), true)
  await view.click(pill('product'), { detail: 0 })
  assert.equal(expanded(), false)
})

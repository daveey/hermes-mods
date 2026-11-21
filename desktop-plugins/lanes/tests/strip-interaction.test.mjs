import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount, React } from './sdk-mock.mjs'

const P = await loadPlugin()
const views = []
const snap = { configured: true, lanes: {
  product: { share: 50, status: { you: 2, ext: 1, worker: 0, run: 3, dep: 0, triage: 0, other: 0 } },
  infra: { share: 50, status: { you: 1, ext: 4, worker: 2, run: 0, dep: 5, triage: 1, other: 0 } } },
  status: { you: 3, ext: 5, worker: 2, run: 3, dep: 5, triage: 1, other: 0 } }
const totals = (view) => Object.fromEntries([...view.container.querySelectorAll('[data-status]')]
  .map(i => [i.dataset.status, Number(i.querySelector('.hermes-lanes-n').textContent)]))
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

test('clicking the line or a pill never expands; only the arrow does', async () => {
  const { view, line, pill, expanded } = await setup()
  await view.click(line)
  assert.equal(expanded(), false)
  await view.click(pill('product'))
  assert.equal(expanded(), false)
  await view.click(view.container.querySelector('.hermes-lanes-toggle'))
  assert.equal(expanded(), true)
  await view.click(pill('product'))
  assert.equal(expanded(), true, 'a pill click does not collapse either')
})

test('pills select and deselect lanes and the totals follow the selection', async () => {
  const { view, pill } = await setup()
  assert.deepEqual(totals(view), { you: 3, ext: 5, worker: 2, run: 3, dep: 5, triage: 1 })
  await view.click(pill('product'))
  assert.equal(pill('product').getAttribute('aria-pressed'), 'true')
  assert.equal(pill('infra').dataset.dimmed, '1')
  assert.deepEqual(totals(view), { you: 2, ext: 1, run: 3 })
  assert.match(view.container.querySelector('[data-status=you]').title, /in product$/)
  await view.click(pill('infra'))
  assert.deepEqual(totals(view), { you: 3, ext: 5, worker: 2, run: 3, dep: 5, triage: 1 })
  await view.click(pill('product'))
  assert.deepEqual(totals(view), { you: 1, ext: 4, worker: 2, dep: 5, triage: 1 })
  assert.deepEqual(JSON.parse(localStorage.getItem('hermes.lanes.selected')), ['infra'])
  await view.click(pill('infra'))
  assert.equal(pill('infra').getAttribute('aria-pressed'), 'false')
  assert.equal(pill('product').dataset.dimmed, '0')
  assert.deepEqual(totals(view), { you: 3, ext: 5, worker: 2, run: 3, dep: 5, triage: 1 })
})

test('selection survives a remount; an older hub without per-lane status keeps all-lanes totals', async () => {
  localStorage.setItem('hermes.lanes.selected', JSON.stringify(['infra', 'gone']))
  const { view, pill } = await setup()
  assert.equal(pill('infra').getAttribute('aria-pressed'), 'true')
  assert.deepEqual(totals(view), { you: 1, ext: 4, worker: 2, dep: 5, triage: 1 })
  assert.deepEqual(P.selectedStatus({ lanes: { a: {} }, status: { you: 9 } }, ['a']), { you: 9 })
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
    const { view } = await setup(400)
    await view.click(view.container.querySelector('.hermes-lanes-toggle'))
    const body = view.container.querySelector('.hermes-lanes-body')
    assert.ok(body, 'arrow opens the body')
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

test('keyboard: the line is not a button; Enter on a pill selects it without expanding', async () => {
  const { view, line, pill, expanded } = await setup()
  assert.equal(line.getAttribute('role'), 'group')
  await React.act(async () => line.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })))
  assert.equal(expanded(), false)
  // Native buttons synthesize click on keyboard activation (detail 0).
  await view.click(pill('product'), { detail: 0 })
  assert.equal(pill('product').getAttribute('aria-pressed'), 'true')
  assert.equal(expanded(), false)
})

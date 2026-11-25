import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount, React } from './sdk-mock.mjs'

const P = await loadPlugin({ SIDEBAR_SECTIONS_TOP_AREA: 'sidebar.sections.top', SIDEBAR_SECTIONS_AREA: 'sidebar.sections', SESSION_FILTER_AREA: 'sidebar.filter' })
const snapshot = () => ({ configured: true, status: { run: 7, you: 3 }, totals: { not_ready: 41 },
  sidebar: { cost_today: 12.34, idle_lanes: ['idle'] },
  governors: { idle_total: 31, rows: [{ id: 'llm_pool', name: 'LLM pool', state: 'on', cards_charged: 31,
    reading: { used_pct: 97, accounts: 23 }, threshold: { seven_day: 95 }, scope: [] }], counts: { on: 1, off: 8 } },
  lanes: { work: { share: 20, open: 90, not_ready: 41, status: { run: 7, you: 3 }, actual: .2, target: .3,
    governor_held: 31, top_reason: '31 held · LLM pool', steps: [{ key: 'ready', count: 90, piling: true }],
    limits: { max_workers: 4, cards_per_day: 20, usd_per_day: 100 }, boards: ['alpha', 'beta'],
    held_sessions: ['chat'], held_cards: ['card'] }, idle: { open: 0 } } })

function setup(body = snapshot()) {
  const contributions = [], writes = []
  let push
  const ctx = { rest: async (path, options) => { if (options?.method === 'PUT') writes.push([path, options]); return body },
    onDispose() {}, register(c) { contributions.push(c); return () => {} } }
  P.sourceFor(ctx).subscribe = (_p, cb) => { push = cb; cb(body)
    return Object.assign(() => {}, { update() {}, refresh() {} }) }
  return { ctx, contributions, writes, push: (...args) => push(...args) }
}

test('sidebar registration is separate from top totals and old SDK keeps original strip', async () => {
  const modern = setup()
  P.default.register(modern.ctx)
  assert.equal(modern.contributions.find(c => c.id === 'lanes-sidebar').area, 'sidebar.sections.top')
  assert.equal(modern.contributions.find(c => c.id === 'strip').render().type, P.TopTotals)
  const old = await loadPlugin()
  const legacy = setup()
  old.default.register(legacy.ctx)
  assert.equal(legacy.contributions.some(c => c.id === 'lanes-sidebar'), false)
  assert.equal(legacy.contributions.find(c => c.id === 'strip').render().type, old.Lanes)
})

test('expanded lane survives unmount and remount', async () => {
  localStorage.clear()
  const t = setup()
  await P.sourceFor(t.ctx).read(6, '6h', true)
  const first = await mount(React.createElement(P.SidebarLanes, { ctx: t.ctx }))
  try {
    await first.click(first.container.querySelector('.lanes-sb-heading'))
    await first.click(first.container.querySelector('[data-sidebar-lane="work"] button'))
  } finally { await first.close() }
  const second = await mount(React.createElement(P.SidebarLanes, { ctx: t.ctx }))
  try {
    assert.equal(second.container.querySelector('.lanes-sb-heading').getAttribute('aria-expanded'), 'true')
    assert.equal(second.container.querySelector('[data-sidebar-lane="work"] button').getAttribute('aria-expanded'), 'true')
    assert.ok(second.container.querySelector('button[type=submit]'))
  } finally { await second.close(); localStorage.clear() }
})

test('minimized counts are hub numbers, expand and lane controls persist; offline retains numbers', async () => {
  localStorage.clear()
  const t = setup()
  await P.sourceFor(t.ctx).read(6, '6h', true)
  const pane = await mount(React.createElement(P.SidebarLanes, { ctx: t.ctx }))
  try {
    assert.match(pane.container.textContent, /7 running31 held3 you41 waiting/)
    assert.match(pane.container.textContent, /\$12.34 today/)
    assert.equal(pane.container.querySelector('[data-sidebar-lane]'), null)
    await pane.click(pane.container.querySelector('.lanes-sb-heading'))
    assert.equal(localStorage.getItem(P.SIDEBAR_OPEN_KEY), '1')
    assert.equal(pane.container.querySelector('[data-sidebar-lane="idle"]'), null)
    await pane.click(pane.container.querySelector('[data-sidebar-lane="work"] button'))
    assert.equal(pane.container.querySelector('[data-step="ready"]').dataset.piling, '1')
    assert.match(pane.container.textContent, /boards: alpha, beta/)
    const setInput = async (label, value) => {
      const input = pane.container.querySelector(`[aria-label="work ${label}"]`)
      await React.act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, value)
        input.dispatchEvent(new window.Event('input', { bubbles: true }))
      })
    }
    await setInput('share', '30')
    await setInput('max workers', '')
    await setInput('cards / day', '24')
    await setInput('$ / day', '80.25')
    await pane.click(pane.container.querySelector('input[type=checkbox]'))
    await pane.click(pane.container.querySelector('button[type=submit]'))
    assert.deepEqual(t.writes[0], ['/v1/lanes/work', { timeoutMs: P.REQUEST_TIMEOUT_MS, method: 'PUT',
      body: { share: 30, max_workers: null, cards_per_day: 24, usd_per_day: 80.25, paused: true } }])
    await React.act(async () => t.push(undefined, Error('unreachable')))
    assert.match(pane.container.textContent, /Lanes offline - retrying/)
    assert.match(pane.container.textContent, /7 running31 held3 you41 waiting/)
    assert.ok(pane.container.querySelector('.lanes-last-known'))
    assert.ok(pane.container.querySelector('button[type=submit]').disabled)
  } finally { await pane.close() }
})

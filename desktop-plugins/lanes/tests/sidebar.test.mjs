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

test('governors collapse independently and persist across remount', async () => {
  localStorage.clear()
  const t = setup()
  const pane = await mount(React.createElement(P.SidebarLanes, { ctx: t.ctx }))
  try {
    const governors = pane.container.querySelector('[aria-label="GOVERNORS"]')
    assert.ok(governors, 'independent governor toggle exists')
    assert.equal(governors.getAttribute('aria-expanded'), 'false')
    await pane.click(governors)
    assert.ok(pane.container.querySelector('[data-governor="llm_pool"]'))
    assert.ok(pane.container.querySelector('.lanes-share-bar') === null)
    await pane.click(pane.container.querySelector('[aria-label="LANES"]'))
    await pane.click(pane.container.querySelector('[aria-label="LANES"]'))
    assert.equal(governors.getAttribute('aria-expanded'), 'true')
  } finally { await pane.close() }
  const remount = await mount(React.createElement(P.SidebarLanes, { ctx: t.ctx }))
  try {
    assert.equal(remount.container.querySelector('[aria-label="GOVERNORS"]').getAttribute('aria-expanded'), 'true')
    assert.equal(remount.container.querySelector('[aria-label="LANES"]').getAttribute('aria-expanded'), 'false')
    await remount.click(remount.container.querySelector('[aria-label="GOVERNORS"]'))
    assert.ok(remount.container.querySelector('[data-governor]') === null)
    assert.ok(remount.container.querySelector('details') === null)
  } finally { await remount.close(); localStorage.clear() }
})

for (const variant of ['loading', 'unknown', 'offline', 'long']) test(`collapsed sections keep ${variant} truth in their inline summaries`, async () => {
  localStorage.clear()
  const snap = variant === 'loading' ? undefined : snapshot()
  if (variant === 'unknown') { snap.flow_complete = false; snap.status.run = null; snap.governors = undefined }
  if (variant === 'long') { snap.status.run = 123456789012345; snap.governors.counts.unknown = 987654321098765 }
  const pane = await mount(React.createElement(P.SidebarView, { snap, error: variant === 'offline' ? 'unreachable' : '', open: false, setOpen() {} }))
  try {
    const headings = [...pane.container.querySelectorAll('.lanes-sb-heading')]
    assert.equal(headings.length, 2)
    assert.ok(pane.container.querySelector('.lanes-share-bar, [data-governor], details, [data-sidebar-lane]') === null)
    for (const heading of headings) {
      assert.equal(heading.getAttribute('aria-expanded'), 'false')
      const summary = heading.querySelector('.lanes-sb-summary')
      assert.ok(summary, 'summary is inside heading')
      assert.equal(summary.title, summary.textContent)
    }
    if (variant === 'loading') assert.match(headings[0].textContent, /Loading lanes/)
    if (variant === 'unknown') { assert.match(headings[0].textContent, /Partial.*— running/); assert.match(headings[1].textContent, /unknown/) }
    if (variant === 'offline') for (const heading of headings) assert.match(heading.textContent, /offline.*last known/i)
    if (variant === 'long') { assert.match(headings[0].textContent, /123456789012345/); assert.match(headings[1].textContent, /987654321098765 unknown/) }
  } finally { await pane.close() }
})

for (const open of [false, true]) test(`offline before any snapshot never claims last-known data (${open ? 'expanded' : 'collapsed'})`, async () => {
  localStorage.clear()
  if (open) localStorage.setItem(P.GOVERNORS_OPEN_KEY, '1')
  const pane = await mount(React.createElement(P.SidebarView, { snap: undefined, error: 'unreachable', open, setOpen() {} }))
  try {
    const [lanes, governors] = pane.container.querySelectorAll('.lanes-sb-heading')
    assert.equal(lanes.getAttribute('aria-expanded'), String(open))
    assert.equal(governors.getAttribute('aria-expanded'), String(open))
    assert.match(lanes.textContent, /Lanes offline - retrying · Loading lanes…/)
    assert.match(governors.textContent, /Governors offline · Loading governors…/)
    for (const heading of [lanes, governors]) assert.equal(heading.getAttribute('aria-description'), heading.querySelector('.lanes-sb-summary').title)
    assert.doesNotMatch(pane.container.textContent, /last known/i)
    assert.ok(pane.container.querySelector('.lanes-last-known') === null, 'nothing is styled as last-known data')
    assert.equal(pane.container.querySelectorAll('[data-sidebar-body]').length, open ? 2 : 0)
    if (open) {
      assert.equal(pane.container.querySelector('[data-sidebar-body="lanes"] [role=status]').textContent, 'Lanes offline - retrying')
      assert.match(pane.container.querySelector('[data-sidebar-body="lanes"]').textContent, /Loading lanes…/)
      assert.match(pane.container.querySelector('[data-sidebar-body="governors"]').textContent, /Loading governors…/)
    }
  } finally { await pane.close(); localStorage.clear() }
})

test('offline after a snapshot labels both expanded bodies as last known', async () => {
  localStorage.clear()
  localStorage.setItem(P.GOVERNORS_OPEN_KEY, '1')
  const pane = await mount(React.createElement(P.SidebarView, { snap: snapshot(), error: 'unreachable', open: true, setOpen() {} }))
  try {
    assert.equal(pane.container.querySelector('[data-sidebar-body="lanes"] [role=status]').textContent, 'Lanes offline - retrying · last known')
    assert.equal(pane.container.querySelectorAll('[data-sidebar-body].lanes-last-known').length, 2)
  } finally { await pane.close(); localStorage.clear() }
})

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
    assert.match(pane.container.textContent, /7 running · 31 governor-held · 3 you · 41 waiting/)
    assert.doesNotMatch(pane.container.textContent, /\$12.34 today/)
    assert.ok(pane.container.querySelector('.lanes-share-bar') === null)
    assert.ok(pane.container.querySelector('[data-governor]') === null)
    assert.ok(pane.container.querySelector('[data-sidebar-lane]') === null)
    await pane.click(pane.container.querySelector('.lanes-sb-heading'))
    assert.match(pane.container.textContent, /\$12.34 today/)
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
    assert.match(pane.container.textContent, /7 running · 31 governor-held · 3 you · 41 waiting/)
    assert.ok(pane.container.querySelector('.lanes-last-known'))
    assert.ok(pane.container.querySelector('button[type=submit]').disabled)
  } finally { await pane.close() }
})

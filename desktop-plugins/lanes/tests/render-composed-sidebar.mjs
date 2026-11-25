// Real producer collapsed/expanded views; SDK transport only is substituted.
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { loadPlugin, mount, React } from './sdk-mock.mjs'
const { collapsed, expanded } = JSON.parse(await readFile(process.argv[2], 'utf8'))
const P = await loadPlugin({ SIDEBAR_SECTIONS_TOP_AREA: 'sidebar.sections.top',
  SESSIONS_TAB_STRIP_AREA: 'sessions.tabStrip', SESSION_FILTER_AREA: 'sidebar.filter' })
const h = React.createElement
for (const order of ['sidebar-first', 'strip-first']) {
  localStorage.clear()
  localStorage.setItem(P.SIDEBAR_OPEN_KEY, '1')
  localStorage.setItem(P.OPEN_KEY, '0')
  localStorage.setItem(P.STORE_KEY, '48')
  P.saveSelected(['babybear']) // invisible legacy pills must not scope modern totals
  const registered = new Map(), requests = []
  const ctx = { onDispose() {}, register(c) { registered.set(c.id, c); return () => registered.delete(c.id) } }
  const source = P.sourceFor(ctx)
  source.subscribe = (params, onView) => {
    const deliver = next => { requests.push(next); onView(next.expanded ? expanded : collapsed) }
    deliver(params)
    return Object.assign(() => {}, { update: deliver, refresh() {} })
  }
  P.default.register(ctx)
  const tabs = await mount(registered.get('tab-totals').render())
  let sidebar, strip
  const openSidebar = async () => { sidebar = await mount(registered.get('lanes-sidebar').render()) }
  const openStrip = async () => { strip = await mount(registered.get('strip').render()) }
  const limits = async () => {
    await sidebar.flush()
    const row = sidebar.container.querySelector('[data-sidebar-lane="babybear"]')
    assert.ok(row, 'real producer lane rendered')
    if (!row.querySelector('.lanes-controls')) await sidebar.click(row.querySelector('button'))
    for (const [label, expected] of [['max workers', '3'], ['cards / day', '7'], ['$ / day', '12.5']])
      assert.equal(sidebar.container.querySelector(`[aria-label="babybear ${label}"]`).value, expected, `${order}: ${label}`)
    assert.equal(requests.at(-1).expanded, true, 'sidebar retains expanded projection')
    assert.equal(requests.at(-1).hours, 6, 'hidden legacy window cannot change sidebar scope')
    assert.equal(registered.has('session-filter'), false, 'invisible legacy selection does not register filter')
  }
  try {
    if (order === 'sidebar-first') { await openSidebar(); await openStrip() }
    else { await openStrip(); await openSidebar() }
    await limits()
    await strip.close(); strip = undefined
    await openStrip(); await limits()
    await React.act(async () => window.dispatchEvent(new CustomEvent('hermes-show-lanes')))
    await sidebar.flush(); await limits()
    assert.equal(P.loadSelected()[0], 'babybear', 'legacy selection remains available to older SDKs')
  } finally {
    await strip?.close(); await sidebar?.close(); await tabs.close(); localStorage.clear()
  }
}
console.log('real producer composed sidebar mount-order/remount/limits PASS')

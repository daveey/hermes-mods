// Actual dispatcher view and actual HTTP API; only Desktop SDK transport is replaced.
import assert from 'node:assert/strict'
import { loadPlugin, mount, React } from './sdk-mock.mjs'
const endpoint = process.argv[2]
const P = await loadPlugin({ SIDEBAR_SECTIONS_TOP_AREA: 'sidebar.sections.top' })
const get = async () => { const r = await fetch(`${endpoint}/v1/lanes?hours=24&window=24h&expanded=true`); assert.equal(r.status, 200); return r.json() }
let body = await get(), push
const writes = []
const ctx = { onDispose() {}, register() { return () => {} }, rest: async (path, opts) => {
  if (opts?.method === 'PUT') {
    const r = await fetch(endpoint + path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(opts.body) })
    assert.equal(r.status, 200, await r.text()); writes.push(opts.body)
    return { ok: true }
  }
  return get()
} }
P.sourceFor(ctx).subscribe = (_params, cb) => { push = cb; cb(body); return Object.assign(() => {}, { update() {}, refresh() {} }) }
localStorage.clear(); localStorage.setItem(P.SIDEBAR_OPEN_KEY, '1')
await P.sourceFor(ctx).read(24, '24h', true)
const pane = await mount(React.createElement(P.SidebarLanes, { ctx }))
try {
  const row = pane.container.querySelector('[data-sidebar-lane="babybear"]')
  const rejecting = row.querySelector('[data-tick="rejecting"]')
  assert.ok(rejecting, 'real dispatcher rejecting sample reaches DOM')
  assert.equal(rejecting.querySelectorAll('rect')[1].getAttribute('fill'), '#ef4444')
  assert.match(rejecting.querySelector('title').textContent, /1 rejected/)
  const slider = row.querySelector('[role=slider]')
  assert.equal(slider.getAttribute('aria-disabled'), 'false')
  slider.getBoundingClientRect = () => ({ left: 0, width: 200, right: 200 })
  const pointer = (type, x) => pane.event(slider, type, { clientX: x })
  for (const [fraction, cap] of [[2 / 6 * .97, 2], [.99, null], [0, 0]]) {
    await pointer('pointerdown', fraction * 200)
    assert.equal(writes.length, cap === 2 ? 0 : cap === null ? 1 : 2, 'release-only commit')
    await pointer('pointerup', fraction * 200)
    assert.equal(writes.at(-1).max_workers, cap)
    assert.equal(slider.dataset.saving, '1', 'HTTP receipt is not push confirmation')
    // The producer confirmation can follow the PUT; the slider must retain saving meanwhile.
    const deadline = Date.now() + 20000
    do {
      body = await get()
      if (body.lanes.babybear.limits.max_workers === cap) break
      await new Promise(resolve => setTimeout(resolve, 100))
    } while (Date.now() < deadline)
    assert.equal(body.lanes.babybear.limits.max_workers, cap, 'real hub/core limits readback')
    await React.act(async () => push(body))
    assert.equal(slider.dataset.saving, '0')
  }
  console.log('dispatcher -> projected rejecting -> red DOM; pointer -> PUT -> real core limits 2/null/0 PASS')
} finally { await pane.close(); localStorage.clear() }

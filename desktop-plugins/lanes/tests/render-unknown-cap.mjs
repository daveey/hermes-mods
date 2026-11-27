// The actual Python view.project/present failure projection, not a hand-built slider row.
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { loadPlugin, mount, React } from './sdk-mock.mjs'
const view = JSON.parse(await readFile(process.argv[2], 'utf8'))
const P = await loadPlugin({ SIDEBAR_SECTIONS_TOP_AREA: 'sidebar.sections.top' })
localStorage.clear(); localStorage.setItem(P.SIDEBAR_OPEN_KEY, '1')
let writes = 0
const ctx = { onDispose() {}, rest: async (_p, opts) => { if (opts?.method === 'PUT') writes++; return view } }
P.sourceFor(ctx).subscribe = (_p, cb) => { cb(view); return Object.assign(() => {}, { refresh() {} }) }
const pane = await mount(React.createElement(P.SidebarLanes, { ctx }))
try {
  const slider = pane.container.querySelector('[role=slider]')
  assert.ok(slider)
  assert.equal(slider.getAttribute('aria-disabled'), 'true')
  assert.equal(slider.getAttribute('aria-valuenow'), null)
  assert.match(slider.title, /board-a: interrupted/)
  assert.equal(slider.textContent, '—')
  assert.doesNotMatch(slider.getAttribute('aria-valuetext'), /∞/)
  await pane.event(slider, 'pointerdown', { clientX: 100 })
  await pane.event(slider, 'pointerup', { clientX: 100 })
  assert.equal(writes, 0)
} finally { await pane.close(); localStorage.clear() }
console.log('failed flow -> real project/present -> disabled unknown cap DOM PASS')

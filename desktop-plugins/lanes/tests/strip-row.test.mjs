import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { loadPlugin, mount, React } from './sdk-mock.mjs'

const P = await loadPlugin()
const lane = (name) => ({ share: 20, strip: { blocked: 11, running: 7, other: 127, cap: 2, in_share: 2, over: 5, tokens: Array(36).fill(1000) },
  status: { you: 1, ext: 0, worker: 0, run: 1, dep: 0, triage: 0, other: 0 } })
const snap = { configured: true, lanes: Object.fromEntries(['babybear', 'games', 'askeve', 'hermes', 'other', 'research', 'personal'].map(n => [n, lane(n)])),
  status: { you: 18, ext: 56, worker: 24, run: 15, dep: 95, triage: 23, other: 0 } }

test('global filters stay on the strip row, right of the lane pills, however many lanes', async () => {
  const view = await mount(React.createElement(P.Lanes, { ctx: { rest: async () => snap } }))
  try {
    const mini = view.container.querySelector('.hermes-lanes-mini')
    const kids = [...mini.children].map(c => c.className)
    // Pills wrap inside their own box, so they never push the filters off the top row.
    assert.equal(kids[0], 'hermes-lanes-pills')
    assert.equal(mini.querySelectorAll(':scope > .hermes-lanes-pill').length, 0)
    assert.equal(mini.querySelectorAll('.hermes-lanes-pills > .hermes-lanes-pill').length, 7)
    const status = mini.querySelector(':scope > .hermes-lanes-status')
    assert.ok(status, 'global category filters are a direct child of the strip row')
    assert.ok(kids.indexOf('hermes-lanes-status') > kids.indexOf('hermes-lanes-pills'))
    assert.ok(kids.indexOf('hermes-lanes-strip-right') < kids.indexOf('hermes-lanes-status'))
  } finally { await view.close() }
  const css = readFileSync(new URL('../plugin.js', import.meta.url), 'utf8')
  assert.match(css, /\.hermes-lanes-mini\{[^}]*flex-wrap:wrap/)
  assert.match(css, /\.hermes-lanes-pills\{[^}]*min-width:min-content/)
  assert.match(css, /\.hermes-lanes-pills\{[^}]*flex-wrap:wrap;[^}]*flex:1 1 0;min-width:min-content/)
  assert.match(css, /\.hermes-lanes-mini\{[^}]*align-items:flex-start/)
  assert.match(css, /\.hermes-lanes-mini>\.hermes-lanes-strip-right,\.hermes-lanes-mini>\.hermes-lanes-status,\.hermes-lanes-mini>\.hermes-lanes-toggle[^{]*\{flex:none\}/)
})

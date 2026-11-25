import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { loadPlugin, mount, React } from './sdk-mock.mjs'
const { view, case: scenario } = JSON.parse(await readFile(process.argv[2], 'utf8'))
const P = await loadPlugin({ SIDEBAR_SECTIONS_AREA: 'sidebar.sections' })
const pane = await mount(React.createElement(P.SidebarView, {
  snap: view, open: true, setOpen() {}, write() {}, onHeld() {}
}))
try {
  assert.ok(pane.container.querySelector('[data-slot="lanes-sidebar"]'))
  const pool = pane.container.querySelector('[data-governor="llm_pool"]')
  if (scenario === 'pool') {
    assert.equal(pool.dataset.state, 'on')
    assert.match(pool.textContent, /1 held/)
    assert.match(pool.textContent, /% used.*accounts/)
    assert.match(pane.container.querySelector('[data-top-reason="babybear"]').textContent, /LLM pool/)
  } else if (scenario === 'recovered') {
    assert.equal(pool, null, 'pool moves into folded off list')
    const button = [...pane.container.querySelectorAll('button')].find(b => b.textContent.includes(' off ·'))
    await pane.click(button)
    assert.equal(pane.container.querySelector('[data-governor="llm_pool"]').dataset.state, 'off')
  } else if (scenario === 'gate') {
    const gate = pane.container.querySelector('[data-governor="lane_gate"]')
    assert.equal(gate.dataset.state, 'on')
    assert.match(gate.textContent, /1 held/)
    assert.match(gate.textContent, /2/)
  } else if (scenario === 'host') {
    const line = pane.container.querySelector('[data-governor="host_slots"]')
    assert.equal(line.dataset.state, 'on')
    assert.match(line.textContent, /1 held/)
    assert.match(line.textContent, /default 1/)
    assert.match(line.textContent, /beta 0/)
  } else if (scenario === 'host-released') {
    assert.equal(pane.container.querySelector('[data-governor="host_slots"][data-state="on"]'), null)
  } else if (scenario === 'overlap') {
    assert.match(pane.container.querySelector('[data-governor="placement"]').textContent, /1 held/)
    assert.notEqual(pool?.dataset.state, 'on', 'overlap must not be charged again')
    assert.match(pane.container.querySelector('[data-sidebar-totals]').textContent, /1 held/)
  } else if (scenario === 'incomplete') {
    assert.match(pane.container.querySelector('[data-top-reason="babybear"]').textContent, /unknown/i)
    await pane.click(pane.container.querySelector('[data-sidebar-lane="babybear"] button'))
    assert.equal(pane.container.querySelector('[data-step]'), null)
    const held = [...pane.container.querySelectorAll('button')].find(b => b.textContent.startsWith('Show '))
    assert.equal(held.disabled, true)
    assert.match(held.textContent, /Show — held/)
  } else if (scenario === 'unknown' || scenario === 'adapter-stale') {
    assert.equal(pool.dataset.state, scenario === 'unknown' ? 'unknown' : 'on')
    await pane.click(pane.container.querySelector('[data-sidebar-lane="babybear"] button'))
    assert.equal(pane.container.querySelector('[data-device-footnote]').dataset.deviceFootnote, 'unknown')
  }
  console.log('real producer sidebar acceptance PASS:', scenario)
} finally { await pane.close() }

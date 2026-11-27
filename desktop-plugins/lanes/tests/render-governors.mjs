import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { loadPlugin, mount, React } from './sdk-mock.mjs'
const { view, case: scenario, failed_lane, healthy_lane } = JSON.parse(await readFile(process.argv[2], 'utf8'))
const P = await loadPlugin({ SIDEBAR_SECTIONS_AREA: 'sidebar.sections' })
// These views came through the real dispatcher/producer. An absent category in
// its row histogram is zero, not missing telemetry (unknown is its own state).
const states = ['on', 'off', 'at_cap', 'unknown']
const counts = Object.fromEntries(states.map(state => [state, view.governors.rows.filter(row => row.state === state).length]))
assert.deepEqual(view.governors.counts, counts, 'producer publishes a complete state histogram, including zero buckets')
const expected = `${counts.on} on · ${counts.off} off · ${counts.at_cap} at cap · ${counts.unknown} unknown`
const pane = await mount(React.createElement(P.SidebarView, {
  snap: view, open: true, setOpen() {}, write() {}, onHeld() {}
}))
try {
  const heading = pane.container.querySelector('[aria-label="GOVERNORS"]')
  const summary = heading.querySelector('.lanes-sb-summary')
  assert.equal(summary.textContent, expected)
  assert.equal(summary.title, expected)
  assert.equal(heading.getAttribute('aria-description'), expected)
  assert.ok(pane.container.querySelector('[data-slot="lanes-sidebar"]'))
  assert.ok(pane.container.querySelector('[data-sidebar-body="governors"]') === null, 'governors start minimized')
  await pane.click(pane.container.querySelector('[aria-label="GOVERNORS"]'))
  const pool = pane.container.querySelector('[data-governor="llm_pool"]')
  if (scenario === 'pool') {
    assert.equal(pool.dataset.state, 'on')
    assert.match(pool.textContent, /1 held/)
    assert.match(pool.textContent, /% used.*accounts/)
    assert.match(pane.container.querySelector('[data-top-reason="babybear"]').textContent, /LLM pool/)
  } else if (scenario === 'recovered') {
    assert.equal(pool, null, 'pool moves into folded off list')
    const button = [...pane.container.querySelectorAll('[data-sidebar-body="governors"] button')].find(b => b.textContent.includes(' off ·'))
    await pane.click(button)
    assert.equal(pane.container.querySelector('[data-governor="llm_pool"]').dataset.state, 'off')
  } else if (scenario === 'gate') {
    const gate = pane.container.querySelector('[data-governor="lane_gate"]')
    assert.equal(gate.dataset.state, 'on')
    assert.match(gate.textContent, /1 held/)
    assert.match(gate.textContent, /2/)
    // Lane-gated cards are only a subset of dispatch governor charges.
    const laneHeading = pane.container.querySelector('[aria-label="LANES"]')
    const laneSummary = laneHeading.querySelector('.lanes-sb-summary')
    assert.match(laneSummary.textContent, /2 governor-held/)
    assert.equal(laneSummary.title, laneSummary.textContent)
    assert.equal(laneHeading.getAttribute('aria-description'), laneSummary.textContent)
    assert.equal(view.totals.held, 1)
    assert.match(pane.container.querySelector('[data-sidebar-totals]').textContent, /2 governor-held/)
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
    assert.match(pane.container.querySelector('[data-sidebar-totals]').textContent, /1 governor-held/)
  } else if (scenario === 'partial-summary') {
    // The same real projection must stay honest in both SDK mounts.
    const old = await loadPlugin()
    const ctx = { rest: async () => view }
    old.sourceFor(ctx).subscribe = (_p, cb) => {
      cb(view)
      return Object.assign(() => {}, { update() {}, refresh() {} })
    }
    await old.sourceFor(ctx).read(6, '6h', true)
    const strip = await mount(React.createElement(old.Lanes, { ctx }))
    try {
      for (const name of Object.keys(view.lanes)) {
        const pill = strip.container.querySelector(`[data-lane-pill="${name}"]`)
        assert.ok(pill, 'unknown lane remains visible on the old SDK')
        assert.match(pill.textContent, /partial · —/)
        assert.match(pill.getAttribute('aria-label'), /— running, — waiting/)
      }
    } finally { await strip.close() }
    const legacy = await mount(React.createElement(P.Panel, { snap: view, onApply() {}, onWindow() {} }))
    try {
      assert.match(legacy.container.textContent, /Partial lanes data/)
      assert.match(legacy.container.querySelector('.hermes-lanes-head').textContent, /— running · — open · all hub boards/)
      assert.doesNotMatch(legacy.container.textContent, /null running|0% actual/)
    } finally { await legacy.close() }
    for (const [name, row] of Object.entries(view.lanes)) {
      const pill = await mount(React.createElement(P.MiniPill, { row: { name, ...row }, onToggle() {} }))
      try {
        assert.match(pill.container.querySelector('button').getAttribute('aria-label'), /— running, — waiting/)
      } finally { await pill.close() }
    }
    assert.match(pane.container.querySelector('[data-sidebar-totals]').textContent, /— waiting/)
    assert.doesNotMatch(pane.container.querySelector('[data-sidebar-totals]').textContent, /0 waiting/)
    assert.match(pane.container.textContent, /Partial lanes data/)
    assert.equal(pane.container.querySelector('.lanes-share-bar'), null)
    for (const row of pane.container.querySelectorAll('[data-sidebar-lane]')) {
      await pane.click(row.querySelector('button'))
      assert.match(row.querySelector('[data-lane-counts]').textContent, /— running/)
      assert.match(row.querySelector('[data-lane-counts]').textContent, /— waiting/)
      assert.match(row.querySelector('.lanes-target-share').textContent, /—/)
      assert.equal(row.querySelector('meter'), null)
    }
  } else if (scenario === 'partial-status') {
    assert.match(pane.container.querySelector('[data-sidebar-totals]').textContent, /— running/)
    assert.match(pane.container.textContent, /Partial lanes data/)
    assert.equal(pane.container.querySelector('[data-fleet-spend]').getAttribute('aria-label'), P.spendLabel(view.spend), 'spend remains independent of flow failure')
    const bad = pane.container.querySelector(`[data-sidebar-lane="${failed_lane}"]`)
    const good = pane.container.querySelector(`[data-sidebar-lane="${healthy_lane}"]`)
    await pane.click(bad.querySelector('button'))
    assert.match(bad.querySelector('[data-lane-counts]').textContent, /— running/)
    await pane.click(good.querySelector('button'))
    assert.match(good.querySelector('[data-lane-counts]').textContent, /1 running/)
  } else if (scenario === 'incomplete') {
    assert.match(pane.container.querySelector('[data-top-reason="babybear"]').textContent, /unknown/i)
    await pane.click(pane.container.querySelector('[data-sidebar-lane="babybear"] button'))
    assert.equal(pane.container.querySelector('[data-step]'), null)
    const held = [...pane.container.querySelectorAll('button')].find(b => b.textContent.startsWith('Show '))
    assert.equal(held.disabled, true)
    assert.match(held.textContent, /Show — held/)
  } else if (scenario === 'unknown' || scenario === 'adapter-stale') {
    assert.equal(pool.dataset.state, scenario === 'unknown' ? 'unknown' : 'on')
    if (scenario === 'unknown') assert.match(pool.textContent, /— held/)
    await pane.click(pane.container.querySelector('[data-sidebar-lane="babybear"] button'))
    assert.equal(pane.container.querySelector('[data-device-footnote]').dataset.deviceFootnote, 'unknown')
  }
  console.log('real producer sidebar acceptance PASS:', scenario)
} finally { await pane.close() }

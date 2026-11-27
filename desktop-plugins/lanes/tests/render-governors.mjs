import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { loadPlugin, mount, React } from './sdk-mock.mjs'
const { view, case: scenario, failed_lane, healthy_lane } = JSON.parse(await readFile(process.argv[2], 'utf8'))
const P = await loadPlugin({ SIDEBAR_SECTIONS_AREA: 'sidebar.sections' })
// These views came through the real dispatcher/producer. Governors are folded into
// lane rows: gov.holds (per lane), gov.fleet lines, gov.stale_sources, gov.held_total.
const gov = view.governors
const pane = await mount(React.createElement(P.SidebarView, {
  snap: view, open: true, setOpen() {}, write() {}, onHeld() {}
}))
try {
  const text = el => el.textContent
  const totals = pane.container.querySelector('[data-sidebar-totals]')
  const heading = pane.container.querySelector('[aria-label="LANES"]')
  // Governors are no longer a section: no heading, no per-governor rows.
  assert.equal(pane.container.querySelector('[aria-label="GOVERNORS"]'), null)
  assert.equal(pane.container.querySelector('[data-governor]'), null)
  assert.equal(pane.container.querySelector('[data-sidebar-body="governors"]'), null)
  assert.ok(pane.container.querySelector('[data-slot="lanes-sidebar"]'))
  assert.equal(pane.container.querySelectorAll('.lanes-sb-heading').length, 1)
  const fleetIds = [...pane.container.querySelectorAll('[data-fleet-governor]')].map(e => e.dataset.fleetGovernor)
  const chips = lane => [...pane.container.querySelectorAll(`[data-lane-holds="${lane}"] [data-lane-hold]`)].map(e => e.dataset.laneHold)
  const holdText = (lane, id) => text(pane.container.querySelector(`[data-lane-holds="${lane}"] [data-lane-hold="${id}"]`))
  const allChips = () => [...pane.container.querySelectorAll('[data-lane-hold]')].map(e => e.dataset.laneHold)
  if (gov) {
    assert.deepEqual(fleetIds, (gov.fleet || []).map(f => f.id), 'every producer fleet line renders once, in order')
    if (gov.held_total != null) assert.match(text(totals), new RegExp(`${gov.held_total} held`))
    const stale = pane.container.querySelector('[data-stale-sources]')
    if ((gov.stale_sources || []).length) {
      assert.match(text(stale), new RegExp(`${gov.stale_sources.length} sources? stale`))
      for (const s of gov.stale_sources) assert.ok(text(stale).includes(s.board))
    } else assert.equal(stale, null)
  }
  assert.doesNotMatch(text(totals), /governor-held/)

  if (scenario === 'pool') {
    assert.deepEqual(fleetIds, ['llm_pool'])
    assert.match(text(pane.container.querySelector('[data-fleet-governor="llm_pool"]')), /1 waiting.*% used.*accounts/)
    assert.equal(gov.held_total, 1)
    assert.match(text(totals), /1 held/)
  } else if (scenario === 'recovered') {
    assert.equal(pane.container.querySelector('[data-fleet-governor="llm_pool"]'), null, 'recovered pool leaves no fleet line')
    assert.equal(gov.held_total, 0)
    assert.match(text(totals), /0 held/)
  } else if (scenario === 'gate') {
    // The producer folds lane-gated cards onto the lane that owns the gate.
    assert.ok(chips('babybear').includes('lane_gate'))
    assert.match(holdText('babybear', 'lane_gate'), /Lane gate.* · 1/)
    assert.equal(gov.held_total, 2)
    assert.match(text(totals), /2 held/)
    assert.equal(view.totals.held, 1)
    const summary = heading.querySelector('.lanes-sb-summary')
    assert.match(summary.textContent, /2 held/)
    assert.equal(summary.title, summary.textContent)
    assert.equal(heading.getAttribute('aria-description'), summary.textContent)
    await pane.click(pane.container.querySelector('[data-sidebar-lane="babybear"] button'))
    const holding = [...pane.container.querySelectorAll('[data-sidebar-lane="babybear"] label[data-holding="1"]')].map(l => text(l))
    assert.ok(holding.some(t => /admit \/ day.*holding/.test(t)), 'gated admit/day field is marked holding')
    assert.ok(pane.container.querySelector('[aria-label="babybear admit / day"]').value === '2', 'admit/day shows the configured value')
  } else if (scenario === 'host') {
    const line = pane.container.querySelector('[data-fleet-governor="host_slots"]')
    assert.ok(line, 'host slots is a fleet line, not a lane chip')
    assert.match(text(line), /1 waiting/)
    assert.equal(allChips().includes('host_slots'), false)
    assert.equal(gov.held_total, 1)
    assert.match(text(totals), /1 held/)
  } else if (scenario === 'host-released') {
    assert.equal(pane.container.querySelector('[data-fleet-governor="host_slots"]'), null)
    assert.match(text(totals), /0 held/)
  } else if (scenario === 'overlap') {
    // Charged once, to placement (a fault): can't-start chip, counted for you, not held.
    const placement = pane.container.querySelectorAll('[data-lane-hold="fault"]')
    assert.equal(placement.length, 1)
    assert.match(text(placement[0]), /1 can't start/)
    assert.equal(pane.container.querySelector('[data-fleet-governor="llm_pool"]'), null, 'overlap must not be charged again')
    assert.equal(allChips().includes('llm_pool'), false)
    assert.equal(gov.held_total, 0)
    assert.match(text(totals), /0 held/)
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
    assert.match(text(totals), /— waiting/)
    assert.doesNotMatch(text(totals), /0 waiting/)
    assert.match(pane.container.textContent, /Partial lanes data/)
    assert.equal(pane.container.querySelector('.lanes-share-bar'), null)
    for (const row of pane.container.querySelectorAll('[data-sidebar-lane]')) {
      // Unknown lanes say so on the row and never invent zero holds or waits.
      assert.match(text(row.querySelector('[data-lane-holds]')), /board reading stale|—/)
      await pane.click(row.querySelector('button'))
      assert.match(row.querySelector('[data-lane-counts]').textContent, /— running/)
      assert.match(row.querySelector('[data-lane-counts]').textContent, /— waiting/)
      assert.match(row.querySelector('.lanes-target-share').textContent, /—/)
      assert.equal(row.querySelector('meter'), null)
    }
  } else if (scenario === 'partial-status') {
    assert.match(text(totals), /— running/)
    assert.match(pane.container.textContent, /Partial lanes data/)
    assert.equal(pane.container.querySelector('[data-fleet-spend]').getAttribute('aria-label'), P.spendLabel(view.spend), 'spend remains independent of flow failure')
    const bad = pane.container.querySelector(`[data-sidebar-lane="${failed_lane}"]`)
    const good = pane.container.querySelector(`[data-sidebar-lane="${healthy_lane}"]`)
    await pane.click(bad.querySelector('button'))
    assert.match(bad.querySelector('[data-lane-counts]').textContent, /— running/)
    await pane.click(good.querySelector('button'))
    assert.match(good.querySelector('[data-lane-counts]').textContent, /1 running/)
  } else if (scenario === 'incomplete') {
    // Incomplete lane evidence is unknown, not zero: stale marker, no holds, no waits numbers.
    const lane = pane.container.querySelector('[data-lane-holds="babybear"]')
    assert.match(text(lane), /board reading stale/i)
    assert.deepEqual(chips('babybear'), [])
    await pane.click(pane.container.querySelector('[data-sidebar-lane="babybear"] button'))
    assert.equal(pane.container.querySelector('[data-step]'), null)
    assert.match(text(pane.container.querySelector('[data-lane-waits="babybear"]')), /— on parents/)
    const held = [...pane.container.querySelectorAll('button')].find(b => b.textContent.startsWith('Show '))
    assert.equal(held.disabled, true)
    assert.match(held.textContent, /Show — held/)
  } else if (scenario === 'unknown' || scenario === 'adapter-stale') {
    if (scenario === 'unknown') {
      // Missing or stale core telemetry: sources are named stale; no fleet line is invented.
      assert.ok((gov?.stale_sources || []).length > 0 || !gov, 'unknown telemetry is reported as stale sources')
      if (gov) assert.match(text(pane.container.querySelector('[data-stale-sources]')), /stale/)
      else assert.match(pane.container.textContent, /Governor source unknown/)
    } else {
      assert.deepEqual(fleetIds, ['llm_pool'])
      assert.equal(gov.stale_sources.length, 0, 'a stale device adapter is a footnote, not a stale board')
    }
    await pane.click(pane.container.querySelector('[data-sidebar-lane="babybear"] button'))
    assert.equal(pane.container.querySelector('[data-device-footnote]').dataset.deviceFootnote, 'unknown')
  }
  console.log('real producer sidebar acceptance PASS:', scenario)
} finally { await pane.close() }

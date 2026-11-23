import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount, React } from './sdk-mock.mjs'
const P = await loadPlugin()
const row = { name: 'work', target: .25, actual: .7, strip: { blocked: 11, running: 7, other: 127,
  cap: 2, in_share: 2, over: 5, cap_kind: 'fair share', fleet_running: 8,
  idle_lanes: ['idle'], wait: { dep: 100, slot: 27 }, tokens: Array(36).fill(1000) } }

test('C2 squares, split capsule, amber outline, tooltip, and unchanged selection', async () => {
  const clicked = []
  const view = await mount(React.createElement(P.MiniPill, { row, scale: 2000, onToggle: n => clicked.push(n) }))
  try {
    const pill = view.container.querySelector('button')
    assert.equal(pill.querySelector('.sq:not(.hollow)').textContent, '11')
    assert.equal(pill.querySelector('.cap').textContent, '2+5')
    assert.equal(pill.querySelector('[data-part=in-share]').textContent, '2')
    assert.equal(pill.querySelector('[data-part=over]').textContent, '+5')
    assert.equal(pill.querySelector('.hollow').textContent, '127')
    assert.ok(pill.classList.contains('is-over'))
    assert.match(pill.title, /tok\/m avg over 6 h\n11 blocked on you\n7 running · fair share 2 of 8/)
    assert.match(pill.title, /OVER by 5: borrowing idle slots — idle have nothing ready; 6 h actual 70% vs 25% target/)
    assert.match(pill.title, /127 waiting \(100 waits on a card, 27 no free worker\)/)
    assert.ok(pill.querySelector('svg polygon'))
    await view.click(pill)
    assert.deepEqual(clicked, ['work'])
  } finally { await view.close() }
})

test('C2 idle and zero-cap rows, omitted zeros, shared scale, old-hub fallback', async () => {
  const cases = [
    { strip: { blocked: 4, running: 0, other: 16, cap: 2, in_share: 0, over: 0 }, text: 'idle416', over: false },
    { strip: { blocked: 0, running: 3, other: 0, cap: 0, in_share: 0, over: 3 }, text: 'idle+3', over: true },
    { strip: { blocked: 0, running: 0, other: 0 }, text: 'idle', over: false },
    { strip: { blocked: 4, running: 2, other: 16 }, text: 'idle4216', over: false },
    { running: 2, blocked_on_you: 4, not_ready: 16, open: 22, text: 'idle4216', over: false }
  ]
  for (const c of cases) {
    const view = await mount(React.createElement(P.MiniPill, { row: { name: 'idle', ...c }, onToggle() {} }))
    try {
      const pill = view.container.querySelector('button')
      assert.equal(pill.textContent, c.text)
      assert.equal(pill.classList.contains('is-over'), c.over)
      assert.ok(pill.querySelector('svg') === null)
      assert.ok(!pill.textContent.includes('0'))
      if (!c.strip?.running && !c.running) assert.ok(pill.querySelector('.cap') === null)
    } finally { await view.close() }
  }
  assert.equal(P.tokenScale([row, { strip: { tokens: [10000] } }]), 10000)
  const view = await mount(React.createElement(P.LaneSpark, { tokens: [0, 1000], scale: 10000 }))
  try { assert.equal(view.container.querySelector('polyline').getAttribute('points'), '0.0,13.0 34.0,11.8') }
  finally { await view.close() }
})

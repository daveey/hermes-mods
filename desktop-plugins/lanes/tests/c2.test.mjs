import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount, React } from './sdk-mock.mjs'
const P = await loadPlugin()

test('rendered blue lane numerals sum to global running, even above fair share', async () => {
  const lanes = [
    { name: 'a', strip: { running: 1, in_share: 1, over: 0 } },
    { name: 'b', strip: { running: 18, in_share: 4, over: 14 } },
    { name: 'c', strip: { running: 3, in_share: 0, over: 3 } }
  ]
  const status = { run: 22 }
  const view = await mount(React.createElement('div', null,
    ...lanes.map(row => React.createElement(P.MiniPill, { key: row.name, row, onToggle() {} }))))
  try {
    const blue = [...view.container.querySelectorAll('[data-part=total-running]')]
    assert.equal(blue.length, 3)
    assert.equal(blue.reduce((sum, el) => sum + Number(el.textContent), 0), status.run)
  } finally { await view.close() }
})
const row = { name: 'work', target: .25, actual: .7, strip: { blocked: 11, running: 7, other: 127,
  cap: 2, in_share: 2, over: 5, cap_kind: 'fair share', fleet_running: 8,
  actual: .7, token_avg: 100, idle_lanes: ['idle'], wait: { dep: 100, slot: 27 }, tokens: Array(36).fill(1000) } }

test('C2 squares, total capsule, amber overage annotation and unchanged selection', async () => {
  assert.match(P.laneTitle({ ...row, actual: .1, strip: { ...row.strip, token_avg: 321 } }), /321 tok\/m avg/)
  assert.match(P.laneTitle({ ...row, actual: .1 }), /6 h actual 70%/)
  assert.doesNotMatch(P.laneTitle({ ...row, strip: { ...row.strip, actual: undefined } }), /6 h actual/)
  const clicked = []
  const view = await mount(React.createElement(P.MiniPill, { row, scale: 2000, onToggle: n => clicked.push(n) }))
  try {
    const pill = view.container.querySelector('button')
    assert.equal(pill.querySelector('[data-count=blocked]').textContent, '11')
    assert.equal(pill.querySelector('[data-count=running]').textContent, '7+5 over')
    assert.equal(pill.querySelector('[data-part=total-running]').textContent, '7')
    assert.equal(pill.querySelector('[data-part=over]').textContent, '+5 over')
    assert.equal(pill.querySelector('[data-count=other]').textContent, '127')
    // Each number sits inside its category square (the shared numbered card-square look).
    assert.deepEqual([...pill.querySelectorAll('[data-card-colour]')].map(n => [n.dataset.cardColour, n.textContent]),
      [['red', '11'], ['blue', '7'], ['amber', '+5 over'], ['outline', '127']])
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
    { strip: { blocked: 0, running: 3, other: 0, cap: 0, in_share: 0, over: 3 }, text: 'idle3+3 over', over: true },
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

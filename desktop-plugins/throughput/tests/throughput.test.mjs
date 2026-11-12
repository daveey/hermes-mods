// Throughput chip + panel: rate/unit math against hand-computed fixtures, rate-now windows, missing vs 0,
// legend filter, persisted controls, and the rendered chip/panel (charts, ⓘ, tooltip, range refetch).
import test from 'node:test'
import assert from 'node:assert/strict'
import { loadPlugin, mount, button, React } from './sdk-mock.mjs'

const P = await loadPlugin()
const END = 1_800_000_000 - (1_800_000_000 % 60)

// 1h range: 60 one-minute buckets. hub: 600 io tokens/min, 60 cache tokens/min, a session every other
// minute; worker-1 reports nothing for tokens/sessions; cards for both.
function payload(over = {}) {
  const n = 60
  const arr = (f) => Array.from({ length: n }, (_, i) => f(i))
  const rec = (f) => Array.from({ length: 15 }, (_, i) => f(i))
  return {
    now: END, since: END - 3600, bucket_s: 60, range: '1h', local: 'hub', recent_minutes: 15,
    gateways: ['hub', 'worker-1', 'ephemeral', 'unattributed'],
    series: {
      tokens_io: { hub: arr(() => 600), 'worker-1': null, ephemeral: null, unattributed: null },
      tokens_cache: { hub: arr(() => 60), 'worker-1': null, ephemeral: null, unattributed: null },
      sess_in: { hub: arr((i) => (i % 2 ? 1 : 0)), 'worker-1': null, ephemeral: null, unattributed: null },
      sess_out: { hub: arr((i) => (i % 2 ? 0 : 1)), 'worker-1': null, ephemeral: null, unattributed: null },
      cards_in: { hub: arr(() => 2), 'worker-1': arr(() => 1), ephemeral: arr(() => 0), unattributed: arr(() => 0) },
      cards_out: { hub: arr(() => 1), 'worker-1': arr((i) => (i === 59 ? 6 : 0)), ephemeral: arr(() => 0), unattributed: arr(() => 0) }
    },
    recent: {
      tokens_io: { hub: rec((i) => (i === 14 ? null : 600)), 'worker-1': null, ephemeral: null, unattributed: null },
      tokens_cache: { hub: rec(() => 60), 'worker-1': null, ephemeral: null, unattributed: null },
      sess_in: { hub: rec((i) => (i % 2 ? 1 : 0)), 'worker-1': null, ephemeral: null, unattributed: null },
      sess_out: { hub: rec(() => 0), 'worker-1': null, ephemeral: null, unattributed: null },
      cards_in: { hub: rec(() => 2), 'worker-1': rec(() => 1), ephemeral: rec(() => 0), unattributed: rec(() => 0) },
      cards_out: { hub: rec(() => 1), 'worker-1': rec((i) => (i === 14 ? 6 : 0)), ephemeral: rec(() => 0), unattributed: rec(() => 0) }
    },
    ...over
  }
}
const V = (o = {}) => ({ unit: 'm', range: '1h', cache: false, ...o })

test('fmt: compact numbers, missing is an en dash, 0 is 0', () => {
  assert.equal(P.fmt(56_400), '56k')
  assert.equal(P.fmt(5_400), '5.4k')
  assert.equal(P.fmt(5_400_000), '5.4M')
  assert.equal(P.fmt(1.5), '1.5')
  assert.equal(P.fmt(0.333), '0.33')
  assert.equal(P.fmt(0), '0')
  assert.equal(P.fmt(null), '\u2013')
})

test('toRate: counts per bucket to /s /m /h (hand-computed)', () => {
  // 30 in a 5-minute bucket = 0.1/s = 6/m = 360/h; null stays null
  assert.deepEqual(P.toRate([30, null, 0], 300, 's'), [0.1, null, 0])
  assert.deepEqual(P.toRate([30], 300, 'm'), [6])
  assert.deepEqual(P.toRate([30], 300, 'h'), [360])
  assert.deepEqual(P.toRate([90_000], 3600, 'm'), [1500])
  // per-bucket sampled seconds: 14000 tokens sampled for 7 of 60 minutes = 2000/m; 0 sampled = missing
  assert.deepEqual(P.toRate([14_000, 5, null], [420, 0, 3600], 'm'), [2000, null, null])
})

test('partly sampled buckets: rates use the sampled minutes; cards use whole buckets', () => {
  const p = { bucket_s: 3600, now: 7200, since: 0, gateways: ['hub'],
    series: { tokens_io: { hub: [null, 14_000] }, tokens_cache: { hub: [null, 700] }, cards_in: { hub: [60, 6] } },
    sampled: { tokens_io: { hub: [0, 7] }, tokens_cache: { hub: [0, 7] } } }
  assert.deepEqual(P.sampledSeconds(p, 'tokens', 'hub'), [0, 420])
  assert.equal(P.sampledSeconds(p, 'cards_in', 'hub'), null)
  assert.deepEqual(P.layers(p, 'tokens', ['hub'], V()).total, [null, 2000])
  assert.deepEqual(P.layers(p, 'tokens', ['hub'], V({ cache: true })).total, [null, 2100])
  assert.deepEqual(P.layers(p, 'cards_in', ['hub'], V()).total, [1, 0.1])
  // an older backend without `sampled`: whole buckets
  delete p.sampled
  assert.deepEqual(P.layers(p, 'tokens', ['hub'], V()).total.map((x) => x && Math.round(x)), [null, 233])
})

test('rate now: last 5 min on 1h, 15 min otherwise; unsampled minutes are excluded, not 0', () => {
  const p = payload()
  // tokens: last 5 minutes = 600,600,600,600,null -> 4 sampled minutes of 600 -> 600/m
  assert.equal(P.rateNow(p, 'tokens', p.gateways, V()), 600)
  assert.equal(P.rateNow(p, 'tokens', p.gateways, V({ unit: 's' })), 10)
  assert.equal(P.rateNow(p, 'tokens', p.gateways, V({ unit: 'h' })), 36000)
  // +cache: last minute has cache only (60) -> (4*660 + 60) / 5 = 540/m
  assert.equal(P.rateNow(p, 'tokens', p.gateways, V({ cache: true })), 540)
  // cards done: hub 1/min + worker-1 6 in the last minute; 5 min -> (5+6)/5 = 2.2/m; 15 min -> (15+6)/15 = 1.4/m
  assert.equal(P.rateNow(p, 'cards_out', p.gateways, V()), 2.2)
  assert.equal(P.rateNow(p, 'cards_out', p.gateways, V({ range: '6h' })).toFixed(6), '1.400000')
  // sessions on 15 minutes: odd minutes 1..13 -> 7 / 15 per minute
  assert.equal(P.rateNow(p, 'sess_in', p.gateways, V({ range: '24h' })).toFixed(4), (7 / 15).toFixed(4))
  // only non-reporting gateways selected: unknown, not 0
  assert.equal(P.rateNow(p, 'tokens', ['worker-1'], V()), null)
  assert.equal(P.rateNow(p, 'cards_out', ['ephemeral'], V()), 0)
})

test('layers: stacked per visible reporting gateway, totals keep missing buckets null', () => {
  const p = payload()
  p.series.tokens_io.hub[3] = null
  const L = P.layers(p, 'tokens', p.gateways, V())
  assert.deepEqual(L.layers.map((l) => l.g), ['hub'])
  assert.equal(L.total[0], 600)
  assert.equal(L.total[3], null)
  const C = P.layers(p, 'cards_out', p.gateways, V({ unit: 'h' }))
  assert.equal(C.layers.length, 4)
  assert.equal(C.total[59], (1 + 6) * 60)
  assert.deepEqual(P.notReporting(p, 'tokens', p.gateways), ['worker-1', 'ephemeral', 'unattributed'])
  assert.deepEqual(P.notReporting(p, 'cards_in', p.gateways), [])
})

test('legend: click = only this one, again = all; shift-click = hide; All resets', () => {
  let f = { solo: null, hidden: [] }
  f = P.legendClick(f, 'hub', false)
  assert.deepEqual(P.visibleGateways(['hub', 'w'], f), ['hub'])
  assert.deepEqual(P.legendClick(f, 'hub', false), { solo: null, hidden: [] })
  f = P.legendClick({ solo: null, hidden: [] }, 'w', true)
  assert.deepEqual(P.visibleGateways(['hub', 'w'], f), ['hub'])
  assert.deepEqual(P.legendClick(f, 'w', true).hidden, [])
  assert.deepEqual(P.legendClick(f, 'all'), { solo: null, hidden: [] })
})

test('view persists in localStorage and rejects junk', () => {
  localStorage.clear()
  assert.deepEqual(P.loadView(), P.DEFAULT_VIEW)
  P.saveView({ unit: 'h', range: '7d', cache: true })
  assert.deepEqual(P.loadView(), { unit: 'h', range: '7d', cache: true })
  localStorage.setItem(P.STORE_KEY, JSON.stringify({ unit: 'x', range: '2d', cache: 'yes' }))
  assert.deepEqual(P.loadView(), P.DEFAULT_VIEW)
  localStorage.setItem(P.STORE_KEY, '{broken')
  assert.deepEqual(P.loadView(), P.DEFAULT_VIEW)
  localStorage.clear()
})

function ctxWith(make) {
  const calls = []
  return { calls, rest: async (path, opts) => { calls.push([path, opts]); return make(path) } }
}
const forRange = (path) => {
  const r = new URL('http://x' + path).searchParams.get('range')
  const p = payload()
  return { ...p, range: r }
}

test('chip shows tokens and cards done per unit; panel renders three charts with – and ⓘ for non-reporters', async () => {
  localStorage.setItem(P.STORE_KEY, JSON.stringify({ unit: 'm', range: '1h', cache: false }))
  const ctx = ctxWith(forRange)
  const ui = await mount(React.createElement(P.Throughput, { ctx }))
  try {
    assert.equal(ctx.calls[0][0], '/v1/series?range=1h')
    const chip = button('Show throughput')
    assert.match(chip.textContent, /600 tok\/m · 2\.2 cards\/m/)
    assert.ok(chip.querySelector('svg polyline'), 'sparkline')
    await ui.click(chip)
    const charts = [...document.querySelectorAll('[data-chart]')].map((e) => e.dataset.chart)
    assert.deepEqual(charts, ['tokens', 'sessions', 'cards'])
    const big = (m) => document.querySelector(`[data-now="${m}"]`).textContent
    assert.equal(big('tokens'), '600')
    assert.equal(big('cards_out'), '2.2')
    const info = document.querySelector('[data-chart="tokens"] .hermes-tp-info')
    assert.match(info.dataset.tip, /Not reported by worker-1, ephemeral, unattributed/)
    assert.equal(info.getAttribute('title'), null) // CSS tooltip, not a native title
    assert.ok(document.querySelector('[data-chart="cards"] .hermes-tp-info') === null)
    // worker-1 draws card layers but no token layer
    assert.ok(document.querySelector('[data-chart="tokens"] path[data-gw="worker-1"]') === null)
    assert.ok(document.querySelector('[data-chart="cards"] path[data-gw="worker-1"][data-side="down"]'))

    // unit /s: numbers rescale and the choice persists
    await ui.click(button('/s'))
    assert.equal(big('tokens'), '10')
    assert.equal(JSON.parse(localStorage.getItem(P.STORE_KEY)).unit, 's')
    // range 24h: refetches that range
    await ui.click(button('24h'))
    assert.equal(ctx.calls.at(-1)[0], '/v1/series?range=24h')
    assert.equal(JSON.parse(localStorage.getItem(P.STORE_KEY)).range, '24h')
    // +cache
    await ui.click(button('+cache'))
    assert.equal(JSON.parse(localStorage.getItem(P.STORE_KEY)).cache, true)
    assert.match(document.querySelector('[data-chart="tokens"] .hermes-tp-lbl').textContent, /in\+out\+cache\/s/)

    // legend: only worker-1 -> tokens big number is unknown (–), cards still counted
    await ui.click(document.querySelector('.hermes-tp-legend button[data-gw="worker-1"]'))
    assert.equal(big('tokens'), '\u2013')
    assert.ok(document.querySelector('[data-chart="cards"] path[data-gw="hub"]') === null)
    await ui.click(button('All gateways'))
    assert.ok(document.querySelector('[data-chart="cards"] path[data-gw="hub"]'))
    // shift-click hides one
    await ui.click(document.querySelector('.hermes-tp-legend button[data-gw="hub"]'), { shiftKey: true })
    assert.ok(document.querySelector('[data-chart="cards"] path[data-gw="hub"]') === null)
    assert.ok(document.querySelector('.hermes-tp-legend button[data-gw="hub"]').classList.contains('is-off'))
  } finally {
    await ui.close()
    localStorage.clear()
  }
})

test('hover shows per-gateway values and – for gateways without data', async () => {
  localStorage.setItem(P.STORE_KEY, JSON.stringify({ unit: 'm', range: '1h', cache: false }))
  const ui = await mount(React.createElement(P.Throughput, { ctx: ctxWith(forRange) }))
  try {
    await ui.click(button('Show throughput'))
    const plot = document.querySelector('[data-chart="tokens"] .hermes-tp-plot')
    plot.getBoundingClientRect = () => ({ left: 0, top: 0, width: 590, height: 84, right: 590, bottom: 84 })
    await ui.event(plot, 'mousemove', { clientX: 590 }) // last bucket
    const tip = document.querySelector('[data-chart="tokens"] [role="tooltip"]')
    assert.ok(tip, 'tooltip')
    assert.match(tip.textContent, /in\+out 600/)
    assert.match(tip.textContent, /hub600/)
    assert.match(tip.textContent, /worker-1, ephemeral, unattributed\u2013/)
    await ui.event(plot, 'mouseout', { relatedTarget: document.body }) // React's onMouseLeave
    assert.ok(document.querySelector('[data-chart="tokens"] [role="tooltip"]') === null)
  } finally {
    await ui.close()
    localStorage.clear()
  }
})

test('backend error shows Unavailable, never zeros', async () => {
  const ui = await mount(React.createElement(P.Throughput, { ctx: { rest: async () => { throw Error('404 not found') } } }))
  try {
    const chip = button('Show throughput')
    assert.match(chip.textContent, /Throughput …/)
    await ui.click(chip)
    assert.match(document.body.textContent, /Unavailable: 404 not found/)
  } finally {
    await ui.close()
  }
})

test('register: chip right after Runners, palette entry, CSS removed on dispose', () => {
  const regs = []
  let dispose
  P.default.register({ register: (r) => regs.push(r), onDispose: (f) => { dispose = f } })
  assert.deepEqual(regs.map((r) => [r.id, r.area, r.order]), [['chip', 'statusBar.right', 7], ['show-throughput', 'palette', undefined]])
  assert.ok([...document.head.querySelectorAll('style')].some((s) => s.textContent.includes('.hermes-tp-chart')))
  dispose()
  assert.ok(![...document.head.querySelectorAll('style')].some((s) => s.textContent.includes('.hermes-tp-chart')))
})

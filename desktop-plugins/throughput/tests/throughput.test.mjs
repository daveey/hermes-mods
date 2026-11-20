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
  assert.deepEqual(P.loadView(), { unit: 'h', range: '7d', cache: true, by: 'gateway' })
  P.saveView({ unit: 's', range: '24h', cache: false, by: 'model' })
  assert.deepEqual(P.loadView(), { unit: 's', range: '24h', cache: false, by: 'model' })
  localStorage.setItem(P.STORE_KEY, JSON.stringify({ unit: 'x', range: '2d', cache: 'yes', by: 'invalid' }))
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
    // upper half of the window: opens downward; lower half: opens upward (stays on screen)
    assert.equal(tip.style.top, '4px')
    window.innerHeight = 1000
    plot.getBoundingClientRect = () => ({ left: 0, top: 800, width: 590, height: 84, right: 590, bottom: 884 })
    await ui.event(plot, 'mousemove', { clientX: 590 })
    const low = document.querySelector('[data-chart="tokens"] [role="tooltip"]')
    assert.equal(low.style.bottom, '16px')
    assert.equal(low.style.top, '')
    await ui.event(plot, 'mouseout', { relatedTarget: document.body }) // React's onMouseLeave
    assert.ok(document.querySelector('[data-chart="tokens"] [role="tooltip"]') === null)
  } finally {
    await ui.close()
    localStorage.clear()
  }
})

// A 6h payload: 72 five-minute buckets ending at END.
function payload6h() {
  const p = payload()
  const map = (o) => Object.fromEntries(Object.entries(o).map(([g, a]) => [g, a && Array.from({ length: 72 }, () => a[0])]))
  return { ...p, range: '6h', since: END - 21600, bucket_s: 300, series: Object.fromEntries(Object.entries(p.series).map(([m, o]) => [m, map(o)])) }
}
const ticks = () => [...document.querySelectorAll('[data-chart="tokens"] .hermes-tp-ticks span')].map((e) => e.textContent)

test('switching range never draws the previous range: loading, then the new window (or its error)', async () => {
  localStorage.setItem(P.STORE_KEY, JSON.stringify({ unit: 'm', range: '1h', cache: false }))
  let release
  const pending = new Promise((res) => { release = res })
  const ctx = ctxWith((path) => (path.endsWith('range=6h') ? pending : forRange(path)))
  const ui = await mount(React.createElement(P.Throughput, { ctx }))
  try {
    await ui.click(button('Show throughput'))
    const t1h = ticks()
    assert.equal(t1h[0], P.timeLabel(END - 3600, '1h'))
    await ui.click(button('6h'))
    // the 1h charts are gone while 6h loads; controls stay so the user can switch again
    assert.equal(document.querySelectorAll('[data-chart]').length, 0)
    assert.match(document.body.textContent, /Loading 6h/)
    assert.ok(button('1h') && button('24h'))
    release(payload6h())
    await ui.flush()
    assert.equal(document.querySelectorAll('[data-chart]').length, 3)
    assert.equal(ticks()[0], P.timeLabel(END - 21600, '6h'))
    assert.equal(ticks().at(-1), P.timeLabel(END - 300, '6h'))
  } finally {
    await ui.close()
    localStorage.clear()
  }
})

test('hovering a range button prefetches it, so the click draws at once', async () => {
  localStorage.setItem(P.STORE_KEY, JSON.stringify({ unit: 'm', range: '1h', cache: false }))
  const ctx = ctxWith((path) => (path.endsWith('range=6h') ? payload6h() : forRange(path)))
  const ui = await mount(React.createElement(P.Throughput, { ctx }), { gcTime: 60000 }) // the app keeps unobserved queries
  try {
    await ui.click(button('Show throughput'))
    await ui.event(button('6h'), 'pointerover', { relatedTarget: document.body })
    assert.equal(ctx.calls.at(-1)[0], '/v1/series?range=6h')
    const n = ctx.calls.length
    await React.act(async () => button('6h').dispatchEvent(new MouseEvent('click', { bubbles: true })))
    assert.equal(document.querySelectorAll('[data-chart]').length, 3) // no flush: drawn from the prefetch
    assert.equal(ticks()[0], P.timeLabel(END - 21600, '6h'))
    assert.equal(ctx.calls.length, n) // fresh prefetch, no second request
  } finally {
    await ui.close()
    localStorage.clear()
  }
})

test('a failed range switch shows Unavailable for that range, not the old range', async () => {
  localStorage.setItem(P.STORE_KEY, JSON.stringify({ unit: 'm', range: '1h', cache: false }))
  const ctx = ctxWith((path) => { if (path.endsWith('range=24h')) throw Error('timed out'); return forRange(path) })
  const ui = await mount(React.createElement(P.Throughput, { ctx }))
  try {
    await ui.click(button('Show throughput'))
    await ui.click(button('24h'))
    assert.equal(document.querySelectorAll('[data-chart]').length, 0)
    assert.match(document.body.textContent, /24h unavailable: timed out/)
    await ui.click(button('1h'))
    assert.equal(document.querySelectorAll('[data-chart]').length, 3)
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

function modelPayload(range = '1h') {
  const p = forRange(`/v1/series?range=${range}`)
  const n = range === '24h' ? 288 : 60
  const bs = range === '24h' ? 300 : 60
  const values = { 'model-a': 120, 'model-b': 240, other: 60, unreported: null }
  const map = (scale, length) => Object.fromEntries(Object.entries(values).map(([m, v]) => [m, v == null ? null : Array(length).fill(v * scale)]))
  return { ...p, by: 'model', models: Object.keys(values), since: END - (range === '24h' ? 86400 : 3600), bucket_s: bs,
    series: { ...p.series, tokens_io: map(bs / 60, n), tokens_cache: map(bs / 600, n) },
    recent: { ...p.recent, tokens_io: map(1, 15), tokens_cache: map(0.1, 15) } }
}
const modelOrGateway = (path) => {
  const q = new URL('http://x' + path).searchParams
  return q.get('by') === 'model' ? modelPayload(q.get('range')) : forRange(path)
}

test('model toggle fetches separately, persists, and model legend never filters gateway charts or chip', async () => {
  P.saveView(V())
  const ctx = ctxWith(modelOrGateway)
  const ui = await mount(React.createElement(P.Throughput, { ctx }), { gcTime: 60000 })
  const paths = (chart) => [...document.querySelectorAll(`[data-chart="${chart}"] path`)].map((e) => e.dataset.gw)
  try {
    assert.equal(ctx.calls.length, 1, 'gateway default does not request model data')
    await ui.click(button('Show throughput'))
    const chip = button('Show throughput').textContent
    await ui.click(button('model'))
    assert.equal(ctx.calls.at(-1)[0], '/v1/series?range=1h&by=model')
    assert.equal(P.loadView().by, 'model')
    assert.deepEqual([...document.querySelectorAll('button[data-model]')].map((e) => e.dataset.model), ['model-a', 'model-b', 'other', 'unreported'])
    assert.deepEqual(paths('tokens'), ['model-a', 'model-b', 'other'])
    assert.equal(document.querySelector('[data-now="tokens"]').textContent, '420')
    assert.equal(button('Show throughput').textContent, chip)
    const cards = paths('cards'), sessions = paths('sessions')
    const pick = (m) => document.querySelector(`button[data-model="${m}"]`)
    await ui.click(pick('model-b'))
    assert.deepEqual(paths('tokens'), ['model-b'])
    assert.equal(document.querySelector('[data-now="tokens"]').textContent, '240')
    assert.deepEqual(paths('cards'), cards)
    assert.deepEqual(paths('sessions'), sessions)
    await ui.click(pick('model-b'))
    assert.equal(paths('tokens').length, 3)
    await ui.click(pick('model-a'), { shiftKey: true })
    assert.deepEqual(paths('tokens'), ['model-b', 'other'])
    assert.ok(pick('model-a').classList.contains('is-off'))
    await ui.click(button('All models'))
    await ui.click(pick('unreported'))
    assert.equal(document.querySelector('[data-now="tokens"]').textContent, '–')
    await ui.click(button('All models'))
    await ui.click(document.querySelector('button[data-gw="worker-1"]'))
    assert.deepEqual(paths('tokens'), ['model-a', 'model-b', 'other'])
    assert.ok(!paths('cards').includes('hub'))
    await ui.click(button('+cache'))
    assert.equal(document.querySelector('[data-now="tokens"]').textContent, '462')
    await ui.click(button('gateway'))
    assert.equal(P.loadView().by, 'gateway')
    assert.equal(document.querySelector('[data-now="tokens"]').textContent, '–', 'gateway filter survives model mode')
    assert.ok(document.querySelector('button[data-model]') === null)
  } finally { await ui.close(); localStorage.clear() }
})

test('persisted model mode refetches 24h without stale model charts, then returns to 1h', async () => {
  P.saveView(V({ by: 'model' }))
  let release
  const pending = new Promise((resolve) => { release = resolve })
  const ctx = ctxWith((path) => path === '/v1/series?range=24h&by=model' ? pending : modelOrGateway(path))
  const ui = await mount(React.createElement(P.Throughput, { ctx }), { gcTime: 60000 })
  try {
    await ui.click(button('Show throughput'))
    assert.equal(button('model').getAttribute('aria-pressed'), 'true')
    assert.equal(ticks()[0], P.timeLabel(END - 3600, '1h'))
    await ui.click(button('24h'))
    assert.ok(ctx.calls.some(([path]) => path === '/v1/series?range=24h'))
    assert.ok(ctx.calls.some(([path]) => path === '/v1/series?range=24h&by=model'))
    assert.ok(document.querySelector('[data-chart="tokens"]') === null)
    assert.ok(document.querySelector('[data-chart="sessions"]'))
    assert.ok(document.querySelector('[data-chart="cards"]'))
    assert.match(document.body.textContent, /Loading models 24h/)
    release(modelPayload('24h'))
    await ui.flush()
    assert.equal(ticks()[0], P.timeLabel(END - 86400, '24h'))
    assert.equal(ticks().at(-1), P.timeLabel(END - 300, '24h'))
    assert.equal(document.querySelector('[data-now="tokens"]').textContent, '420')
    await ui.click(button('1h'))
    assert.equal(ticks()[0], P.timeLabel(END - 3600, '1h'))
    assert.equal(P.loadView().by, 'model')
  } finally { release(modelPayload('24h')); await ui.close(); localStorage.clear() }
})

test('model request failure leaves gateway chip and session/card charts usable', async () => {
  P.saveView(V({ by: 'model' }))
  const ui = await mount(React.createElement(P.Throughput, { ctx: ctxWith((path) => {
    if (path.includes('by=model')) throw Error('model timeout')
    return forRange(path)
  }) }))
  try {
    await ui.click(button('Show throughput'))
    assert.match(document.body.textContent, /Models 1h unavailable: model timeout/)
    assert.ok(document.querySelector('[data-chart="tokens"]') === null)
    assert.ok(document.querySelector('[data-chart="sessions"]'))
    assert.ok(document.querySelector('[data-chart="cards"]'))
    assert.match(button('Show throughput').textContent, /600 tok\/m/)
    await ui.click(button('gateway'))
    assert.ok(document.querySelector('[data-chart="tokens"]'))
  } finally { await ui.close(); localStorage.clear() }
})

// Throttle fixture: 24-hour peak 20 tok/s (1200/m); level 75 % caps at 5 tok/s (300/m); the hub chart peaks at 600/m.
const throttle = (over = {}) => ({
  percent: 75, reference_per_s: 20, cap_per_s: 5, rate_per_s: 10, holding: true, scope: 'hub', now: END,
  pools: [
    { provider: 'openai-codex', accounts: 3, exhausted: 1, remaining_pct: 122, burn_pct_per_h: 6, runway_s: 73200, next_reset_at: END + 86400, read_at: END },
    { provider: 'anthropic', accounts: 2, exhausted: 2, remaining_pct: 0, burn_pct_per_h: 0, runway_s: null, next_reset_at: null, read_at: END }
  ],
  ...over
})

test('throttle maths: cap per unit, live preview, burn scale, durations and labels (hand-computed)', () => {
  assert.equal(P.capRate(throttle(), 's'), 5)
  assert.equal(P.capRate(throttle(), 'm'), 300)
  assert.equal(P.capRate(throttle({ cap_per_s: null }), 'm'), null)
  assert.equal(P.capRate(null, 'm'), null)
  assert.equal(P.capRate(throttle({ cap_per_s: 0 }), 'h'), 0)
  // preview: 0 = no cap, 100 = zero, between = (1 - p) x the 24-hour peak; no peak = no cap
  assert.equal(P.previewThrottle(throttle(), 0).cap_per_s, null)
  assert.equal(P.previewThrottle(throttle(), 100).cap_per_s, 0)
  assert.equal(P.previewThrottle(throttle(), 50).cap_per_s, 10)
  assert.equal(P.previewThrottle(throttle({ reference_per_s: null }), 50).cap_per_s, null)
  assert.equal(P.previewThrottle(throttle({ reference_per_s: null }), 100).cap_per_s, 0)
  // burn scale: cap 5 under rate 10 halves the burn; a cap above the rate changes nothing; cap 0 pauses
  assert.equal(P.burnScale(throttle()), 0.5)
  assert.equal(P.burnScale(throttle({ cap_per_s: 50 })), 1)
  assert.equal(P.burnScale(throttle({ cap_per_s: null })), 1)
  assert.equal(P.burnScale(throttle({ cap_per_s: 0 })), 0)
  assert.equal(P.burnScale(throttle({ rate_per_s: null })), 1)
  assert.equal(P.fmtDuration(73200), '20.3 h')
  assert.equal(P.fmtDuration(3 * 86400), '3.0 d')
  assert.equal(P.fmtDuration(600), '10 min')
  assert.equal(P.fmtDuration(null), '–')
  assert.equal(P.throttleText(throttle({ percent: 0, cap_per_s: null }), 'm'), 'Throttle off')
  assert.equal(P.throttleText(throttle(), 'm'), 'Throttle 75% · cap 300 tok/m · over the cap, pacing calls')
  assert.equal(P.throttleText(throttle({ percent: 100, cap_per_s: 0 }), 'm'), 'Throttle 100% · cap 0 tok/m · holding every call')
  assert.equal(P.throttleText(throttle({ holding: false }), 's'), 'Throttle 75% · cap 5.0 tok/s')
  assert.match(P.throttleText(throttle({ cap_per_s: null }), 'm'), /no cap yet/)
  const [gpt, claude] = throttle().pools
  // uncapped: 73 200 s = 20.3 h; under the fixture's cap (half the rate) the same allowance lasts twice as long
  assert.match(P.runwayText(gpt, throttle({ cap_per_s: null })), /^GPT: 122% left, empty in 20\.3 h at this rate, next reset /)
  assert.match(P.runwayText(gpt, throttle()), /^GPT: 122% left, empty in 40\.7 h at the cap, next reset /)
  assert.match(P.runwayText(gpt, throttle({ cap_per_s: 0 })), /^GPT: 122% left, paused by the throttle/)
  assert.equal(P.runwayText(claude, throttle()), 'Claude: 0% left, not burning')
  // the quota governor's verdict rides on the line: it limits card starts whatever the throttle level is
  assert.equal(P.pacingText(gpt), '')
  assert.equal(P.pacingText({ pacing: 'open' }), '')
  assert.equal(P.runwayText({ ...claude, pacing: 'paced' }, throttle()), 'Claude: 0% left, not burning · quota pacing is limiting card starts')
  assert.equal(P.pacingText({ pacing: 'held' }), ' · quota pacing holds card starts')
  assert.match(P.runwayText({ ...gpt, pacing: 'held', pacing_until: 1794960000 }, throttle({ cap_per_s: null })), /at this rate, next reset .* · quota pacing holds card starts until \S+ \d\d:\d\d$/)
})

test('throttle scope: the fleet with an acknowledgement count, or this gateway alone without the hub link', () => {
  const fleet = (over = {}) => throttle({
    fleet: true, set_at: END - 60, set_by: null,
    gateways: [{ name: 'hub', seen: true }, { name: 'mini', seen: false }, { name: 'worker-1', seen: true }], ...over
  })
  assert.equal(P.scopeText(fleet()), 'applies to all gateways · 2 of 3 have this level')
  assert.match(P.scopeTitle(fleet()), /Not acknowledged yet: mini\.$/)
  assert.match(P.scopeTitle(fleet({ set_by: 'macbook' })), /^Set on macbook\. /)
  assert.equal(P.scopeText(fleet({ set_at: null })), 'applies to all gateways')  // never set: nothing to acknowledge
  assert.doesNotMatch(P.scopeTitle(fleet({ set_at: null })), /Not acknowledged/)
  assert.doesNotMatch(P.scopeTitle(fleet({ gateways: [{ name: 'hub', seen: true }] })), /Not acknowledged/)
  assert.equal(P.scopeText(throttle()), 'applies to hub only')  // an older backend, or a host without the link
  assert.match(P.scopeTitle(throttle()), /no hub link/)
  assert.equal(P.scopeText(throttle({ fleet: false, scope: undefined })), '')
  assert.equal(P.scopeText(null), '')
})

const withThrottle = (state) => (path) => (path === '/v1/throttle' ? state.value : forRange(path))
const capLines = (chart) => [...document.querySelectorAll(`[data-chart="${chart}"] line[data-cap]`)]
const setRange = async (ui, el, value) => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, String(value))
  await React.act(async () => el.dispatchEvent(new window.Event('input', { bubbles: true })))
  await ui.flush()
}

test('panel: red cap line on Tokens only, slider at the level, runway per pool', async () => {
  P.saveView(V())
  const ctx = ctxWith(withThrottle({ value: throttle() }))
  const ui = await mount(React.createElement(P.Throughput, { ctx }))
  try {
    assert.ok(!ctx.calls.some(([p]) => p === '/v1/throttle'), 'the throttle is not polled while the panel is closed')
    await ui.click(button('Show throughput'))
    const [line] = capLines('tokens')
    assert.ok(line, 'cap line drawn')
    assert.equal(line.dataset.cap, '300')
    assert.equal(line.getAttribute('stroke'), P.THROTTLE_RED)
    // Tokens chart: H 84, baseline 70, 66 px for the 600/m peak; 300/m sits half way = y 37
    assert.equal(Number(line.getAttribute('y1')), 37)
    assert.equal(Number(line.getAttribute('y2')), 37)
    assert.match(document.querySelector('[data-chart="tokens"] .hermes-tp-capl').textContent, /^cap 300\/m$/)
    assert.equal(capLines('sessions').length, 0)
    assert.equal(capLines('cards').length, 0)
    const slider = document.querySelector('[data-slot="throttle"] input[type=range]')
    assert.equal(slider.value, '75')
    assert.match(document.querySelector('[data-slot="throttle"]').textContent, /Throttle 75% · cap 300 tok\/m · over the cap, pacing calls/)
    assert.match(document.querySelector('[data-slot="throttle"]').textContent, /applies to hub/)
    assert.equal(document.querySelector('[data-scope]').dataset.scope, 'local')
    assert.match(document.querySelector('[data-pool="openai-codex"]').textContent, /^GPT: 122% left, empty in 40\.7 h at the cap/)
    assert.equal(document.querySelector('[data-pool="anthropic"]').textContent, 'Claude: 0% left, not burning')
    // the line follows the unit switch: 5 tok/s on a 10 tok/s peak is still half way
    await ui.click(button('/s'))
    assert.equal(capLines('tokens')[0].dataset.cap, '5')
    assert.equal(Number(capLines('tokens')[0].getAttribute('y1')), 37)
    // +cache and a gateway filter draw something the in+out fleet cap cannot be compared with: no line
    await ui.click(button('+cache'))
    assert.equal(capLines('tokens').length, 0)
    await ui.click(button('in+out'))
    assert.equal(capLines('tokens').length, 1)
    await ui.click(document.querySelector('.hermes-tp-legend [data-gw="hub"]'))
    assert.equal(capLines('tokens').length, 0)
  } finally {
    await ui.close()
  }
})

test('panel: a cap above the chart pins to the top with an arrow; 0 % draws no line; 100 % sits on the baseline', async () => {
  P.saveView(V())
  const state = { value: throttle({ percent: 10, cap_per_s: 18, holding: false }) }
  const ctx = ctxWith(withThrottle(state))
  const ui = await mount(React.createElement(P.Throughput, { ctx }))
  try {
    await ui.click(button('Show throughput'))
    assert.equal(Number(capLines('tokens')[0].getAttribute('y1')), 4) // 1080/m is above the 600/m peak
    assert.match(document.querySelector('[data-chart="tokens"] .hermes-tp-capl').textContent, /^cap 1\.1k\/m ↑$/)
  } finally {
    await ui.close()
  }
  for (const [value, y] of [[throttle({ percent: 0, cap_per_s: null, holding: false }), null], [throttle({ percent: 100, cap_per_s: 0 }), 70]]) {
    const ui2 = await mount(React.createElement(P.Throughput, { ctx: ctxWith(withThrottle({ value })) }))
    try {
      await ui2.click(button('Show throughput'))
      if (y == null) {
        assert.equal(capLines('tokens').length, 0)
        assert.match(document.querySelector('[data-slot="throttle"]').textContent, /Throttle off/)
      } else assert.equal(Number(capLines('tokens')[0].getAttribute('y1')), y)
    } finally {
      await ui2.close()
    }
  }
})

test('slider: dragging previews the cap, releasing saves it with PUT, a refused save is shown', async () => {
  P.saveView(V())
  const state = { value: throttle(), fail: false }
  const calls = []
  const ctx = {
    calls,
    rest: async (path, opts) => {
      calls.push([path, opts])
      if (path !== '/v1/throttle') return forRange(path)
      if (opts?.method === 'PUT') {
        if (state.fail) throw Error('HTTP 400')
        state.value = throttle({ percent: opts.body.percent, cap_per_s: (1 - opts.body.percent / 100) * 20, holding: false })
      }
      return state.value
    }
  }
  const ui = await mount(React.createElement(P.Throughput, { ctx }))
  const puts = () => calls.filter(([p, o]) => p === '/v1/throttle' && o?.method === 'PUT')
  try {
    await ui.click(button('Show throughput'))
    const slider = () => document.querySelector('[data-slot="throttle"] input[type=range]')
    await setRange(ui, slider(), 50)
    assert.equal(puts().length, 0, 'nothing is saved while dragging')
    assert.equal(capLines('tokens')[0].dataset.cap, '600') // preview: half of the 1200/m peak
    assert.match(document.querySelector('[data-slot="throttle"]').textContent, /Throttle 50% · cap 600 tok\/m/)
    await ui.event(slider(), 'pointerup')
    assert.equal(puts().length, 1)
    assert.deepEqual(puts()[0][1].body, { percent: 50 })
    assert.equal(slider().value, '50')
    assert.equal(capLines('tokens')[0].dataset.cap, '600')
    await ui.event(slider(), 'pointerup')
    assert.equal(puts().length, 1, 'releasing without a change saves nothing')
    state.fail = true
    await setRange(ui, slider(), 90)
    await ui.event(slider(), 'pointerup')
    assert.equal(puts().length, 2)
    assert.match(document.querySelector('[data-slot="throttle"] [role=alert]').textContent, /Throttle not saved: HTTP 400/)
    assert.equal(slider().value, '50', 'the slider returns to the saved level')
  } finally {
    await ui.close()
  }
})

test('no throttle endpoint (older backend): the panel renders without slider or line', async () => {
  P.saveView(V())
  const ctx = ctxWith((path) => { if (path === '/v1/throttle') throw Error('HTTP 404'); return forRange(path) })
  const ui = await mount(React.createElement(P.Throughput, { ctx }))
  try {
    await ui.click(button('Show throughput'))
    assert.equal(document.querySelectorAll('[data-chart]').length, 3)
    assert.equal(document.querySelector('[data-slot="throttle"]'), null)
    assert.equal(capLines('tokens').length, 0)
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

test('budget mode: the slider scales the published budget; stale and expired budgets say so', () => {
  const auto = (over = {}) => throttle({
    mode: 'auto', percent: 0, cap_per_s: 10, holding: false,
    budget: { state: 'fresh', budget_at: END - 60, age_s: 60, budget_per_s: 10, cap_per_s: 10, pool_caps: { anthropic: 4 } },
    ...over
  })
  assert.equal(P.throttleText(auto(), 's'), 'Budget · cap 10 tok/s') // 0 % is the full budget, not "off"
  assert.equal(P.throttleText(auto({ percent: 25, cap_per_s: 7.5, holding: true }), 'm'), 'Budget −25% · cap 450 tok/m · over the cap, holding card starts')
  assert.equal(P.throttleText(auto({ percent: 100, cap_per_s: 0 }), 'm'), 'Budget −100% · holding every call')
  assert.equal(P.throttleText(auto({ budget: { state: 'stale', age_s: 1800, budget_per_s: 10 } }), 's'), 'Budget · cap 10 tok/s · budget stale (30 min old), keeping last caps')
  assert.equal(P.throttleText(auto({ cap_per_s: null, budget: { state: 'expired', age_s: 7200, budget_per_s: 10 } }), 's'), 'Budget · budget expired (2.0 h old), no cap')
  assert.equal(P.throttleText(auto({ cap_per_s: null, budget: { state: 'fresh', age_s: 60, budget_per_s: null } }), 's'), 'Budget · cap none tok/s')
  // preview: (1 - p) x the budget; 100 = zero; an expired budget previews no cap
  assert.equal(P.previewThrottle(auto(), 0).cap_per_s, 10)
  assert.equal(P.previewThrottle(auto(), 40).cap_per_s, 6)
  assert.equal(P.previewThrottle(auto(), 100).cap_per_s, 0)
  assert.equal(P.previewThrottle(auto({ budget: { state: 'expired', budget_per_s: 10 } }), 40).cap_per_s, null)
  // the preview stays a budget level: mode kept, pool caps scaled from the unscaled budgets, a reserve 0 stays 0
  const moved = P.previewThrottle(auto({ budget: { state: 'fresh', budget_per_s: 10, pool_budgets: { anthropic: 0, 'openai-codex': 6, 'xai-oauth': null } } }), 50)
  assert.equal(moved.mode, 'auto')
  assert.deepEqual(moved.budget.pool_caps, { anthropic: 0, 'openai-codex': 3, 'xai-oauth': null })
  assert.equal(moved.budget.cap_per_s, 5)
  assert.match(P.throttleText(moved, 's'), /^Budget −50% · cap 5\.0 tok\/s/)
})

// ── burst + budget chart ────────────────────────────────────────────────────

// Budget fixture (hand-computed): two pools; history = 3 hourly buckets ending at END; curves from END.
// anthropic: spent 100, 300, 600 cumulative; capacity 1000 now, +500 at END+7200; plan 0 -> 400 at END+7200.
// openai-codex: spent 0, 0, 60; capacity 200 flat to END+3600; plan 0 -> 200 at END+3600.
const budgetFixture = (over = {}) => ({
  now: END, computed_at: END, margin_s: 10800, global_rate_tokens_per_s: 30,
  pools: { anthropic: { rate_tokens_per_s: 10, remaining_tokens: 1000, exhaust_plan_at: END + 7200, at_reserve: false, accounts: 2 },
    'openai-codex': { rate_tokens_per_s: 20, remaining_tokens: 200, exhaust_plan_at: END + 3600, at_reserve: false, accounts: 1 } },
  events: [{ ts: END - 5400, provider: 'anthropic', type: 'reset_early', kind: 'five_hour' },
    { ts: END - 1800, provider: 'openai-codex', type: 'account_added', kind: null },
    { ts: END - 30 * 86400, provider: 'anthropic', type: 'reset', kind: 'seven_day' }],
  curve: { anthropic: [[END, 1000, 0], [END + 7200, 1500, 400]], 'openai-codex': [[END, 200, 0], [END + 3600, 200, 200]] },
  history: { bucket_s: 3600, since: END - 10800, until: END, t: [END - 10800, END - 7200, END - 3600],
    providers: { anthropic: { rate: [0.03, 0.06, 0.08], cumulative: [100, 300, 600] }, 'openai-codex': { rate: [0, 0, 0.02], cumulative: [0, 0, 60] },
      unknown: { rate: [1, 1, 1], cumulative: [3600, 7200, 10800] } } },
  burst: null,
  projection: { factor: 2, hours: 1, pools: {
    anthropic: { rate_now: 10, extra_tokens: 36000, exhausts_during_burst_at: null, earlier_s: 3600, rate_after: 4.5 },
    'openai-codex': { rate_now: 20, extra_tokens: 200, exhausts_during_burst_at: END + 300, earlier_s: 10, rate_after: 0 } } },
  ...over
})

test('burst maths: factor in force, preview and labels scale by it; projection text and reserve warning', () => {
  const auto = (over = {}) => throttle({ mode: 'auto', percent: 50, cap_per_s: 10, holding: false,
    budget: { state: 'fresh', age_s: 60, budget_per_s: 10, pool_budgets: { anthropic: 4, 'openai-codex': 0 }, burst_factor: 2 },
    burst: { factor: 2, until: END + 2400, set_at: END - 1200, remaining_s: 2400, active: true }, ...over })
  assert.equal(P.burstFactor(auto()), 2)
  assert.equal(P.burstFactor(auto({ budget: {} })), 2) // an older payload: the burst's own `active`
  assert.equal(P.burstFactor(auto({ budget: {}, burst: { factor: 2, active: false } })), 1)
  assert.equal(P.burstFactor(throttle()), 1)
  // the slider preview keeps the burst: 10 x (1 - 0.25) x 2 = 15; a reserve 0 stays 0; 100 % is 0
  const moved = P.previewThrottle(auto(), 25)
  assert.equal(moved.cap_per_s, 15)
  assert.deepEqual(moved.budget.pool_caps, { anthropic: 6, 'openai-codex': 0 })
  assert.equal(P.previewThrottle(auto(), 100).cap_per_s, 0)
  assert.equal(P.throttleText(auto(), 's'), 'Budget −50% · burst 2× · cap 10 tok/s')
  assert.equal(P.burstText(auto().burst, END), 'Burst 2× · 40 min left')
  assert.equal(P.burstText(auto().burst, END + 2400), null)
  assert.equal(P.burstText(null, END), null)
  assert.equal(P.fmtHours(0.5), '30 min')
  assert.equal(P.fmtHours(4), '4 h')
  const pr = budgetFixture().projection.pools
  assert.deepEqual(P.projectionText('anthropic', pr.anthropic),
    { text: 'Claude: runs out ~60 min earlier at today’s rate · after the burst the planner slows to 4.5 tok/s (now 10)', warn: null })
  const gpt = P.projectionText('openai-codex', pr['openai-codex'])
  assert.match(gpt.text, /^GPT: runs out ~1 min earlier/)
  assert.match(gpt.warn, /^GPT would hit its reserve during the burst, at \S+ \d\d:\d\d$/)
  assert.match(P.projectionText('anthropic', { rate_now: 0, extra_tokens: 0, earlier_s: 0, rate_after: 0 }).text, /a burst adds nothing/)
  assert.match(P.projectionText('xai-oauth', { rate_now: null }).text, /^Grok: rate unknown/)
})

test('budget chart series: history to now, forward curve lifted by the spend so far, marks (hand-computed)', () => {
  const b = budgetFixture()
  const a = P.chartSeries(b, 'anthropic', { now: END })
  assert.deepEqual(a.hist, [[END - 10800, 0], [END - 7200, 100], [END - 3600, 300], [END, 600]])
  assert.equal(a.base, 600)
  assert.deepEqual(a.cap, [[END, 1600], [END + 7200, 2100]])
  assert.deepEqual(a.plan, [[END, 600], [END + 7200, 1000]])
  assert.equal(a.x0, END - 10800)
  assert.equal(a.x1, END + 7200) // the furthest reset in the curve
  assert.equal(a.ymax, 2100)
  // events of this pool inside the window, plus the curve's future reset; nothing of the other pool, nothing 30 d old
  assert.deepEqual(a.marks.map((m) => [m.t, m.type]), [[END - 5400, 'reset_early'], [END + 7200, 'reset']])
  assert.equal(a.marks[0].label, 'Claude early reset (5 h window)')
  // the total: both pools (never the unattributed `unknown` provider); the curves merge on their union of times:
  // capacity steps (1000 + 200 = 1200 now, 1500 + 200 at END+7200), plan interpolates (anthropic 200 at END+3600)
  const t = P.chartSeries(b, null, { now: END })
  assert.deepEqual(t.hist.map((p) => p[1]), [0, 100, 300, 660])
  assert.deepEqual(t.cap, [[END, 1860], [END + 3600, 1860], [END + 7200, 2360]])
  assert.deepEqual(t.plan, [[END, 660], [END + 3600, 1060], [END + 7200, 1260]])
  assert.deepEqual(t.marks.map((m) => m.type), ['reset_early', 'account_added', 'reset'])
  // a running burst marks its own span; a previewed one starts now and is drawn dashed
  const run = P.chartSeries(b, 'anthropic', { now: END, burst: { factor: 2, set_at: END - 600, until: END + 1800 } })
  assert.deepEqual(run.burst, [END - 600, END + 1800])
  assert.deepEqual(run.marks.filter((m) => m.type.startsWith('burst')).map((m) => [m.t, m.preview]), [[END - 600, false], [END + 1800, false]])
  const pre = P.chartSeries(b, 'anthropic', { now: END, preview: { factor: 3, hours: 2 } })
  assert.deepEqual(pre.burst, [END, END + 7200])
  assert.ok(pre.marks.filter((m) => m.type.startsWith('burst')).every((m) => m.preview))
  // no curve, no history: an empty chart still spans 7 days back to a day ahead
  const empty = P.chartSeries({ pools: { anthropic: {} } }, 'anthropic', { now: END })
  assert.deepEqual([empty.hist, empty.cap, empty.plan, empty.x0, empty.x1], [[], [], [], END - 7 * 86400, END + 86400])
})

test('panel: budget charts per pool and total, burst projection, start and end, slider relabelled', async () => {
  P.saveView(V())
  const autoThrottle = (burst = null) => throttle({
    mode: 'auto', percent: 20, cap_per_s: 8, holding: false, burst,
    budget: { state: 'fresh', age_s: 60, budget_per_s: 10, cap_per_s: 8, pool_budgets: { anthropic: 4, 'openai-codex': 6 }, pool_caps: {}, burst_factor: burst?.active ? burst.factor : 1 }
  })
  const state = { throttle: autoThrottle() }
  const calls = []
  const ctx = {
    calls,
    rest: async (path, opts) => {
      calls.push([path, opts])
      if (path === '/v1/throttle') return state.throttle
      if (path === '/v1/throttle/burst') {
        if (opts.method === 'PUT') state.throttle = autoThrottle({ factor: opts.body.factor, until: Date.now() / 1000 + opts.body.hours * 3600, set_at: Date.now() / 1000, remaining_s: opts.body.hours * 3600, active: true })
        else state.throttle = autoThrottle()
        return state.throttle
      }
      if (path.startsWith('/v1/budget')) {
        const q = new URL('http://x' + path).searchParams
        const factor = Number(q.get('factor')), hours = Number(q.get('hours'))
        return budgetFixture({ projection: { ...budgetFixture().projection, factor, hours } })
      }
      return forRange(path)
    }
  }
  const ui = await mount(React.createElement(P.Throughput, { ctx }))
  try {
    await ui.click(button('Show throughput'))
    await ui.flush()
    assert.ok(calls.some(([p]) => p === '/v1/budget?factor=2&hours=1'), 'the default 2x / 1 h burst is costed')
    const charts = [...document.querySelectorAll('[data-budget-chart]')].map((e) => e.dataset.budgetChart)
    assert.deepEqual(charts, ['total', 'anthropic', 'openai-codex'])
    const claude = document.querySelector('[data-budget-chart="anthropic"]')
    for (const line of ['spent', 'plan', 'capacity']) assert.ok(claude.querySelector(`path[data-line="${line}"]`), line)
    assert.ok(claude.querySelector('line[data-now-line]'))
    assert.ok(claude.querySelector('line[data-mark="reset_early"]'))
    assert.ok(claude.querySelector('line[data-mark="reset"]'))
    assert.ok(claude.querySelector('rect[data-mark="burst"]'), 'the burst being considered is shaded')
    assert.equal(claude.querySelector('line[data-mark="burst_start"]').getAttribute('stroke-dasharray'), '2 2')
    assert.ok(document.querySelector('[data-budget-chart="openai-codex"] line[data-mark="account_added"]'))
    // the panel's own charts are themed by CSS variables; the budget lines by fixed mid-tone colours
    assert.equal(claude.querySelector('line[data-now-line]').getAttribute('stroke'), 'var(--ui-text-secondary, #8a909c)')
    // the slider stays, relabelled for budget mode
    assert.equal(document.querySelector('[data-slider-label]').textContent, 'Hold back 20%')
    assert.equal(document.querySelector('[data-slot="throttle"] input[type=range]').getAttribute('aria-label'), 'Hold back percent of the budget')
    // projection per pool, with the reserve warning on GPT
    assert.match(document.querySelector('[data-projection="anthropic"]').textContent, /^Claude: runs out ~60 min earlier at today’s rate · after the burst the planner slows to 4\.5 tok\/s/)
    assert.match(document.querySelector('[data-warn="openai-codex"]').textContent, /GPT would hit its reserve during the burst/)
    assert.equal(document.querySelector('[data-warn="anthropic"]'), null)
    // another factor and duration: costed again before it can start
    await ui.click(button('3×'))
    await ui.click(button('4 h'))
    assert.ok(calls.some(([p]) => p === '/v1/budget?factor=3&hours=4'))
    const start = button('Start 3× for 4 h')
    assert.equal(start.disabled, false)
    await ui.click(start)
    const put = calls.find(([p, o]) => p === '/v1/throttle/burst' && o?.method === 'PUT')
    assert.deepEqual(put[1].body, { factor: 3, hours: 4 })
    assert.match(document.querySelector('[data-burst]').textContent, /^Burst 3× · 4\.0 h left$/)
    assert.match(document.querySelector('[data-slot="throttle"]').textContent, /Budget −20% · burst 3×/)
    assert.equal(document.querySelector('[data-projection]'), null, 'no projection while a burst runs')
    await ui.click(button('End burst'))
    assert.ok(calls.some(([p, o]) => p === '/v1/throttle/burst' && o?.method === 'DELETE'))
    assert.equal(document.querySelector('[data-burst]'), null)
    assert.ok(button('Start 3× for 4 h'))
  } finally {
    await ui.close()
  }
})

test('manual mode: budget charts without a burst control; no budget endpoint: no budget section', async () => {
  P.saveView(V())
  const ctx = ctxWith((path) => (path === '/v1/throttle' ? throttle() : path.startsWith('/v1/budget') ? budgetFixture({ projection: null }) : forRange(path)))
  const ui = await mount(React.createElement(P.Throughput, { ctx }))
  try {
    await ui.click(button('Show throughput'))
    await ui.flush()
    assert.ok(ctx.calls.some(([p]) => p === '/v1/budget'), 'no burst is costed on a manual level')
    assert.match(document.querySelector('[data-slot="burst"]').textContent, /Burst needs budget mode/)
    assert.equal(document.querySelectorAll('[data-budget-chart]').length, 3)
    assert.equal(document.querySelector('[data-slider-label]'), null)
    assert.equal(document.querySelector('[data-slot="throttle"] input[type=range]').getAttribute('aria-label'), 'Token throttle percent')
  } finally {
    await ui.close()
  }
  const ui2 = await mount(React.createElement(P.Throughput, { ctx: ctxWith(withThrottle({ value: throttle() })) }))
  try {
    await ui2.click(button('Show throughput'))
    assert.equal(document.querySelector('[data-slot="budget"]'), null)
    assert.equal(document.querySelectorAll('[data-chart]').length, 3)
  } finally {
    await ui2.close()
  }
})

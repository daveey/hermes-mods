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

test('register: titlebar chip left of llm-split, status-bar fallback, palette entry, CSS removed on dispose', () => {
  const regs = []
  const disposers = []
  P.default.register({ register: (r) => regs.push(r), onDispose: (f) => { disposers.push(f) } })
  assert.deepEqual(regs.map((r) => [r.id, r.area, r.order]), [['titlebar-chip', 'titleBar.tools.right', 40], ['chip', 'statusBar.right', 7], ['show-throughput', 'palette', undefined]])
  const tool = regs[0].data
  assert.equal(tool.span, P.TITLEBAR_SPAN)
  assert.equal(tool.className, 'hermes-tp-tool')
  assert.ok([...document.head.querySelectorAll('style')].some((s) => s.textContent.includes(`.hermes-tp-tool{width:calc(${P.TITLEBAR_SPAN}*`)))
  assert.ok([...document.head.querySelectorAll('style')].some((s) => s.textContent.includes('.hermes-tp-chart')))
  for (const dispose of disposers.reverse()) dispose()
  assert.ok(![...document.head.querySelectorAll('style')].some((s) => s.textContent.includes('.hermes-tp-chart')))
})

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
    anthropic: { rate_now: 10, extra_tokens: 36000, exhausts_during_burst_at: null, earlier_s: 3600, rate_after: 4.5, rate_cap_5h: null, exceeds_5h_cap: null },
    'openai-codex': { rate_now: 20, extra_tokens: 200, exhausts_during_burst_at: END + 300, earlier_s: 10, rate_after: 0, rate_cap_5h: 30, exceeds_5h_cap: true } } },
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
  assert.equal(P.burstText(auto().burst, END), 'Raised 2× · 40 min left')
  assert.equal(P.burstText(auto().burst, END + 2400), null)
  assert.equal(P.burstText(null, END), null)
  assert.equal(P.fmtHours(0.5), '30 min')
  assert.equal(P.fmtHours(4), '4 h')
  const pr = budgetFixture().projection.pools
  assert.deepEqual(P.projectionText('anthropic', pr.anthropic, 2),
    { text: 'Claude: runs out ~60 min earlier at today’s rate · after the raise the planner slows to 4.5 tok/s (now 10)', warn: null, warn5h: null })
  const gpt = P.projectionText('openai-codex', pr['openai-codex'], 2)
  assert.match(gpt.text, /^GPT: runs out ~1 min earlier/)
  assert.match(gpt.warn, /^GPT would hit its reserve during the raise, at \S+ \d\d:\d\d$/)
  // 2 x 20 = 40 tok/s over a 30 tok/s 5-hour cap
  assert.equal(gpt.warn5h, 'GPT would run over its 5-hour cap (30 tok/s against 40 tok/s): accounts reach their 5-hour reserve and wait for the window to reset')
  assert.equal(P.projectionText('openai-codex', { ...pr['openai-codex'], exceeds_5h_cap: false }, 2).warn5h, null)
  assert.match(P.projectionText('anthropic', { rate_now: 0, extra_tokens: 0, earlier_s: 0, rate_after: 0 }).text, /a raise adds nothing/)
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
  assert.equal(a.marks[0].label, 'Claude early refill (5 h window)')
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


// ── token tiles (design C), the limit control, raise, forecast, the chip menu ──────────────────────────────

const onP = { state: 'on', publisher: 'hub', budget_seen: true }
const budgetT = (over = {}) => throttle({ mode: 'auto', percent: 0, cap_per_s: 20, holding: false, burst: null, pacing: onP, user_cap_per_s: null,
  budget: { state: 'fresh', age_s: 60, budget_per_s: 20, cap_per_s: 20, pool_budgets: { anthropic: 8, 'openai-codex': 12 }, pool_caps: {}, burst_factor: 1 }, ...over })
const limitT = (perS, over = {}) => throttle({ mode: 'manual', percent: 0.001, cap_per_s: perS, user_cap_per_s: perS, holding: false, burst: null, pacing: onP, budget: null, ...over })
const offT = (over = {}) => throttle({ mode: 'manual', percent: 0, cap_per_s: null, user_cap_per_s: null, holding: false, burst: null, budget: null, pacing: { state: 'off', publisher: 'hub', budget_seen: true }, ...over })
const tilesEl = () => document.querySelector('[data-slot="spend"]')
const tile = (k) => tilesEl().querySelector(`[data-tile="${k}"]`)

test('tiles maths: the choice, the three tiles, account rows, the limit field (hand-computed)', () => {
  assert.equal(P.capRate(throttle(), 'm'), 300)
  assert.equal(P.burnScale(throttle()), 0.5)
  assert.equal(P.fmtDuration(73200), '20.3 h')
  // what a person chose
  assert.equal(P.limitChoice(budgetT()), 'budget')
  assert.equal(P.limitChoice(limitT(5)), 'limit')
  assert.equal(P.limitChoice(offT()), 'no_limit')
  assert.equal(P.limitChoice(offT({ cap_per_s: 5, user_cap_per_s: 5 })), 'limit')  // a plain cap, budget off
  assert.equal(P.limitChoice(throttle({ percent: 100, cap_per_s: 0 })), 'limit')
  assert.equal(P.limitChoice(throttle({ percent: 0, cap_per_s: null })), 'no_limit')  // an older backend, nothing set
  assert.equal(P.limitChoice(null), null)
  assert.equal(P.budgetOffered(budgetT()), true)
  assert.equal(P.budgetOffered(throttle()), false, 'an older backend without the pacing switch offers no Budget')
  assert.equal(P.budgetOffered(throttle({ pacing: { state: 'unavailable', budget_seen: false } })), false)
  // tiles: 600 tok/m against a 300 limit of one's own: at the limit
  const t = P.tiles(limitT(5, { slot_gate: { allowed: 6, running: 4, per_slot_per_s: 50 } }), 'm')
  assert.deepEqual([t.choice, t.state, t.limitLabel, t.limitBig, t.limitNote], ['limit', 'at', 'Limit · your limit', '300', 'at the limit, calls queue'])
  assert.deepEqual(t.slots, { big: '4', of: '6', note: 'running · open on this gateway' })
  const b = P.tiles(budgetT({ budget: { state: 'fresh', burst_factor: 2 } }), 'm')
  assert.deepEqual([b.limitLabel, b.limitBig, b.state], ['Limit · subscription budget · raised 2×', '1.2k', 'under'])
  const o = P.tiles(offT({ slot_gate: { allowed: null, running: 9 } }), 'm')
  assert.deepEqual([o.limitLabel, o.limitBig, o.limitNote, o.state], ['Limit · no limit', 'None', 'nothing limits spend', 'unlimited'])
  assert.deepEqual(o.slots, { big: '9', of: null, note: 'running here · no slot limit' })
  assert.equal(P.tiles(throttle({ percent: 100, cap_per_s: 0 }), 'm').state, 'holding')
  assert.equal(P.tiles(throttle(), 'm').slots, null)
  // account rows: blocks per account, time left at the limit (half the rate: twice as long), a state
  const [gpt, claude] = throttle().pools
  const g = P.accountRow(gpt, throttle(), END)
  assert.deepEqual([g.name, g.total, g.usable, g.text, g.state], ['GPT', 3, 2, '2 of 3 · 40.7 h', 'ok'])
  assert.match(g.title, /^GPT: ok\n2 of 3 accounts usable\nlasts 40\.7 h at the limit\nnext refill /)
  assert.equal(P.accountRow(gpt, throttle({ cap_per_s: null }), END).state, 'tight')  // 20.3 h, refill in 24 h
  assert.equal(P.accountRow(gpt, throttle({ cap_per_s: 0 }), END).text, '2 of 3 · paused')
  const c = P.accountRow(claude, throttle(), END)
  assert.deepEqual([c.text, c.state], ['0 of 2 · idle', 'used'])
  assert.match(c.title, /^Claude: every account has used its week/)
  assert.equal(P.accountRow({ ...gpt, pacing: 'held' }, throttle(), END).state, 'held')
  assert.equal(P.accountRow({ ...gpt, pacing: 'paced' }, throttle(), END).state, 'paced')
  assert.equal(P.accountRow({ provider: 'xai-oauth', runway_s: null }, throttle(), END).text, 'idle')
  assert.equal(P.parseLimitK('150'), 150000)
  assert.equal(P.parseLimitK('1.5m'), 1500000)
  assert.equal(P.parseLimitK('lots'), undefined)
})

test('panel: three tiles on top, one row per subscription with a block per account, the limit line on Tokens only', async () => {
  P.saveView(V())
  const ctx = ctxWith(withThrottle({ value: limitT(5, { slot_gate: { allowed: 6, running: 4, per_slot_per_s: 50 } }) }))
  const ui = await mount(React.createElement(P.Throughput, { ctx }))
  try {
    assert.ok(!ctx.calls.some(([p]) => p === '/v1/throttle'), 'the limit is not polled while the panel is closed')
    await ui.click(button('Show throughput'))
    const kids = [...document.querySelector('.hermes-tp').children]
    assert.ok(kids.findIndex((e) => e.dataset.slot === 'spend') < kids.findIndex((e) => e.matches('[data-chart]')), 'tiles come first')
    assert.equal(tilesEl().dataset.state, 'at')
    assert.equal(tile('rate').textContent, 'Spending now600tokens / min')
    assert.equal(tile('limit').textContent, 'Limit · your limit300at the limit, calls queue')
    assert.equal(tile('slots').textContent, 'Card slots4 / 6running · open on this gateway')
    const gpt = tilesEl().querySelector('[data-pool="openai-codex"]')
    assert.equal(gpt.querySelectorAll('.hermes-tp-blocks > span').length, 3)
    assert.equal(gpt.querySelectorAll('.hermes-tp-blocks > span.is-on').length, 2)
    assert.equal(gpt.querySelector('.hermes-tp-aleft').textContent, '2 of 3 · 40.7 h')
    assert.equal(tilesEl().querySelector('[data-pool="anthropic"]').dataset.poolState, 'used')
    assert.equal(document.querySelector('input[type=range]'), null, 'no slider')
    assert.equal(document.querySelector('[data-slot="pacing"]'), null, 'no separate switch: Budget / No limit is the switch')
    assert.equal(tilesEl().querySelector('[data-choice="budget"]').getAttribute('aria-pressed'), 'false')
    assert.equal(tilesEl().querySelector('[data-choice="limit"]').getAttribute('aria-pressed'), 'true')
    const [line] = capLines('tokens')
    assert.equal(line.dataset.cap, '300')
    assert.equal(Number(line.getAttribute('y1')), 37)  // H 84, baseline 70, 300/m half the 600/m peak
    assert.match(document.querySelector('[data-chart="tokens"] .hermes-tp-capl').textContent, /^limit 300\/m$/)
    assert.equal(capLines('sessions').length + capLines('cards').length, 0)
    await ui.click(button('+cache'))
    assert.equal(capLines('tokens').length, 0)
  } finally {
    await ui.close()
  }
})

test('panel: a limit above the chart pins to the top; no limit draws no line; holding sits on the baseline', async () => {
  P.saveView(V())
  for (const [value, y] of [[limitT(18), 4], [offT(), null], [throttle({ percent: 100, cap_per_s: 0 }), 70]]) {
    const ui = await mount(React.createElement(P.Throughput, { ctx: ctxWith(withThrottle({ value })) }))
    try {
      await ui.click(button('Show throughput'))
      if (y == null) {
        assert.equal(capLines('tokens').length, 0)
        assert.equal(tile('limit').textContent, 'Limit · no limitNonenothing limits spend')
      } else assert.equal(Number(capLines('tokens')[0].getAttribute('y1')), y)
    } finally {
      await ui.close()
    }
  }
})

// A backend stand-in: the cap route and the pacing switch, with the requests recorded.
function limitBackend(start) {
  const state = { value: start, fail: false }
  const calls = []
  const ctx = {
    calls,
    rest: async (path, opts) => {
      calls.push([path, opts])
      if (path === '/v1/throttle' && opts?.method === 'PUT') {
        if (state.fail) throw Error('HTTP 400')
        const c = opts.body.cap_per_min
        const off = state.value.pacing?.state === 'off'
        state.value = c == null ? (off ? offT() : budgetT()) : limitT(c / 60, off ? { pacing: { state: 'off', publisher: 'hub', budget_seen: true } } : {})
        return state.value
      }
      if (path === '/v1/throttle/pacing') {
        if (state.fail) throw Error('HTTP 400')
        state.value = opts.body.on ? budgetT() : offT()
        return state.value
      }
      if (path === '/v1/throttle') return state.value
      if (path.startsWith('/v1/budget')) return budgetFixture()
      return forRange(path)
    }
  }
  const puts = () => calls.filter(([, o]) => o?.method === 'PUT').map(([p, o]) => [p, o.body])
  return { state, ctx, puts }
}
const setText = async (el, v) => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, String(v))
  await React.act(async () => el.dispatchEvent(new window.Event('input', { bubbles: true })))
}
const enter = async (el) => React.act(async () => el.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true })))

test('limit control: Budget / Limit / No limit send the right requests; a number only on Enter or Set', async () => {
  P.saveView(V())
  const be = limitBackend(budgetT())
  const ui = await mount(React.createElement(P.Throughput, { ctx: be.ctx }))
  const choice = (c) => tilesEl().querySelector(`[data-choice="${c}"]`)
  const field = () => tilesEl().querySelector('input[aria-label^="Limit"]')
  try {
    await ui.click(button('Show throughput'))
    await ui.flush()
    assert.equal(choice('budget').getAttribute('aria-pressed'), 'true')
    await ui.click(choice('budget'))
    assert.deepEqual(be.puts(), [], 'choosing what is chosen sends nothing')
    // type, then change your mind: nothing is sent
    await ui.click(choice('limit'))
    await setText(field(), '150')
    await ui.event(field(), 'blur')
    await ui.click(choice('budget'))
    assert.deepEqual(be.puts(), [])
    // junk refused; Enter sets a limit
    await ui.click(choice('limit'))
    await setText(field(), 'lots')
    await enter(field())
    assert.deepEqual(be.puts(), [])
    assert.match(tilesEl().querySelector('[role=alert]').textContent, /e\.g\. 150 or 1\.5m/)
    await setText(field(), '0.6')
    await ui.click(button('Set'))
    await ui.flush()
    assert.deepEqual(be.puts(), [['/v1/throttle', { cap_per_min: 600 }]])
    assert.equal(tile('limit').textContent, 'Limit · your limit600under the limit')  // 600 against 600
    // No limit: the own cap is cleared, then budget pacing is switched off
    await ui.click(choice('no_limit'))
    await ui.flush()
    assert.deepEqual(be.puts().slice(1), [['/v1/throttle', { cap_per_min: null }], ['/v1/throttle/pacing', { on: false }]])
    assert.equal(tilesEl().dataset.state, 'unlimited')
    assert.equal(tilesEl().querySelector('[data-slot="burst"]'), null, 'nothing to raise without a budget')
    // Budget again: pacing back on (no own cap to clear)
    await ui.click(choice('budget'))
    await ui.flush()
    assert.deepEqual(be.puts().slice(3), [['/v1/throttle/pacing', { on: true }]])
    assert.equal(choice('budget').getAttribute('aria-pressed'), 'true')
    be.state.fail = true
    await ui.click(choice('no_limit'))
    await ui.flush()
    assert.ok([...tilesEl().querySelectorAll('[role=alert]')].some((e) => /^Limit not saved: HTTP 400/.test(e.textContent)))
  } finally {
    await ui.close()
  }
})

test('an older backend without the pacing switch: no Budget choice; no throttle endpoint: no tiles', async () => {
  P.saveView(V())
  const ui = await mount(React.createElement(P.Throughput, { ctx: ctxWith(withThrottle({ value: throttle() })) }))
  try {
    await ui.click(button('Show throughput'))
    assert.equal(tilesEl().querySelector('[data-choice="budget"]'), null)
    assert.ok(tilesEl().querySelector('[data-choice="limit"]'))
    assert.equal(document.querySelector('[data-slot="forecast"]'), null, 'no budget endpoint: no forecast')
  } finally {
    await ui.close()
  }
  const ctx = ctxWith((path) => { if (path === '/v1/throttle') throw Error('HTTP 404'); return forRange(path) })
  const ui2 = await mount(React.createElement(P.Throughput, { ctx }))
  try {
    await ui2.click(button('Show throughput'))
    assert.equal(tilesEl(), null)
    assert.equal(document.querySelectorAll('[data-chart]').length, 3)
  } finally {
    await ui2.close()
  }
})

test('raise: one button costed on hover, choices on demand, a reserve warning inline, start and end; forecast folded', async () => {
  P.saveView(V())
  const state = { throttle: budgetT() }
  const calls = []
  const ctx = {
    calls,
    rest: async (path, opts) => {
      calls.push([path, opts])
      if (path === '/v1/throttle') return state.throttle
      if (path === '/v1/throttle/burst') {
        state.throttle = opts.method === 'PUT'
          ? budgetT({ burst: { factor: opts.body.factor, until: Date.now() / 1000 + opts.body.hours * 3600, set_at: Date.now() / 1000, active: true }, budget: { state: 'fresh', burst_factor: opts.body.factor } })
          : budgetT()
        return state.throttle
      }
      if (path.startsWith('/v1/budget')) {
        const q = new URL('http://x' + path).searchParams
        return budgetFixture({ projection: { ...budgetFixture().projection, factor: Number(q.get('factor')), hours: Number(q.get('hours')) } })
      }
      return forRange(path)
    }
  }
  const ui = await mount(React.createElement(P.Throughput, { ctx }))
  try {
    await ui.click(button('Show throughput'))
    await ui.flush()
    const raise = () => tilesEl().querySelector('[data-raise-tip]')
    assert.equal(raise().textContent, 'Raise 2× · 1 h')
    assert.match(raise().dataset.raiseTip, /^Claude: runs out ~60 min earlier at today’s rate · after the raise the planner slows to 4\.5 tok\/s/)
    assert.match(tilesEl().querySelector('[data-raise-warn]').textContent, /^⚠ GPT would hit its reserve during the raise, at .* \(\+1 more on hover\)$/)
    assert.equal(button('3×'), undefined, 'the choices are folded away')
    await ui.click(tilesEl().querySelector('[aria-label="Choose how much and how long"]'))
    await ui.click(button('3×'))
    await ui.click(button('4 h'))
    await ui.flush()
    assert.equal(raise().textContent, 'Raise 3× · 4 h')
    await ui.click(raise())
    assert.deepEqual(calls.find(([p, o]) => p === '/v1/throttle/burst' && o?.method === 'PUT')[1].body, { factor: 3, hours: 4 })
    assert.match(tilesEl().querySelector('[data-burst]').textContent, /^Raised 3× · 4\.0 h left$/)
    assert.equal(tile('limit').querySelector('.hermes-tp-tlabel').textContent, 'Limit · subscription budget · raised 3×')
    await ui.click(button('End raise'))
    assert.ok(calls.some(([p, o]) => p === '/v1/throttle/burst' && o?.method === 'DELETE'))
    // the 7-day forecast, folded, with plain labels
    const f = document.querySelector('[data-slot="forecast"]')
    assert.equal(f.tagName, 'DETAILS')
    assert.equal(f.open, false)
    assert.equal(f.querySelector('summary').textContent, 'Forecast · 7 days')
    assert.match(f.querySelector('.hermes-tp-blegend').textContent, /^usedplannedavailablerefillearly refillaccount addedraise$/)
    assert.deepEqual([...f.querySelectorAll('[data-budget-chart]')].map((e) => e.dataset.budgetChart), ['total', 'anthropic', 'openai-codex'])
    assert.match(f.querySelector('[data-budget-chart="anthropic"] .hermes-tp-lbl').textContent, /^used in 7 d: 600 · still available until \S+ \d\d:\d\d: 1\.5k$/)
  } finally {
    await ui.close()
  }
})

test('chip menu: right-click the chip for Budget / Limit… / No limit and a raise, the current choice checked', async () => {
  P.saveView(V())
  const be = limitBackend(budgetT())
  const ui = await mount(React.createElement(P.Throughput, { ctx: be.ctx }))
  const openMenu = async () => {
    const chip = document.querySelector('[data-slot="throughput-chip"]')
    await React.act(async () => chip.dispatchEvent(new window.MouseEvent('contextmenu', { bubbles: true, clientX: 5, clientY: 5 })))
    await ui.flush()
  }
  const item = (k) => document.querySelector(`[data-slot="throughput-menu"] [data-menu="${k}"]`)
  try {
    await openMenu()
    await ui.flush()
    assert.ok(be.ctx.calls.some(([p]) => p === '/v1/throttle'), 'the menu loads the limit even with the panel closed')
    assert.equal(item('budget').getAttribute('aria-checked'), 'true')
    assert.equal(item('no_limit').getAttribute('aria-checked'), 'false')
    assert.equal(item('raise-2').textContent, 'Raise 2× for 1 h')
    await ui.click(item('no_limit'))
    await ui.flush()
    assert.deepEqual(be.puts(), [['/v1/throttle/pacing', { on: false }]])
    await openMenu()
    assert.equal(item('no_limit').getAttribute('aria-checked'), 'true')
    assert.equal(item('raise-2'), null, 'nothing to raise with no limit')
    await ui.click(item('budget'))
    await ui.flush()
    assert.deepEqual(be.puts().slice(1), [['/v1/throttle/pacing', { on: true }]])
    await openMenu()
    await ui.click(item('raise-3'))
    await ui.flush()
    assert.deepEqual(be.ctx.calls.filter(([p, o]) => p === '/v1/throttle/burst' && o?.method === 'PUT').map(([, o]) => o.body), [{ factor: 3, hours: 1 }])
    await openMenu()
    await ui.click(item('limit'))
    await ui.flush()
    assert.ok(tilesEl(), 'Limit… opens the panel at the tiles')
  } finally {
    await ui.close()
  }
})

test('review fixes: no pacing switch without a publisher; one change at a time; budget starting; menu errors; small caps', async () => {
  P.saveView(V())
  // a fleet without a budget publisher: No limit clears the cap and never writes a pacing Off
  const noPub = limitBackend(limitT(5, { pacing: { state: 'unavailable', publisher: null, budget_seen: false } }))
  let ui = await mount(React.createElement(P.Throughput, { ctx: noPub.ctx }))
  try {
    await ui.click(button('Show throughput'))
    await ui.flush()
    assert.equal(tilesEl().querySelector('[data-choice="budget"]'), null)
    assert.equal(tilesEl().querySelector('input[aria-label^="Limit"]').value, '0.3', 'a cap under 10k shows a decimal')
    await ui.click(tilesEl().querySelector('[data-choice="no_limit"]'))
    await ui.flush()
    assert.deepEqual(noPub.puts(), [['/v1/throttle', { cap_per_min: null }]])
  } finally {
    await ui.close()
  }
  // one change at a time: a click while one saves is ignored, and the controls are disabled meanwhile
  const slow = limitBackend(limitT(5))
  let release
  const gate = new Promise((r) => { release = r })
  const rest = slow.ctx.rest
  slow.ctx.rest = async (path, opts) => { if (opts?.method === 'PUT') await gate; return rest(path, opts) }
  ui = await mount(React.createElement(P.Throughput, { ctx: slow.ctx }))
  try {
    await ui.click(button('Show throughput'))
    await ui.flush()
    await ui.click(tilesEl().querySelector('[data-choice="no_limit"]'))
    assert.equal(tilesEl().querySelector('[data-choice="budget"]').disabled, true)
    await ui.click(tilesEl().querySelector('[data-choice="budget"]'))
    release()
    await ui.flush()
    await ui.flush()
    assert.deepEqual(slow.puts(), [['/v1/throttle', { cap_per_min: null }], ['/v1/throttle/pacing', { on: false }]], 'the second click was not queued behind the first')
  } finally {
    await ui.close()
  }
  // the budget while it starts
  const pending = P.tiles(throttle({ mode: 'manual', percent: 0, cap_per_s: null, user_cap_per_s: null, pacing: { state: 'pending', publisher: 'hub', budget_seen: true } }), 'm')
  assert.deepEqual([pending.choice, pending.limitBig, pending.limitNote], ['budget', '…', 'budget starting (a few minutes)'])
  const nobody = P.tiles(throttle({ mode: 'manual', percent: 0, cap_per_s: null, user_cap_per_s: null, pacing: { state: 'unavailable', publisher: null, budget_seen: true } }), 'm')
  assert.equal(nobody.limitNote, 'no hub is publishing a budget')
  // the menu: a failed limit read and an earlier error are shown in it
  const failing = ctxWith((path) => { if (path === '/v1/throttle') throw Error('HTTP 404'); return forRange(path) })
  ui = await mount(React.createElement(P.Throughput, { ctx: failing }))
  try {
    const chip = document.querySelector('[data-slot="throughput-chip"]')
    await React.act(async () => chip.dispatchEvent(new window.MouseEvent('contextmenu', { bubbles: true, clientX: 5, clientY: 5 })))
    await ui.flush()
    await ui.flush()
    assert.equal(document.querySelector('[data-slot="throughput-menu"] [data-menu="loading"]').textContent, 'Limit unavailable')
  } finally {
    await ui.close()
  }
  // a change that failed is shown in the menu the next time it opens
  const be = limitBackend(budgetT())
  be.state.fail = true
  ui = await mount(React.createElement(P.Throughput, { ctx: be.ctx }))
  try {
    const open = async () => {
      const chip = document.querySelector('[data-slot="throughput-chip"]')
      await React.act(async () => chip.dispatchEvent(new window.MouseEvent('contextmenu', { bubbles: true, clientX: 5, clientY: 5 })))
      await ui.flush()
      await ui.flush()
    }
    await open()
    await ui.click(document.querySelector('[data-slot="throughput-menu"] [data-menu="no_limit"]'))
    await ui.flush()
    await open()
    assert.equal(document.querySelector('[data-slot="throughput-menu"] [data-menu="error"]').textContent, '⚠ HTTP 400')
  } finally {
    await ui.close()
  }
})

test('push: watch registers the view, pushes update the chip, polls slow to the safety read, resume when pushes stop', async () => {
  localStorage.setItem(P.STORE_KEY, JSON.stringify({ unit: 'm', range: '1h', cache: false }))
  const listeners = new Map()
  const FEED = 'hub:77'
  const ctx = ctxWith((path) => path === '/v1/watch'
    ? { feed: FEED, series_keys: ['1h|gateway'], series: { '1h|gateway': payload() }, throttle: null }
    : forRange(path))
  ctx.onEvent = (type, fn) => { listeners.set(type, fn); return () => listeners.delete(type) }
  const ui = await mount(React.createElement(P.Throughput, { ctx }))
  const realNow = Date.now
  try {
    const watch = ctx.calls.find(([p]) => p === '/v1/watch')
    assert.ok(watch, 'the window registered its view')
    assert.deepEqual(watch[1].body, { series: [{ range: '1h', by: 'gateway' }], advisory: false })
    const series = () => ui.client.getQueryCache().find({ queryKey: ['throughput', 'series', '1h'] })
    assert.equal(series().observers[0].options.refetchInterval, P.POLL_MS, 'no push yet: the old cadence')
    const faster = payload()
    faster.recent.tokens_io.hub = faster.recent.tokens_io.hub.map((v) => (v == null ? null : 1200))
    await React.act(async () => listeners.get(P.SNAPSHOT_EVENT)({ payload: { feed: 'other:1', series_keys: ['1h|gateway'], series: { '1h|gateway': faster } } }))
    assert.match(button('Show throughput').textContent, /600 tok\/m/, 'another gateway\'s push is ignored')
    await React.act(async () => listeners.get(P.SNAPSHOT_EVENT)({ payload: { feed: FEED, series_keys: ['1h|gateway'], series: { '1h|gateway': faster } } }))
    await ui.flush()
    assert.match(button('Show throughput').textContent, /1\.2k tok\/m/, 'the pushed series is drawn')
    assert.equal(series().observers[0].options.refetchInterval, P.SAFETY_MS, 'live: only the safety read')
    // Hidden: nothing polls.
    const vis = async (state) => {
      Object.defineProperty(document, 'visibilityState', { value: state, configurable: true })
      await React.act(async () => document.dispatchEvent(new window.Event('visibilitychange')))
      await ui.flush()
    }
    await vis('hidden')
    assert.equal(series().observers[0].options.refetchInterval, false)
    // Pushes stopped meanwhile: once shown and PUSH_TRUST_MS past the last push, the 30 s polls are back (the same
    // check runs every 15 s while shown).
    const t0 = realNow()
    Date.now = () => t0 + P.PUSH_TRUST_MS + 1
    await vis('visible')
    assert.equal(series().observers[0].options.refetchInterval, P.POLL_MS, 'fallback to polling')
  } finally {
    Date.now = realNow
    delete document.visibilityState
    await ui.close()
  }
  assert.equal(listeners.size, 0, 'unsubscribed on unmount')
})

test('applySnapshot: series by name only are re-read; invalid bodies never land; the throttle is taken', () => {
  const set = [], invalidated = []
  const client = { setQueryData: (k, v) => set.push([k, v]), invalidateQueries: (o) => invalidated.push(o.queryKey) }
  assert.equal(P.applySnapshot(client, { series_keys: ['6h|model', '7d|gateway', 'bad|gateway'], series: { '7d|gateway': { nope: 1 } }, throttle: { percent: 40 } }), true)
  assert.deepEqual(invalidated, [['throughput', 'series', '6h', 'model'], ['throughput', 'series', '7d']])
  assert.deepEqual(set, [[['throughput', 'throttle'], { percent: 40 }]])
  assert.equal(P.applySnapshot(client, null), false)
})

test('push: one that leaves out this window\'s view is not trusted and the window watches again', async () => {
  localStorage.setItem(P.STORE_KEY, JSON.stringify({ unit: 'm', range: '1h', cache: false }))
  const listeners = new Map()
  const FEED = 'hub:78'
  const ctx = ctxWith((path) => path === '/v1/watch' ? { feed: FEED, series_keys: [], series: {}, throttle: null } : forRange(path))
  ctx.onEvent = (type, fn) => { listeners.set(type, fn); return () => listeners.delete(type) }
  const ui = await mount(React.createElement(P.Throughput, { ctx }))
  const realNow = Date.now
  try {
    const watches = () => ctx.calls.filter(([p]) => p === '/v1/watch').length
    assert.equal(watches(), 1)
    const series = () => ui.client.getQueryCache().find({ queryKey: ['throughput', 'series', '1h'] })
    const t0 = realNow()
    Date.now = () => t0 + P.REWATCH_MS + 1
    // Our gateway pushes, but only other windows' views (ours was pushed out of its four).
    await React.act(async () => listeners.get(P.SNAPSHOT_EVENT)({ payload: { feed: FEED, series_keys: ['6h|gateway', '24h|gateway', '7d|gateway', '6h|model'], series: {} } }))
    await ui.flush()
    assert.equal(series().observers[0].options.refetchInterval, P.POLL_MS, 'not live: our view was not pushed')
    assert.equal(watches(), 2, 're-registered')
    await React.act(async () => listeners.get(P.SNAPSHOT_EVENT)({ payload: { feed: FEED, series_keys: ['6h|gateway'], series: {} } }))
    await ui.flush()
    assert.equal(watches(), 2, 'at most one re-watch per REWATCH_MS')
    await React.act(async () => listeners.get(P.SNAPSHOT_EVENT)({ payload: { feed: FEED, series_keys: ['1h|gateway'], series: { '1h|gateway': payload() } } }))
    await ui.flush()
    assert.equal(series().observers[0].options.refetchInterval, P.SAFETY_MS, 'covered: live')
  } finally {
    Date.now = realNow
    await ui.close()
  }
})

test('pushCovers: every view this window shows must be named', () => {
  assert.equal(P.pushCovers({ series_keys: ['1h|gateway', '1h|model'] }, ['1h|gateway', '1h|model']), true)
  assert.equal(P.pushCovers({ series_keys: ['1h|gateway'] }, ['1h|gateway', '1h|model']), false)
  assert.equal(P.pushCovers({}, ['1h|gateway']), false)
})

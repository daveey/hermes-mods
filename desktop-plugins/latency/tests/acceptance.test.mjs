// Real Chromium + React/Radix, fixture HTTP API. No production samples are fabricated.
import test from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'vite'
import { chromium } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import { readFile, mkdir } from 'node:fs/promises'
const root = fileURLToPath(new URL('.', import.meta.url))
const spark = Array.from({ length: 8 }, (_, i) => ({ day: `2026-11-${20+i}`, p50: 100+i*10, p90: 300+i*10, n: 10 }))
function fixture(range = '7d') {
  const rows = [
    { id: 'turn_first', label: 'Turn: first reply', group: 'Using Hermes', n: 80, p50: 140, p90: 340, target_p90: 100, status: 'bad', spark, series: spark, hours: [], slowest: [{ id: 'slow-1', start: 1795800000, dur_ms: 400, attrs: { session: 'session-1' }, link: { kind: 'session', id: 'session-1', gateway: 'hermes-hub', profile: 'default' } }], stages: [{ name: 'queue', dur_ms: 40 }, { name: 'model', dur_ms: 300 }] },
    { id: 'ui.session_switch', label: 'Switch session', group: 'Using Hermes', n: 0, p50: null, p90: null, target_p90: 250, status: 'none', spark: [], series: [], hours: [], slowest: [] },
    { id: 'card_queue', label: 'Card waits for worker', group: 'Cards & workers', n: 3, p50: 100, p90: 200, target_p90: 300, status: 'ok', spark: [{ p50: 0, p90: 0 }], series: [{ day: '2026-11-27', p50: 0, p90: 0 }], slowest: [] },
    { id: 'ci_core', label: 'Core CI', group: 'Ship', n: 3, p50: 100, p90: 400, target_p90: 300, status: 'warn', spark, series: spark, slowest: [] }
  ]
  // Deliberately disagree with the row list: the chip MUST use the server count.
  return { range, at: 1795800000, groups: ['Using Hermes', 'Cards & workers', 'Ship'].map(label => ({ label, rows: rows.filter(r => r.group === label) })), chip: { over_target_count: 12, spark, worst: Array.from({ length: 5 }, (_, i) => ({ ...rows[0], id: 'worst-'+i, label: 'Worst metric '+i })) } }
}

test('Ledger browser acceptance and Desktop span batching', { timeout: 90000 }, async () => {
  const server = await createServer({ root, configFile: false, resolve: { alias: { '@hermes/plugin-sdk': root+'sdk.js', react: root+'node_modules/react', 'react-dom': root+'node_modules/react-dom' }, dedupe: ['react', 'react-dom'] }, server: { port: 0, host: '127.0.0.1', fs: { allow: [root+'..'] } } })
  await server.listen()
  let browser
  try { browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] }) }
  catch (error) { await server.close(); throw error }
  const page = await browser.newPage({ viewport: { width: 900, height: 800 } })
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  const requests = []
  await page.route('**/api/**', route => {
    const url = new URL(route.request().url())
    requests.push(url.pathname+url.search)
    return route.fulfill({ json: fixture(url.searchParams.get('range') || '7d') })
  })
  try {
    await page.goto(server.resolvedUrls.local[0])
    const cardRoutes = await page.evaluate(async moduleUrl => {
      const { openSample } = await import(moduleUrl)
      const calls = []
      const hook = (...args) => calls.push(args)
      window.__hermesKanbanOpenTask = hook
      const sample = { link: { kind: 'card', id: 'card-1', board: 'work', gateway: 'hermes-hub', profile: 'other-profile' } }
      let refused = false
      try { await openSample({}, sample) } catch { refused = true }
      const oldCalls = calls.length
      hook.supportsQualifiedOwners = true
      await openSample({}, sample)
      delete window.__hermesKanbanOpenTask
      return { refused, oldCalls, calls }
    }, '/@fs/'+fileURLToPath(new URL('../plugin.js', import.meta.url)))
    assert.deepEqual(cardRoutes, { refused: true, oldCalls: 0, calls: [['card-1', 'work', 'hermes-hub', 'other-profile']] })
    await page.getByTestId('latency-count').filter({ hasText: '12' }).waitFor()
    assert.equal(await page.locator('.lat-chip svg path').count(), 2)
    const chip = page.getByRole('button', { name: 'Latency', exact: true })
    await chip.hover()
    const pop = page.getByTestId('latency-popover')
    await pop.waitFor()
    assert.equal(await pop.locator('.lat-pop-row').count(), 5)
    const p = await pop.boundingBox(), c = await chip.boundingBox()
    assert(p.y + p.height <= c.y, 'hover popover must open upward')
    assert(p.x >= 0 && p.x+p.width <= 900 && p.y >= 0, 'popover stays in window')
    await page.mouse.move(1, 1)
    await pop.waitFor({ state: 'hidden' })
    await chip.focus()
    await pop.waitFor()
    await chip.click()
    await page.getByTestId('ledger-panel').waitFor()
    const row = page.locator('[data-metric="turn_first"]')
    await row.click()
    await page.getByTestId('metric-detail').waitFor()
    assert.equal(await row.getAttribute('aria-expanded'), 'true')
    assert.equal(await page.getByTestId('target-line').count(), 1)
    assert.equal(await page.getByLabel('Stage split').count(), 1)
    await page.getByRole('button', { name: 'session-1', exact: true }).click()
    assert.deepEqual(await page.evaluate(() => window.opened[0]), ['session-1', { route: { connectionId: 'hermes-hub', mode: 'remote', profile: 'default', targetProfile: 'default' } }])
    const empty = page.locator('[data-metric="ui.session_switch"]')
    assert.match(await empty.innerText(), /not measured yet/)
    assert.equal(await empty.locator('svg').count(), 0)
    for (const range of ['24h', '30d', '90d', '7d']) {
      await Promise.all([page.waitForResponse(r => r.url().includes('range='+range)), page.getByRole('button', { name: range, exact: true }).click()])
    }
    assert(requests.some(p => p.includes('range=90d')))
    const sparse = page.locator('[data-metric="card_queue"]')
    assert.equal(await sparse.locator('circle[data-observation]').count(), 2)
    await sparse.click()
    assert.equal(await page.locator('.lat-chart circle[data-observation]').count(), 2)
    await row.click()
    await page.setViewportSize({ width: 664, height: 800 })
    assert(await page.locator('.lat-dialog').evaluate(e => Math.round(e.getBoundingClientRect().width)) === 640)
    assert(await page.locator('.lat-dialog').evaluate(e => e.scrollWidth <= e.clientWidth), 'no horizontal overflow in 640px panel')
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no window overflow')
    const evidence = process.env.LATENCY_EVIDENCE_DIR || root+'evidence'
    await mkdir(evidence, { recursive: true })
    await page.screenshot({ path: evidence+'/ledger-fixture.png' })
    await page.getByRole('button', { name: 'Close', exact: true }).click()

    // Test production transport implementation in the browser, including Web Crypto.
    const result = await page.evaluate(async () => {
      let clock = Date.now(), sent = [], offline = true
      const storage = new Map()
      const ctx = { storage: { get: (k, d) => storage.get(k) ?? d, set: (k, v) => storage.set(k, v) }, rest: async (path, req) => { if (offline) throw Error('offline'); sent.push({ path, ...req }); return { ok: true } } }
      const q = window.latency.spanQueue(ctx, () => clock)
      for (const name of window.latency.UI_METRICS) q.add({ name, start_ms: clock, duration_ms: 25, session_id: 'safe-session', card_id: 'card-1', body: 'PRIVATE', query: 'PRIVATE', host: 'desktop', gateway: 'gateway-1' })
      await q.flush()
      const buffered = q.size()
      clock += 3599000
      offline = false
      await q.flush()
      const drained = q.size()
      q.add({ name: 'ui.session_switch', start_ms: clock, duration_ms: 1 })
      clock += 3600001
      await q.flush()
      return { buffered, drained, expired: q.size(), sent }
    })
    assert.equal(result.buffered, 5)
    assert.equal(result.drained, 0)
    assert.equal(result.expired, 0)
    assert.equal(result.sent.length, 1)
    assert.equal(result.sent[0].path, '/v1/spans')
    assert.equal(result.sent[0].method, 'POST')
    for (const span of result.sent[0].body.spans) {
      assert.match(span.trace_id, /^[a-f0-9]{32}$/)
      assert.match(span.span_id, /^[a-f0-9]{16}$/)
      assert.equal(span.attributes['service.name'], 'hermes-desktop')
      assert.equal(BigInt(span.end_time_unix_nano)-BigInt(span.start_time_unix_nano), 25000000n)
    }
    assert(!JSON.stringify(result.sent).includes('PRIVATE'))
    assert.deepEqual(errors, [])
  } finally { await browser.close(); await server.close() }
})

test('chat proposal Add / target controls, failures and collecting panel row', { timeout: 90000 }, async () => {
  const server = await createServer({ root, configFile: false, resolve: { alias: { '@hermes/plugin-sdk': root+'sdk.js', react: root+'node_modules/react', 'react-dom': root+'node_modules/react-dom' }, dedupe: ['react', 'react-dom'] }, server: { port: 0, host: '127.0.0.1', fs: { allow: [root+'..'] } } })
  await server.listen()
  let browser
  try {
    browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] })
    const page = await browser.newPage({ viewport: { width: 664, height: 800 } })
    const errors = [], added = []
    page.on('pageerror', e => errors.push(e.message))
    let fail = true
    await page.route('**/api/**', async route => {
      if (route.request().method() === 'POST') {
        if (fail) { fail = false; return route.fulfill({ status: 503, json: { detail: 'retry' } }) }
        const d = route.request().postDataJSON().definition
        added.push(d)
        return route.fulfill({ json: { added: true, definition: { ...d, instrumentation_card: d.kind === 'span' ? { id: 'instrument-1', board: 'default', gateway: 'hermes-hub', profile: 'default' } : undefined } } })
      }
      const data = fixture()
      for (const d of added) data.groups[1].rows.push({ ...d, n: d.kind === 'derived' ? 30 : 0, p50: d.kind === 'derived' ? 10000 : null, p90: d.kind === 'derived' ? 10000 : null, status: d.kind === 'derived' ? 'ok' : 'none', collection_label: d.kind === 'span' ? 'collecting from today' : null, spark, series: spark, slowest: [] })
      return route.fulfill({ json: data })
    })
    await page.goto(server.resolvedUrls.local[0])
    await page.waitForFunction(() => !!window.renderWatch)
    const definition = { id: 'watch.derived', label: 'Decision requested → unblocked', group: 'Cards & workers', kind: 'derived', source: 'kanban', start: 'decision_requested', end: 'unblocked', unit: 'ms', target_p90: 60000 }
    const encode = d => Buffer.from(JSON.stringify(d)).toString('base64url')
    const encoded = encode(definition)
    await page.evaluate(d => window.renderWatch(d, true), encoded)
    const card = page.getByTestId('watch-proposal')
    await card.waitFor()
    assert.equal(added.length, 0, 'render is inert')
    assert(await card.getByRole('button', { name: 'Add', exact: true }).isDisabled(), 'streaming is inert')
    assert.match(await card.innerText(), /Start[\s\S]*decision_requested[\s\S]*End[\s\S]*unblocked[\s\S]*Target p90[\s\S]*History[\s\S]*30 days/)
    await page.evaluate(d => window.renderWatch(d), encoded)
    await card.getByRole('button', { name: 'Change target' }).click()
    await card.getByLabel('Target p90 (ms)').fill('0')
    assert(await card.getByRole('button', { name: 'Add', exact: true }).isDisabled())
    await card.getByLabel('Target p90 (ms)').fill('1500')
    await card.getByRole('button', { name: 'Add', exact: true }).click()
    await card.getByRole('alert').waitFor()
    await card.getByRole('button', { name: 'Add', exact: true }).click()
    await card.getByRole('status').waitFor()
    assert.equal(added.length, 1)
    assert.equal(added[0].target_p90, 1500)
    await page.evaluate(d => window.renderWatch(d), encode({ ...definition, id: 'watch.span', label: 'Submit → unblocked', kind: 'span', source: 'spans', start: 'decision.submit' }))
    await card.getByRole('button', { name: 'Add', exact: true }).click()
    await card.getByRole('status').waitFor()
    assert.match(await card.innerText(), /collecting from today/)
    await card.getByRole('button', { name: 'Instrumentation: instrument-1' }).waitFor()
    await page.getByRole('button', { name: 'Latency', exact: true }).click()
    await page.locator('[data-metric="watch.span"]').waitFor()
    assert.match(await page.locator('[data-metric="watch.span"]').innerText(), /collecting from today/)
    await page.locator('[data-metric="watch.derived"]').click()
    await page.getByTestId('metric-detail').waitFor()
    assert.equal(await page.getByTestId('target-line').count(), 1)
    const evidence = process.env.LATENCY_EVIDENCE_DIR || root+'evidence'
    await mkdir(evidence, { recursive: true })
    await page.screenshot({ path: evidence+'/watch-fixture.png' })
    assert.deepEqual(errors, [])
  } finally { if (browser) await browser.close(); await server.close() }
})

test('runtime imports stay within the real Desktop loader contract', async () => {
  const source = await readFile(new URL('../plugin.js', import.meta.url), 'utf8')
  const imports = [...source.matchAll(/from ['"]([^'"]+)['"]/g)].map(m => m[1])
  assert(imports.every(s => ['@hermes/plugin-sdk', 'react', 'react/jsx-runtime', 'react/jsx-dev-runtime'].includes(s)))
})

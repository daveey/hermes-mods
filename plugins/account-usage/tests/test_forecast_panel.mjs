// Forecast wiring in the status bar: request shape, verdict text, old-backend fallback. Loads the actual plugin source.
import { readFile } from 'node:fs/promises'
import assert from 'node:assert/strict'
import test from 'node:test'
const source = await readFile(new URL('../../../desktop-plugins/account-usage/plugin.js', import.meta.url), 'utf8')
function atom(value) { const l = new Set(); return { get: () => value, set(v) { value = v; l.forEach(f => f(v)) }, subscribe(f) { l.add(f); f(value); return () => l.delete(f) } } }
globalThis.__auTestHost = { state: { connectionId: atom('local'), profile: atom('default') } }
globalThis.__auTestAtom = atom
const plugin = await import('data:text/javascript;base64,' + Buffer.from('const host=globalThis.__auTestHost, atom=globalThis.__auTestAtom;\n' + source.replace(/^import .*$/gm, '')).toString('base64'))
const payload = { providers: [
  { slug: 'anthropic', usage: { accounts: [
    { id: 'A', label: 'a@x', state: 'ready', windows: [{ label: 'Current session', used_percent: 10 }, { label: 'Current week', used_percent: 40, resets_at: '2099-01-03T00:00:00Z' }] },
    { id: 'B', label: 'b@x', state: 'limited', limit_kind: 'quota', resets_at: '2099-01-02T00:00:00Z', windows: [] }] } },
  { slug: 'xai-oauth', usage: { windows: [{ label: 'API', used_percent: 0, scope: 'account' }] } }] }

test('forecast request carries weekly used + resets for pooled providers only', () => {
  const req = plugin.forecastRequest(plugin.summarize(payload))
  assert.deepEqual(req.providers.map(p => p.slug), ['anthropic'])
  assert.deepEqual(req.providers[0].accounts[0], { id: 'A', label: 'a@x', state: 'ready', disabled: false, week_used: 40, week_resets_at: '2099-01-03T00:00:00Z', resets_at: null })
  assert.equal(req.providers[0].accounts[1].week_used, null)
  assert.equal(req.providers[0].accounts[1].resets_at, '2099-01-02T00:00:00Z')
})

test('verdict and forecast wording', () => {
  assert.equal(plugin.verdictText({ verdict: 'short', runout_h: 17 }), 'dry in 17h')
  assert.equal(plugin.verdictText({ verdict: 'short', runout_h: 0 }), 'dry now')
  assert.equal(plugin.verdictText({ verdict: 'learning' }), 'learning…')
  assert.equal(plugin.verdictText({ verdict: 'ok' }), 'ok')
  assert.equal(plugin.verdictText(null), '')
  assert.match(plugin.forecastText({ verdict: 'learning', history_h: 1.25 }), /^learning… 1.3h of 2h history$/)
  assert.match(plugin.forecastText({ verdict: 'ok', pace: 0.42 }), /^lasts the week · 42% of supply$/)
  assert.match(plugin.forecastText({ verdict: 'short', gaps: [{ start_h: 3, end_h: 168 }] }, 0), /^out .+ → past 7d$/)
  assert.match(plugin.forecastText({ verdict: 'short', gaps: [{ start_h: 3, end_h: 5 }, { start_h: 70, end_h: 150 }] }, 0), /^out .+ · dry 3d of 7d$/)
  assert.equal(plugin.forecastText({ verdict: 'tight', pace: 0.71, burn_per_h: 4, curve: [90, 2, 100] }), 'tight · ~30m spare')
  assert.equal(plugin.forecastText({ verdict: 'tight', pace: 1.2, burn_per_h: 4, curve: [90, 50] }), 'tight · 120% of supply')
  assert.equal(plugin.forecastText(null), 'no forecast (backend too old)')
  assert.equal(plugin.leftText({ left: 110 }), '1.1 acct')
  assert.equal(plugin.fmtHours(0.25), '15m'); assert.equal(plugin.fmtHours(50), '2d')
})

test('attachForecast posts once and an old backend leaves forecast null', async () => {
  const groups = plugin.summarize(payload), calls = []
  const ok = await plugin.attachForecast({ rest: async (p, o) => { calls.push([p, o.method]); return { anthropic: { verdict: 'ok', curve: [100, 99] } } } }, groups)
  assert.deepEqual(calls, [['/forecast', 'POST']])
  assert.equal(ok[0].forecast.verdict, 'ok'); assert.equal(ok[1].forecast, null)
  const old = await plugin.attachForecast({ rest: async () => { throw new Error('404') } }, groups)
  assert.deepEqual(old.map(g => g.forecast), [null, null])
})
test('smoothCurve flattens the refill sawtooth but keeps the trend', () => {
  const saw = Array.from({ length: 48 }, (_, i) => 60 - i + (i % 4) * 8)
  const sm = plugin.smoothCurve(saw, 9)
  const jumps = c => c.slice(1).reduce((n, v, i) => n + (Math.abs(v - c[i]) > 6 ? 1 : 0), 0)
  assert.ok(jumps(saw) > 5); assert.equal(jumps(sm), 0)
  assert.ok(sm[0] > sm[47], 'still trends down')
  assert.deepEqual(plugin.smoothCurve([5, -3, 5], 1), [5, -3, 5])
  assert.match(plugin.smoothPath([[0, 5], [10, 2], [20, 8]], 10), /^M0\.0,5\.0 C.* C.*20\.0,8\.0$/)
})

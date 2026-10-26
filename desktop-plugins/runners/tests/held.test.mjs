import test from 'node:test'
import assert from 'node:assert/strict'
import { React, host, route, snapshot, fixtures, loadPlugin, mount, button, enrollment } from './sdk-mock.mjs'
const p = await loadPlugin()
const now = Date.now()
const iso = delta => new Date(now + delta).toISOString()
const evidence = (change = {}) => ({ kind: 'drained', source: 'systemd', state: 'held', freshness: 'fresh', observed_at: iso(-1000), expires_at: iso(60000), recorded_at: null, ...change })
function setup(items, change = {}) {
  const s = snapshot()
  // Keep the envelope on the same deterministic clock as projection/evidence.
  s.meta.observed_at = iso(-1000)
  s.meta.expires_at = iso(120000)
  Object.assign(s.data.runners[0], { registration: 'online', readiness: 'pass', activity: 'idle', operational_evidence: items }, change)
  return s
}
const project = (s, error = null) => p.projectFleet([{ route, snapshot: s, error }], now).rows[0]
test('current hold remains separate from offline, lifecycle and readiness failures', () => {
  const s = setup([evidence()], { registration: 'offline' })
  const row = project(s)
  assert.deepEqual(row.holds, ['Drained (intentional)'])
  assert.equal(row.health, 'Held')
  assert.equal(row.registration, 'offline')
  assert.match(row.reason, /provider: offline/)
  assert.equal(row.issue, true)
  assert.equal(row.ready, false)
  assert.notEqual(row.tone, 'bad')
  assert.equal(row.lifecycle, s.data.runners[0].lifecycle)
  const failed = project(setup([evidence()], { readiness: 'fail', activity: 'busy' }))
  assert.equal(failed.health, 'Held')
  assert.match(failed.reason, /readiness: fail/)
  assert.equal(failed.tone, 'bad')
  assert.equal(failed.activity, 'busy')
  const held = project(setup([evidence()]))
  assert.equal(held.health, 'Held')
  assert.equal(held.ready, false)
  assert.equal(held.issue, false)
  assert.equal(held.tone, 'muted')
})
for (const change of [
  { freshness: 'stale' }, { observed_at: iso(1000) }, { observed_at: null },
  { observed_at: 'invalid' }, { expires_at: null }, { expires_at: iso(-1) },
  { recorded_at: iso(1000) }, { recorded_at: 'invalid' }, { observed_at: 1 },
  { observed_at: '2026-01-01' }, { expires_at: iso(0) }, { source: 'unsupported' },
  { state: 'clear' }, { freshness: 'unavailable' }
]) test(`not current: ${JSON.stringify(change)}`, () => {
  const row = project(setup([evidence(change)]))
  assert.deepEqual(row.holds, [])
  if (change.freshness === 'unavailable') assert.equal(row.operational[0].status, 'Not observed')
  else if (change.state === 'clear') assert.equal(row.operational[0].status, 'Clear')
  else assert.equal(row.operational[0].status, 'Last-known / stale')
})
test('snapshot freshness and owner errors fence current holds', () => {
  const s = setup([evidence()])
  assert.deepEqual(project(s, 'Disconnected').holds, [])
  s.meta.freshness = 'stale'
  assert.deepEqual(project(s).holds, [])
  const expired = setup([evidence()])
  expired.meta.expires_at = iso(-1)
  assert.deepEqual(project(expired).holds, [])
  assert.deepEqual(project(setup([evidence()], { freshness: 'stale' })).holds, [])
})
test('unknown evidence is not observed and stale clear is not current', () => {
  for (const change of [{ state: 'unknown' }, { freshness: 'unknown' }]) {
    const row = project(setup([evidence(change)]))
    assert.deepEqual(row.holds, [])
    assert.equal(row.operational[0].status, 'Not observed')
  }
  assert.equal(project(setup([evidence({ state: 'clear', freshness: 'stale' })])).operational[0].status, 'Last-known / stale')
  for (const items of [null, {}, [null, { kind: 'unsupported' }]]) assert.deepEqual(project(setup(items)).operational, [])
})
test('each hold is informational, suppresses ready, and preserves input lifecycle', () => {
  for (const kind of ['drained', 'maintenance', 'disk_pressure', 'root_headroom']) {
    for (const lifecycle of ['persistent', 'ephemeral']) {
      const s = setup([evidence({ kind })], { lifecycle })
      const original = structuredClone(s)
      const row = project(s)
      assert.equal(row.health, 'Held')
      assert.equal(row.tone, 'muted')
      assert.equal(row.issue, false)
      assert.equal(row.ready, false)
      assert.equal(row.lifecycle, lifecycle)
      assert.deepEqual(s, original)
      assert.equal(p.selectRows([row], '', 'Ready').length, 0)
      if (kind === 'maintenance') assert.equal(p.selectRows([row], '', 'Maintenance').length, 1)
    }
  }
})
test('historical withdrawal never creates a hold; absent evidence stays compatible', () => {
  for (const recorded_at of [null, 'invalid', iso(1000)]) {
    const absent = project(setup([evidence({ kind: 'label_withdrawn', source: 'supervisor_log', state: 'unknown', freshness: 'unavailable', recorded_at })]))
    assert.equal(absent.operational[0].status, 'Not observed')
    assert.deepEqual(absent.holds, [])
  }
  const row = project(setup([evidence({ kind: 'label_withdrawn', source: 'supervisor_log', state: 'unknown', recorded_at: iso(-2000) })]))
  assert.deepEqual(row.holds, [])
  assert.equal(row.operational[0].label, 'Label withdrawn by supervisor (historical)')
  assert.equal(row.operational[0].timestamp, iso(-2000))
  const old = project(setup(undefined))
  assert.deepEqual(old.operational, [])
  assert.equal(old.health, 'Ready')
  assert.equal(old.ready, true)
})
test('all holds and historical provenance render alongside provider registration', async () => {
  const items = [evidence(), evidence({ kind: 'maintenance', source: 'maintenance_marker' }), evidence({ kind: 'disk_pressure', source: 'admission_journal' }), evidence({ kind: 'root_headroom', source: 'runner_journal' }), evidence({ kind: 'label_withdrawn', source: 'supervisor_log', state: 'unknown', recorded_at: iso(-2000) })]
  const s = setup(items, { registration: 'offline', activity: 'busy' })
  const d = { ...s, data: { ...fixtures.RunnerDetail, runner: s.data.runners[0] } }
  host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: { rest: async path => path.includes('/enrollment') ? enrollment() : path.includes('/runners/') ? d : s } }))
  try {
    await ui.click(button('Show runners'))
    await ui.click(document.querySelector('[data-node-row]'))
    const row = document.querySelector('[data-runner-row]')
    for (const label of ['Drained (intentional)', 'Maintenance', 'Withheld: disk pressure', 'Waiting for root headroom', 'Held', 'busy']) assert.ok(row.textContent.includes(label), label)
    await ui.click(row)
    const detail = document.querySelector('.hermes-runners-detail').textContent
    assert.match(detail, /Provider registrationoffline/)
    for (const item of items) { assert.ok(detail.includes(item.source)); assert.ok(detail.includes(item.kind === 'label_withdrawn' ? item.recorded_at : item.observed_at)) }
    assert.ok(detail.includes(`Expires: ${items[0].expires_at}`))
    assert.ok(detail.includes('Recorded: Not observed'))
    assert.ok(detail.includes('Label withdrawn by supervisor (historical)'))
  } finally { await ui.close() }
})

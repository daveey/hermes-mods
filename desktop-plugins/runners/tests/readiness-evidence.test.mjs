// Contract-faithful fixtures: shapes mirror the merged readiness_evidence
// contract (plugins/runners collector.readiness_evidence) and the live
// projection categories accepted on the owner. Identifiers are fictional.
import test from 'node:test'
import assert from 'node:assert/strict'
import { React, host, snapshot, fixtures, loadPlugin, route, mount, button, enrollment } from './sdk-mock.mjs'
const p = await loadPlugin()
const now = Date.now()
const iso = delta => new Date(now + delta).toISOString()
const hold = (change = {}) => ({ kind: 'drained', source: 'systemd', state: 'held', freshness: 'fresh', observed_at: iso(-1000), expires_at: iso(179000), recorded_at: null, ...change })
const causes = (change = {}) => ({ required: [], passed: [], failed: [], warned: [], hold_explained_failures: [], observed_at: iso(-1000), expires_at: iso(179000), freshness: 'fresh', ...change })
function setup(change = {}) {
  const s = snapshot()
  s.meta.observed_at = iso(-1000)
  s.meta.expires_at = iso(60000)
  s.data.hosts = [structuredClone(fixtures.Host)]
  Object.assign(s.data.runners[0], { host_id: s.data.hosts[0].id, registration: 'online', activity: 'idle', readiness: 'fail', operational_evidence: [] }, change)
  s.meta.provenance = [s.data.runners[0].id, s.data.hosts[0].resource_id].map(source_id => ({ source_id, state: 'fresh', observed_at: iso(-1000), expires_at: iso(60000), observation_ids: [] }))
  return s
}
const row = s => p.projectFleet([{ route, snapshot: s }], now).rows[0]

// Live category 1: intentional systemd drain. Stopped service and the drain
// check are both hold-explained; disk is an independent warning.
const drained = () => ({
  registration: 'offline', readiness: 'fail', operational_evidence: [hold()],
  readiness_evidence: causes({
    required: ['disk', 'service', 'tool:git', 'tool:python3', 'operational:drained:systemd'],
    passed: ['tool:git', 'tool:python3'], failed: ['service', 'operational:drained:systemd'],
    warned: ['disk'], hold_explained_failures: ['service', 'operational:drained:systemd']
  })
})
// Live category 2: independent tool failure, nothing explained.
const toolFail = () => ({
  readiness: 'fail',
  readiness_evidence: causes({ required: ['disk', 'service', 'tool:git', 'tool:xcode'], passed: ['disk', 'service', 'tool:git'], failed: ['tool:xcode'] })
})
// Live category 3: service failure under a maintenance hold. A maintenance
// hold never explains service, so this stays red.
const maintenanceServiceFail = () => ({
  readiness: 'fail',
  operational_evidence: [hold({ kind: 'maintenance', source: 'maintenance_marker' })],
  readiness_evidence: causes({
    required: ['disk', 'service', 'tool:git', 'operational:maintenance:maintenance_marker'],
    passed: ['disk', 'tool:git'], failed: ['service', 'operational:maintenance:maintenance_marker'],
    hold_explained_failures: ['operational:maintenance:maintenance_marker']
  })
})
// Live category 4: pressure source unreadable by the probe. The operational
// key is required but neither passed nor failed: not observed, not a failure.
const pressureNotObserved = () => ({
  readiness: 'warn',
  operational_evidence: [hold({ kind: 'disk_pressure', source: 'admission_journal', state: 'unknown', freshness: 'unavailable' })],
  readiness_evidence: causes({
    required: ['disk', 'service', 'tool:git', 'operational:disk_pressure:admission_journal'],
    passed: ['service', 'tool:git'], warned: ['disk']
  })
})

test('hold-only failure renders amber Held, never Failed', () => {
  const r = row(setup(drained()))
  assert.equal(r.bucket, 'held')
  assert.equal(r.bucketLabel, 'Held')
  assert.equal(r.tone, 'warn')
  assert.equal(r.ready, false)
  assert.match(r.reason, /Drained/)
  assert.match(r.reason, /warning: Disk/i)
  assert.doesNotMatch(r.reason, /failed/i)
  assert.deepEqual(r.checks.unexplained, [])
  assert.deepEqual(r.checks.explained, ['service', 'operational:drained:systemd'])
  assert.equal(p.selectRows([r], '', 'Failed').length, 0)
  assert.equal(p.selectRows([r], '', 'Held/maintenance').length, 1)
})
test('busy runner under a hold-only drain is Running · draining with its job eligibility', () => {
  const r = row(setup({ ...drained(), registration: 'online', activity: 'busy' }))
  assert.equal(r.bucket, 'running')
  assert.match(r.reason, /draining.*no new jobs/i)
})
test('independent tool failure stays red and names the failed check', () => {
  const r = row(setup(toolFail()))
  assert.equal(r.bucket, 'failed')
  assert.match(r.reason, /Tool xcode/)
  assert.doesNotMatch(r.reason, /causes not reported/)
  assert.equal(p.selectRows([r], '', 'Needs attention').length, 1)
})
test('service failure under a maintenance hold is not explained and stays red with the hold visible', () => {
  const r = row(setup(maintenanceServiceFail()))
  assert.equal(r.bucket, 'failed')
  assert.match(r.reason, /Failed: Service/)
  assert.match(r.reason, /Maintenance \(maintenance marker\)/)
  assert.deepEqual(r.checks.unexplained, ['service'])
  assert.equal(r.maintenance, true)
})
test('unobserved required keys are reported as not observed, not passed or failed', () => {
  const r = row(setup(pressureNotObserved()))
  assert.equal(r.bucket, 'held')
  assert.equal(r.bucketLabel, 'Degraded')
  assert.deepEqual(r.checks.notObserved, ['operational:disk_pressure:admission_journal'])
  assert.equal(r.ready, false)
})
test('explanations are trusted only with a matching current client-side hold', () => {
  // The hold the backend cited has expired on the client clock.
  const expired = drained(); expired.operational_evidence = [hold({ expires_at: iso(-1) })]
  assert.equal(row(setup(expired)).bucket, 'failed')
  // Service explanation without a drained/systemd hold present.
  const other = drained(); other.operational_evidence = [hold({ kind: 'maintenance', source: 'maintenance_marker' })]
  other.readiness_evidence.failed = ['service', 'operational:maintenance:maintenance_marker']
  other.readiness_evidence.hold_explained_failures = ['service', 'operational:maintenance:maintenance_marker']
  assert.equal(row(setup(other)).bucket, 'failed')
})
for (const [name, mutate] of [
  ['stale freshness', e => { e.freshness = 'stale' }],
  ['unavailable freshness', e => { e.freshness = 'unavailable' }],
  ['expired evidence', e => { e.expires_at = iso(-1) }],
  ['future observation', e => { e.observed_at = iso(5000) }],
  ['null observation', e => { e.observed_at = null }],
  ['explained not a subset of failed', e => { e.hold_explained_failures = ['service', 'operational:drained:systemd', 'disk'] }],
  ['failed not a subset of required', e => { e.required = ['disk'] }],
  ['non-array failed', e => { e.failed = 'service' }],
  ['non-string key', e => { e.failed = [{ key: 'service' }] }],
  ['empty failed under aggregate fail', e => { e.failed = []; e.hold_explained_failures = [] }]
]) test(`malformed or unusable causes fall back to conservative Failed: ${name}`, () => {
  const change = drained(); mutate(change.readiness_evidence)
  const r = row(setup(change))
  assert.equal(r.bucket, 'failed')
  assert.match(r.reason, /causes not reported/)
})
test('missing readiness_evidence keeps the aggregate failure and the hold visible', () => {
  const change = drained(); delete change.readiness_evidence
  const r = row(setup(change))
  assert.equal(r.bucket, 'failed')
  assert.match(r.reason, /causes not reported/)
  assert.match(r.reason, /Drained/)
})
test('evidence does not mutate the snapshot and never upgrades a stale runner', () => {
  const s = setup(drained()), copy = structuredClone(s)
  row(s)
  assert.deepEqual(s, copy)
  const stale = setup(drained()); stale.data.runners[0].freshness = 'stale'
  assert.equal(row(stale).bucket, 'unknown')
})
test('expanded detail lists independent, hold-explained, warning and not-observed checks', async () => {
  const s = setup(maintenanceServiceFail())
  s.data.runners[0].readiness_evidence.warned = ['disk']
  s.data.runners[0].readiness_evidence.passed = ['tool:git']
  s.data.runners[0].readiness_evidence.required.push('tool:xcode')
  const d = { ...s, data: { ...fixtures.RunnerDetail, runner: s.data.runners[0], jobs: [] } }
  host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: {
    rest: async path => path.includes('/enrollment') ? enrollment() : path.includes('/runners/') ? d : s
  } }))
  try {
    await ui.click(button('Show runners'))
    await ui.click(document.querySelector('[data-node-row]'))
    await ui.click(document.querySelector('[data-runner-row]'))
    const detail = document.querySelector('.hermes-runners-detail').textContent
    assert.match(detail, /Failed \(independent\): Service/)
    assert.match(detail, /Failed \(explained by current hold\): Maintenance \(maintenance marker\)/)
    assert.match(detail, /Warning: Disk/)
    assert.match(detail, /Not observed: Tool xcode/)
  } finally { await ui.close() }
})
test('API count parity: active host maintenance withholds ready and busy counts last-known activity', () => {
  const s = setup({ readiness: 'pass' }); delete s.data.runners[0].readiness_evidence
  s.data.hosts[0].maintenance = { state: 'active', reason: 'Planned', expires_at: iso(60000) }
  const v = p.projectFleet([{ route, snapshot: s }], now)
  assert.equal(v.rows[0].bucket, 'idle')
  assert.equal(v.rows[0].ready, false)
  assert.equal(v.counts.ready, 0)
  const stale = setup({ activity: 'busy', readiness: 'pass', freshness: 'stale' }); delete stale.data.runners[0].readiness_evidence
  const sv = p.projectFleet([{ route, snapshot: stale }], now)
  assert.equal(sv.rows[0].bucket, 'unknown')
  assert.equal(sv.counts.busy, 1)
})
test('chip issue count keeps owner API parity even when display classifies a row as hold-only', () => {
  const change = drained(); change.readiness_evidence.warned = []; change.readiness_evidence.passed.push('disk')
  const v = p.projectFleet([{ route, snapshot: setup(change) }], now)
  assert.equal(v.rows[0].bucket, 'held')
  assert.equal(p.selectRows(v.rows, '', 'Needs attention').length, 0)
  // Owner API counts readiness fail and offline registrations as issues.
  assert.equal(v.counts.issues, 1)
})
test('causes that contradict the owner aggregate are ignored in both directions', () => {
  for (const [readiness, change] of [['pass', { failed: ['service'] }], ['warn', { failed: ['service'] }], ['pass', { warned: ['disk'] }]]) {
    const r = row(setup({ readiness, readiness_evidence: causes({ required: ['disk', 'service'], passed: [], ...change }) }))
    assert.equal(r.checks.causes, false, `${readiness} ${JSON.stringify(change)}`)
    assert.deepEqual(r.checks.unexplained, [])
  }
})

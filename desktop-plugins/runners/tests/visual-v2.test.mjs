import test from 'node:test'
import assert from 'node:assert/strict'
import { React, host, route, snapshot, fixtures, loadPlugin, mount, button, enrollment } from './sdk-mock.mjs'
const p = await loadPlugin()
const now = Date.now()
const iso = delta => new Date(now + delta).toISOString()
const evidence = (change = {}) => ({ kind: 'drained', source: 'systemd', state: 'held', freshness: 'fresh', observed_at: iso(-1000), expires_at: iso(60000), recorded_at: null, ...change })
// Backend-faithful hold: a fresh held item is a failed required check that the
// owner reports as hold-explained (plugins/runners collector.readiness_evidence).
function faithful(kind = 'drained', source = 'systemd', change = {}) {
  const key = `operational:${kind}:${source}`
  const failed = kind === 'drained' ? ['service', key] : [key]
  return { readiness: 'fail', operational_evidence: [evidence({ kind, source })],
    readiness_evidence: { required: ['disk', 'service', key], passed: kind === 'drained' ? ['disk'] : ['disk', 'service'], failed, warned: [],
      hold_explained_failures: failed, observed_at: iso(-1000), expires_at: iso(60000), freshness: 'fresh' }, ...change }
}
function setup(change = {}) {
  const s = snapshot()
  s.meta.observed_at = iso(-1000)
  s.meta.expires_at = iso(60000)
  s.data.hosts = [structuredClone(fixtures.Host)]
  Object.assign(s.data.runners[0], { host_id: s.data.hosts[0].id, registration: 'online', activity: 'idle', readiness: 'not_checked', operational_evidence: [] }, change)
  s.meta.provenance = [s.data.runners[0].id, s.data.hosts[0].resource_id].map(source_id => ({ source_id, state: 'fresh', observed_at: iso(-1000), expires_at: iso(60000), observation_ids: [] }))
  return s
}
const source = s => ({ route, snapshot: s })
const row = (s, extra = {}) => p.projectFleet([{ ...source(s), ...extra }], now).rows[0]
for (const [name, change, bucket, label] of [
  ['busy', { activity: 'busy' }, 'running', 'Running'],
  ['idle with checks unknown', {}, 'idle', 'Idle'],
  ['offline false never idle', { registration: 'offline', activity: 'idle' }, 'unknown', 'Offline observation'],
  ['missing', { registration: 'missing' }, 'unknown', 'Missing'],
  ['unknown activity', { activity: 'unknown' }, 'unknown', 'Activity unknown'],
  ['disk hold', faithful('disk_pressure', 'admission_journal'), 'held', 'Held'],
  ['drained disabled', faithful('drained', 'systemd', { registration: 'offline' }), 'held', 'Held'],
  ['maintenance', faithful('maintenance', 'maintenance_marker'), 'held', 'Held'],
  ['historical label withdrawal is not a current hold', { operational_evidence: [evidence({ kind: 'label_withdrawn', source: 'supervisor_log', recorded_at: iso(-1000) })] }, 'idle', 'Idle'],
  ['check failure while held', { readiness: 'fail', operational_evidence: [evidence()] }, 'failed', 'Failed'],
  ['busy draining', faithful('drained', 'systemd', { activity: 'busy' }), 'running', 'Running'],
  ['busy offline conflict', { activity: 'busy', registration: 'offline', operational_evidence: [evidence()] }, 'unknown', 'Conflicting observations'],
  ['warning idle', { readiness: 'warn' }, 'held', 'Degraded'],
  ['expired hold', { operational_evidence: [evidence({ expires_at: iso(-1) })] }, 'unknown', 'Hold expired · awaiting evidence'],
  ['stale idle', { freshness: 'stale' }, 'unknown', 'Stale']
]) test(`display precedence: ${name}`, () => {
  const s = setup(change), original = structuredClone(s), r = row(s)
  assert.equal(r.bucket, bucket)
  assert.equal(r.bucketLabel, label)
  assert.deepEqual(s, original)
  if (bucket !== 'idle') assert.equal(r.ready, false)
})
test('freshness requires runner provenance, coverage and a connected owner', () => {
  for (const change of [{ observed_at: null }, { observed_at: 'bad' }, { observed_at: iso(1) }, { observed_at: iso(-120001) }, { expires_at: null }, { expires_at: iso(0) }, { state: 'unknown' }]) {
    const s = setup(); Object.assign(s.meta.provenance[0], change)
    const r = row(s)
    assert.equal(r.bucket, 'unknown', JSON.stringify(change))
    assert.equal(r.actionable, false)
    assert.equal(r.lastKnownActivity, 'idle')
  }
  const s = setup(); s.meta.provenance = []
  assert.equal(row(s).bucket, 'unknown')
  assert.equal(row(setup(), { error: 'Owner gateway disconnected' }).bucket, 'unknown')
  const partial = setup(); partial.meta.coverage[0].pagination_complete = false
  assert.equal(row(partial).bucket, 'unknown')
})
test('holds retain their independent clocks and host maintenance is not a blanket runner hold', () => {
  const s = setup({ operational_evidence: [evidence()] })
  s.meta.provenance[0].expires_at = iso(-1)
  const r = row(s)
  assert.equal(r.bucket, 'unknown')
  assert.equal(r.operational[0].held, true)
  assert.match(r.reason, /Drained/)
  const hostOnly = setup(); hostOnly.data.hosts[0].maintenance = { state: 'active', reason: 'Inspection', expires_at: iso(60000) }
  assert.equal(row(hostOnly).bucket, 'idle')
  assert.equal(row(hostOnly).maintenance, false)
  const checks = setup({ readiness: 'pass' }); checks.meta.provenance[1].expires_at = iso(-1)
  assert.equal(row(checks).ready, false)
  assert.match(row(checks).reason, /checks not reported/i)
})
test('running drain, failure and healthy maintenance remain orthogonal', () => {
  const busy = row(setup(faithful('drained', 'systemd', { activity: 'busy' })))
  assert.match(busy.reason, /draining.*no new jobs/i)
  const fail = row(setup({ readiness: 'fail', operational_evidence: [evidence()] }))
  assert.match(fail.reason, /failed/i); assert.match(fail.reason, /Drained/)
  assert.equal(p.selectRows([fail], '', 'Needs attention').length, 1)
  const held = row(setup(faithful('drained', 'systemd', { registration: 'offline' })))
  assert.equal(p.selectRows([held], '', 'Needs attention').length, 0)
  assert.equal(p.selectRows([held], '', 'Held/maintenance').length, 1)
})
test('ownership conflicts override buckets and current job eligibility', () => {
  const a = setup({ activity: 'busy' }), b = structuredClone(a)
  b.meta.owner.gateway_id = '00000000-0000-4000-8000-000000000002'
  const v = p.projectFleet([source(a), { route: { ...route, connectionId: 'other' }, snapshot: b }], now)
  assert.equal(v.rows.length, 1)
  assert.equal(v.rows[0].bucket, 'unknown')
  assert.equal(v.rows[0].actionable, false)
})
function cohort() {
  const s = setup(), base = s.data.runners[0]
  s.data.hosts.push({ ...structuredClone(s.data.hosts[0]), id: '00000000-0000-4000-8000-000000000003', display_name: 'Empty example node' })
  s.data.runners = Array.from({ length: 57 }, (_, i) => ({ ...base,
    id: `runner_${(i + 1).toString(16).padStart(16, '0')}`, provider_runner_id: String(i + 1), display_name: `Example runner ${i + 1}`,
    host_id: i < 12 ? base.host_id : null, activity: i === 0 ? 'busy' : i < 12 ? 'idle' : 'unknown',
    ...(i >= 4 && i < 12 ? faithful() : { operational_evidence: [] }) }))
  s.meta.provenance.push(...s.data.runners.map(r => ({ ...s.meta.provenance[0], source_id: r.id })))
  s.data.runners.push(structuredClone(s.data.runners[0]))
  return s
}
test('57 registration partition, empty hosts, unassigned and filtered denominators', () => {
  const s = cohort(), sources = [source(s)], v = p.projectFleet(sources, now)
  assert.equal(v.rows.length, 57)
  assert.deepEqual(p.bucketCounts(v.rows), { running: 1, idle: 3, held: 8, failed: 0, unknown: 45 })
  const nodes = p.projectNodes(sources, v.rows, now)
  assert.equal(nodes.nodes.length, 2); assert.equal(nodes.unassigned.length, 45)
  assert.deepEqual(nodes.nodes[0].counts, { running: 1, idle: 3, held: 8, failed: 0, unknown: 0 })
  const filtered = p.selectNodes(nodes, '', 'Idle')
  assert.equal(filtered.rows.length, 3)
  assert.equal(filtered.groups[0].registrationCount, 3)
  assert.equal(filtered.groups[0].counts.idle, 3)
  const mixed = setup(); mixed.data.runners.push({ ...mixed.data.runners[0], id: 'runner_0000000000000099', provider_runner_id: '99' })
  const mn = p.projectNodes([source(mixed)], p.projectFleet([source(mixed)], now).rows, now).nodes[0]
  assert.equal(mn.counts.idle, 1); assert.equal(mn.counts.unknown, 1)
  assert.equal(mn.activity.idle, 1)
})
test('native state select, labeled bars, filtered cohort and progressive telemetry', async () => {
  const s = cohort(); host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: { rest: async path => path.includes('/enrollment') ? enrollment() : s } }))
  try {
    await ui.click(button('Show runners'))
    const select = document.querySelector('select[aria-label="Runner state"]')
    assert.ok(select)
    assert.deepEqual([...select.options].map(o => o.textContent), ['All', 'Needs attention', 'Running (Busy)', 'Idle', 'Held/maintenance', 'Failed', 'Unknown/stale', 'Checks passed + idle (Ready)'])
    assert.equal(document.querySelectorAll('[data-runner-row]').length, 0)
    assert.match(document.querySelector('[data-node-row="unmapped"]').textContent, /45.*registrations/)
    assert.match(document.body.textContent, /No physical node mapping/)
    assert.equal(document.querySelectorAll('[data-registration-bar]').length, 2)
    assert.match(document.body.textContent, /45 unknown/i)
    assert.doesNotMatch(document.body.textContent, /CPU.*not reported/)
    const input = document.querySelector('input[aria-label="Search runners"]')
    await React.act(async () => { select.value = 'Idle'; select.dispatchEvent(new window.Event('change', { bubbles: true })) })
    await ui.flush()
    assert.equal(document.querySelector('input[aria-label="Search runners"]'), input)
    assert.match(document.body.textContent, /57 registrations · 3 shown/)
    const bar = document.querySelector('[data-registration-bar]')
    assert.equal(bar.getAttribute('aria-label'), '3 registrations: 0 running, 3 idle, 0 held/degraded, 0 failed, 0 unknown')
    await ui.click(document.querySelector('[data-node-row]'))
    assert.match(document.body.textContent, /CPU: not reported.*Memory: not reported/)
    assert.equal(document.querySelectorAll('[data-runner-row]').length, 3)
  } finally { await ui.close() }
})
test('stale activity still permits lazy read-only evidence, never a current job', async () => {
  const s = setup({ activity: 'busy' }); s.meta.provenance[0].expires_at = iso(-1)
  const calls = []
  host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: { rest: async path => {
    calls.push(path)
    return path.includes('/enrollment') ? enrollment() : path.includes('/runners/') ? { ...s, data: { ...fixtures.RunnerDetail, jobs: [], observed_jobs: [], job_monitoring: { state: 'forbidden' } } } : s
  } } }))
  try {
    await ui.click(button('Show runners'))
    await ui.click(document.querySelector('[data-node-row]'))
    assert.equal(calls.filter(c => c.includes('/runners/')).length, 0)
    await ui.click(document.querySelector('[data-runner-row]'))
    assert.equal(calls.filter(c => c.includes('/runners/')).length, 1)
    assert.match(document.querySelector('.hermes-runners-detail').textContent, /Job details permission denied/)
    assert.equal(button('Open current job'), undefined)
  } finally { await ui.close() }
})
test('fresh current job is lazy, owner-routed and guarded by row evidence', async () => {
  const s = setup({ activity: 'busy' }), r = row(s)
  const job = { repository: s.data.scopes[0].name, runner_id: r.provider_runner_id, run_id: '123', job_id: '456', status: 'in_progress', observed_at: iso(-1000), expires_at: iso(60000), url: `https://github.com/${s.data.scopes[0].name}/actions/runs/123/job/456` }
  assert.equal(p.observedJobURL(r, job, now), job.url)
  assert.equal(p.observedJobURL({ ...r, actionable: false }, job, now), null)
  assert.equal(p.observedJobURL(r, { ...job, runner_id: '999' }, now), null)
  const calls = [], opened = []
  host.profileRoutes = async () => [route]
  const ui = await mount(React.createElement(p.Runners, { ctx: { os: { openExternal: url => opened.push(url) }, rest: async (path, options) => {
    calls.push([path, options])
    return path.includes('/enrollment') ? enrollment() : path.includes('/runners/') ? { ...s, data: { ...fixtures.RunnerDetail, jobs: [], observed_jobs: [job] } } : s
  } } }))
  try {
    await ui.click(button('Show runners'))
    await ui.click(document.querySelector('[data-node-row]'))
    assert.equal(button('Open current job 456'), undefined)
    await ui.click(document.querySelector('[data-runner-row]'))
    const detailCalls = calls.filter(([path]) => path.includes('/runners/'))
    assert.equal(detailCalls.length, 1)
    assert.equal(detailCalls[0][1].connectionId, route.connectionId)
    await ui.click(button('Open current job 456'))
    assert.deepEqual(opened, [job.url])
  } finally { await ui.close() }
})
test('degradation filter, mixed ages, invalid identities and future host evidence stay honest', () => {
  const s = setup({ readiness: 'warn' }), r = row(s)
  assert.equal(p.selectRows([r], '', 'Held/maintenance').length, 1)
  assert.equal(p.selectRows([r], '', 'Needs attention').length, 1)
  const invalid = setup(); invalid.data.runners[0].provider_runner_id = 'not-an-id'
  assert.equal(row(invalid).bucket, 'unknown')
  s.meta.provenance[1].observed_at = iso(1)
  assert.notEqual(p.projectNodes([source(s)], [], now).nodes[0].freshness.state, 'fresh')
  const olderHold = setup({ operational_evidence: [evidence({ observed_at: iso(-30000) })] })
  assert.equal(p.projectNodes([source(olderHold)], [row(olderHold)], now).nodes[0].age, 'Mixed ages')
})
test('review: conflicting owners cannot populate the held filter', () => {
  const a = setup({ operational_evidence: [evidence()] }), b = structuredClone(a)
  b.meta.owner.gateway_id = '00000000-0000-4000-8000-000000000002'
  const v = p.projectFleet([source(a), { route: { ...route, connectionId: 'other' }, snapshot: b }], now)
  assert.equal(p.selectRows(v.rows, '', 'Held/maintenance').length, 0)
  assert.deepEqual(v.rows[0].holds, [])
})
test('review: filtered cohort age respects injected time', () => {
  const s = setup(), sources = [source(s)], v = p.projectFleet(sources, now)
  const nodes = p.projectNodes(sources, v.rows, now)
  assert.equal(p.selectNodes(nodes, '', 'Idle', now + 30000).groups[0].age, '31s ago')
})
test('landed aggregate contract: fresh hold contributes readiness failure until causes are exposed', () => {
  const r = row(setup({ readiness: 'fail', operational_evidence: [evidence()] }))
  assert.equal(r.bucket, 'failed')
  assert.match(r.reason, /individual check causes not reported/)
})

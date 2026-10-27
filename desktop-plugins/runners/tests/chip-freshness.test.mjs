import test from 'node:test'
import assert from 'node:assert/strict'
import { snapshot, fixtures, loadPlugin, route } from './sdk-mock.mjs'
const p = await loadPlugin()

test('chip and panel share current buckets, excluding holds and last-known activity', () => {
  const now = Date.now(), iso = delta => new Date(now + delta).toISOString()
  const s = snapshot(), base = s.data.runners[0]
  s.data.hosts = [structuredClone(fixtures.Host)]
  const hold = { kind: 'drained', source: 'systemd', state: 'held', freshness: 'fresh', observed_at: iso(-1000), expires_at: iso(60000), recorded_at: null }
  const cases = [
    { activity: 'busy' }, { activity: 'idle' },
    { activity: 'busy', freshness: 'stale' },
    { activity: 'idle', freshness: 'stale' },
    { activity: 'busy', registration: 'offline' },
    { activity: 'idle', operational_evidence: [hold] },
    { activity: 'busy', operational_evidence: [hold] },
    { activity: 'busy', readiness: 'fail' },
    { activity: 'idle', readiness: 'warn' }
  ]
  s.data.runners = cases.map((change, i) => ({ ...base, id: `runner_${String(i + 1).padStart(16, '0')}`, provider_runner_id: String(i + 1), host_id: i === 1 ? null : s.data.hosts[0].id, readiness: 'not_checked', ...change }))
  s.meta.observed_at = iso(-1000); s.meta.expires_at = iso(60000)
  s.meta.provenance = [...s.data.runners.map(r => r.id), s.data.hosts[0].resource_id].map(source_id => ({ source_id, observed_at: iso(-1000), expires_at: iso(60000), state: 'fresh', observation_ids: [] }))
  const sources = [{ route, snapshot: s }]
  const v = p.projectFleet(sources, now), panel = p.projectNodes(sources, v.rows, now)
  const panelRows = [...panel.nodes.flatMap(n => n.runners), ...panel.unassigned]
  const buckets = p.bucketCounts(panelRows)
  assert.deepEqual(buckets, { running: 2, idle: 1, held: 2, failed: 1, unknown: 3 })
  const draining = panelRows.filter(r => r.bucket === 'running' && r.holds.length).length
  assert.equal(draining, 1)
  assert.equal(v.counts.busy, buckets.running - draining)
  assert.equal(v.counts.online, buckets.running + buckets.idle - draining)
  assert.equal(v.counts.busy, 1)
  assert.equal(v.counts.online, 2)
  assert.match(p.chipTooltip(v), /held.*excluded/i)
  // The client clock and disconnected owner must invalidate both surfaces.
  for (const [input, clock] of [[sources, now + 60001], [[{ ...sources[0], error: 'Disconnected' }], now]]) {
    const expired = p.projectFleet(input, clock)
    const nodes = p.projectNodes(input, expired.rows, clock)
    assert.equal(p.bucketCounts([...nodes.nodes.flatMap(n => n.runners), ...nodes.unassigned]).running, 0)
    assert.equal(expired.counts.online, 0)
    assert.equal(expired.counts.busy, 0)
  }
})

import test from 'node:test'
import assert from 'node:assert/strict'
import { React, host, route, snapshot, fixtures, loadPlugin, mount, button, enrollment } from './sdk-mock.mjs'
const p = await loadPlugin()
test('node projection includes empty hosts and keeps unassigned registrations separate', () => {
  const s = snapshot()
  s.data.hosts = [structuredClone(fixtures.Host), { ...structuredClone(fixtures.Host), id: 'host_0000000000000002', display_name: 'Empty node' }]
  s.data.runners[0].host_id = s.data.hosts[0].id
  s.data.runners.push({ ...s.data.runners[0], id: 'runner_0000000000000002', provider_runner_id: '2', host_id: null })
  const sources = [source(s)]
  const v = p.projectNodes(sources, p.projectFleet(sources).rows)
  assert.equal(v.nodes.length, 2)
  assert.deepEqual(v.nodes.map(n => n.registrationCount), [1, 0])
  assert.equal(v.unassigned.length, 1)
  assert.equal(v.nodes[0].runners[0].source.route, route)
  assert.equal(v.nodes[1].activity.busy, null)
  assert.equal(v.nodes[1].freshness.observedAt, null)
})
test('node health never turns management reachability or busy activity into verified health', () => {
  const s = snapshot()
  s.data.hosts = [structuredClone(fixtures.Host)]
  s.data.runners[0].host_id = s.data.hosts[0].id
  s.data.runners[0].activity = 'busy'
  const sources = [source(s)]
  let n = p.projectNodes(sources, p.projectFleet(sources).rows).nodes[0]
  assert.equal(n.health.state, 'unknown')
  assert.match(n.health.reason, /Reachable.*health unverified/)
  assert.equal(n.freshness.observedAt, null)
  s.data.hosts[0].management = 'unreachable'
  n = p.projectNodes(sources, p.projectFleet(sources).rows).nodes[0]
  assert.match(n.health.reason, /not proof of.*outage/)
  assert.equal(n.health.state, 'unknown')
  assert.equal(n.monitoring, 'unreachable')
})
test('status chip cannot wrap or shrink below its reduced label', () => {
  assert.match(p.CSS, /\[data-slot=runners-chip\]\{flex-shrink:0;white-space:nowrap\}/)
})
test('same-owner duplicates choose newest usable evidence independent of route ordering', () => {
  const a = snapshot(),
    b = snapshot()
  a.meta.observed_at = new Date(Date.now() - 50000).toISOString()
  b.data.runners[0].activity = 'busy'
  for (const list of [
    [source(a), source(b, { route: { ...route, connectionId: 'second' } })],
    [source(b, { route: { ...route, connectionId: 'second' } }), source(a)]
  ]) {
    const v = p.projectFleet(list)
    assert.equal(v.counts.ready, 0)
    assert.equal(v.counts.busy, 1)
    assert.equal(v.rows[0].source.route.connectionId, 'second')
  }
  const v = p.projectFleet([
    source(a, { error: 'Owner gateway disconnected' }),
    source(b, { route: { ...route, connectionId: 'second' } })
  ])
  assert.equal(v.rows[0].actionable, true)
  assert.equal(v.rows[0].source.route.connectionId, 'second')
})
test('host warnings affect severity and attention without repeating per registration', () => {
  const s = snapshot()
  s.data.hosts = [{ ...fixtures.Host, management: 'unreachable' }]
  s.data.runners[0].host_id = fixtures.Host.id
  s.data.runners.push({
    ...s.data.runners[0],
    id: 'runner_0000000000000002',
    provider_runner_id: '2'
  })
  const v = p.projectFleet([source(s)])
  assert.equal(v.tone, 'warn')
  assert.equal(v.counts.issues, 1)
  assert.equal(p.selectRows(v.rows, '', 'Needs attention').length, 2)
})
const source = (s = snapshot(), extra = {}) => ({
  route,
  snapshot: s,
  ...extra
})
test('registration counts never imply slots; busy is not unhealthy', () => {
  const s = snapshot()
  s.data.runners[0].activity = 'busy'
  const v = p.projectFleet([source(s)])
  assert.equal(v.counts.online, 1)
  assert.equal(v.counts.busy, 1)
  assert.equal(v.counts.unverified, 1)
  assert.equal(v.counts.ready, 0)
  assert.equal(v.counts.issues, 0)
  assert.match(p.chipLabel(v), /1 online · 1 busy/)
  assert.match(p.chipTooltip(v), /registrations.*not.*slots/i)
})
test('row observation age never borrows the collector heartbeat', () => {
  const s = snapshot()
  const stamp = new Date(Date.now() - 600000).toISOString()
  s.meta.provenance = [{ source_id: s.data.runners[0].id, observed_at: stamp, state: 'stale' }]
  const v = p.projectFleet([source(s)])
  assert.equal(v.rows[0].observed_at, stamp)
  assert.equal(v.rows[0].stale, true)
  assert.equal(v.counts.ready, 0)
  s.meta.provenance = []
  assert.equal(p.projectFleet([source(s)]).rows[0].observed_at, null)
})
test('expired snapshot cannot stay Ready, even if server said fresh', () => {
  const s = snapshot()
  s.meta.expires_at = new Date(Date.now() - 1).toISOString()
  const v = p.projectFleet([source(s)])
  assert.equal(v.counts.ready, 0)
  assert.notEqual(v.tone, 'good')
  assert.match(v.rows[0].health, /stale/i)
})
test('duplicate provider identity deduplicates across scope handles and conflicting owners disable actions', () => {
  const a = snapshot(),
    b = snapshot()
  b.meta.owner.gateway_id = '00000000-0000-4000-8000-000000000002'
  b.data.scopes[0].id = 'scope_0000000000000002'
  b.data.runners[0].scope_id = b.data.scopes[0].id
  const v = p.projectFleet([source(a), source(b, { route: { ...route, connectionId: 'second' } })])
  assert.equal(v.rows.length, 1)
  assert.equal(v.rows[0].sources.length, 2)
  assert.equal(v.rows[0].conflict, true)
  assert.equal(v.rows[0].actionable, false)
  assert.notEqual(v.tone, 'good')
})
test('host grouping capacity and maintenance do not conflate registrations with machines', () => {
  const s = snapshot()
  s.data.hosts = [fixtures.Host]
  s.data.hosts[0].maintenance = {
    state: 'active',
    expires_at: '2099-01-01T00:00:00Z',
    reason: 'Scheduled'
  }
  s.data.runners[0].host_id = fixtures.Host.id
  s.data.runners.push({
    ...s.data.runners[0],
    id: 'runner_0000000000000002',
    provider_runner_id: '2'
  })
  const v = p.projectFleet([source(s)])
  assert.equal(v.groups.length, 1)
  assert.equal(v.counts.issues, 0)
  assert.match(v.groups[0].capacity, /unknown/i)
  assert.equal(p.selectRows(v.rows, '', 'Maintenance').length, 2)
})
test('unmapped host, search all metadata, stable sort and long untrusted names', () => {
  const s = snapshot()
  s.data.runners[0].display_name = '<img onerror=alert(1)>'.repeat(30)
  s.data.runners[0].labels = ['xcode']
  const v = p.projectFleet([source(s)])
  assert.equal(v.groups[0].name, 'Host not mapped')
  assert.equal(p.selectRows(v.rows, 'example/project xcode', 'All').length, 1)
  assert.deepEqual(p.selectRows(v.rows, '', 'All'), p.selectRows([...v.rows].reverse(), '', 'All'))
})
for (const [state, label] of [
  ['not_configured', 'Not configured'],
  ['ready', 'No registrations'],
  ['owner_disconnected', 'Owner gateway disconnected'],
  ['plugin_unavailable', 'Plugin unavailable'],
  ['owner_conflict', 'Ownership conflict'],
  ['partial', 'Partial permissions'],
  ['provider_unavailable', 'Provider outage'],
  ['configuration_error', 'Configuration error']
])
  test(`explicit ${state} state`, () => {
    const s = snapshot()
    s.data.state = state
    s.data.runners = []
    assert.match(p.projectFleet([source(s)]).messages.join(' '), new RegExp(label, 'i'))
  })
test('source error once, host management warning once, disconnected cached rows remain stale', () => {
  const s = snapshot()
  s.meta.source_errors = [
    {
      source_id: fixtures.Scope.id,
      code: 'partial_permissions',
      retry_after_seconds: null,
      observed_at: s.meta.observed_at
    }
  ]
  s.data.hosts = [{ ...fixtures.Host, management: 'unreachable' }]
  s.data.runners[0].host_id = fixtures.Host.id
  let v = p.projectFleet([source(s)])
  assert.equal(v.messages.filter((x) => x.includes('partial_permissions')).length, 1)
  assert.match(v.groups[0].issue, /unavailable/i)
  v = p.projectFleet([source(s, { error: 'Owner gateway disconnected' })])
  assert.equal(v.rows.length, 1)
  assert.equal(v.rows[0].actionable, false)
  assert.equal(v.counts.ready, 0)
})
test('source cooldown ETA is aged from its observation, not reset on render', () => {
  const s = snapshot()
  const now = Date.now()
  s.meta.source_errors = [{ source_id: fixtures.Scope.id, code: 'forbidden',
    retry_after_seconds: 60, observed_at: new Date(now - 20000).toISOString() }]
  assert.match(p.projectFleet([source(s)], now).messages.join(' '), /retry in 40s/)
  assert.doesNotMatch(p.projectFleet([source(s)], now + 61000).messages.join(' '), /retry in/)
})
test('polling pauses hidden and uses 15/60 second cadence', () => {
  assert.equal(p.pollInterval(true, true), 15000)
  assert.equal(p.pollInterval(false, true), 60000)
  assert.equal(p.pollInterval(true, false), false)
})
test('render 100 registrations with no per-row request amplification, native popover and details on demand', async () => {
  const s = snapshot()
  s.data.runners = Array.from({ length: 100 }, (_, i) => ({
    ...s.data.runners[0],
    id: `runner_${i.toString(16).padStart(16, '0')}`,
    provider_runner_id: String(i),
    display_name: `Worker ${i}`
  }))
  const calls = []
  host.profileRoutes = async () => [route]
  const ctx = {
    rest: async (path, opts) => {
      calls.push([path, opts])
      if (path.includes('/enrollment')) return enrollment()
      if (path.includes('/runners/'))
        return {
          ...s,
          data: { ...fixtures.RunnerDetail, runner: s.data.runners[0] }
        }
      return path.includes('/refresh') ? { ...s, data: fixtures.Collection } : s
    }
  }
  const ui = await mount(React.createElement(p.Runners, { ctx }))
  try {
    assert.ok(button('Show runners'))
    await ui.click(button('Show runners'))
    await ui.click(document.querySelector('[data-node-row]'))
    assert.equal(document.querySelectorAll('[data-runner-row]').length, 100)
    assert.match(document.body.textContent, /Collector heartbeat/)
    assert.equal(calls.filter(([x]) => x.includes('/inventory')).length, 1)
    assert.equal(calls.filter(([x]) => x.includes('/runners/')).length, 0)
    assert.equal(calls.filter(([x]) => x.includes('/refresh')).length, 1)
    assert.equal(document.querySelector('[data-runners-panel]').getAttribute('data-side'), 'top')
    await ui.click(document.querySelector('[data-runner-row]'))
    assert.equal(calls.filter(([x]) => x.includes('/runners/')).length, 1)
    assert.match(document.body.textContent, /Source observations/)
    assert.equal(document.querySelectorAll('img').length, 0)
  } finally {
    await ui.close()
  }
})
test('keyboard slash, arrows, Enter, Escape details then popover and focus restoration', async () => {
  host.profileRoutes = async () => [route]
  const s = snapshot()
  const ctx = {
    rest: async (path) => path.includes('/enrollment') ? enrollment() : (path.includes('/runners/') ? { ...s, data: fixtures.RunnerDetail } : s)
  }
  const ui = await mount(React.createElement(p.Runners, { ctx }))
  try {
    await ui.click(button('Show runners'))
    await ui.click(document.querySelector('[data-node-row]'))
    await ui.key(document.querySelector('[data-runners-panel]'), '/')
    assert.equal(document.activeElement.getAttribute('aria-label'), 'Search runners')
    await ui.key(document.activeElement, 'ArrowDown')
    assert.ok(document.activeElement.hasAttribute('data-node-row'))
    await ui.key(document.activeElement, 'ArrowDown')
    assert.ok(document.activeElement.hasAttribute('data-runner-row'))
    await ui.key(document.activeElement, 'Enter')
    assert.match(document.body.textContent, /Source observations/)
    await ui.key(document.activeElement, 'Escape')
    assert.doesNotMatch(document.body.textContent, /Source observations/)
    await ui.key(document.querySelector('[data-runners-panel]'), 'Escape')
    assert.equal(document.querySelector('[data-node-row]').getAttribute('aria-expanded'), 'false')
    await ui.key(document.activeElement, 'Escape')
    assert.equal(document.querySelector('[data-runners-panel]'), null)
    assert.equal(document.activeElement, button('Show runners'))
  } finally {
    await ui.close()
  }
})

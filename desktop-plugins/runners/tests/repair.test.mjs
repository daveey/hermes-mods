import test from 'node:test'
import assert from 'node:assert/strict'
import { React, host, route, snapshot, fixtures, loadPlugin, mount, button, enrollment } from './sdk-mock.mjs'
const p = await loadPlugin()
const oid = prefix => `${prefix}_${'1'.repeat(16)}`
function setup() {
  const s = snapshot(), source = { route: { ...route }, snapshot: s }
  s.data.hosts = [structuredClone(fixtures.Host)]
  s.data.runners[0].host_id = s.data.hosts[0].id
  const row = p.projectFleet([source]).rows[0]
  const incident = { ...fixtures.Incident, id: oid('incident'), entity_id: row.id, resolved_at: null }
  const repair = { ...structuredClone(fixtures.Repair), entity_id: row.id, incident_id: incident.id,
    owner: s.meta.owner, session: { ...fixtures.SessionAssociation, start_request_id: oid('start') } }
  const calls = [], opens = [], rpc = []
  let available = [source.route]
  const result = { gateway_id: s.meta.owner.gateway_id, profile: route.targetProfile,
    stored_session_id: 'same_session', resolved_session_id: 'compressed_tip', exists: true,
    first_turn_state: 'running', reconciliation: 'running' }
  const response = { meta: s.meta, data: { repair, owner_route: null, route_status: 'unresolved' } }
  const ctx = { rest: async (path, options) => {
    calls.push({ path, options })
    if (path.includes('/enrollment')) return enrollment()
    if (path.includes('/runners/')) return { meta: s.meta, data: { ...fixtures.RunnerDetail, incidents: [incident], repairs: [] } }
    if (path.includes('/repairs/') && options?.method !== 'POST') return { ...response, data: {
      ...response.data, repair: { ...repair, session: { ...repair.session,
        stored_session_id: result.stored_session_id, lineage_tip_id: result.resolved_session_id, state: 'running' } } } }
    if (path.includes('/repairs')) return options?.method === 'POST' ? response : { meta: s.meta, data: [] }
    if (path.includes('/maintenance') && options?.method !== 'POST') return { meta: s.meta, data: { revision: s.meta.owner.config_revision } }
    return s
  } }
  const ownerHost = { profileRoutes: async () => available,
    requestProfile: async (...args) => { rpc.push(args); return result },
    openSession: async (...args) => { opens.push(args) } }
  return { s, row, source, incident, repair, response, ctx, ownerHost, calls, opens, rpc, result,
    disconnect: () => { available = [] } }
}
test('repair POST uses frozen contract and explicit owner RPC/open route; dead host is irrelevant', async () => {
  const f = setup()
  f.row.host.management = 'unreachable'
  await p.createTransport(f.ctx, f.ownerHost).repair(f.row)
  const post = f.calls.find(c => c.options?.method === 'POST')
  assert.equal(post.path, '/v1/repairs?profile=example')
  assert.deepEqual(Object.keys(post.options.body).sort(), ['entity_id', 'expected_owner', 'idempotency_key', 'incident_id', 'mode'])
  assert.equal(post.options.body.entity_id, f.row.id)
  assert.equal(post.options.body.incident_id, f.incident.id)
  assert.equal(post.options.connectionId, route.connectionId)
  assert.deepEqual(f.rpc[0].slice(0, 3), [route, 'session.start_request', { namespace: 'runners', profile: 'example', start_request_id: f.repair.session.start_request_id }])
  assert.deepEqual(f.opens, [['compressed_tip', { route }]])
})
test('concurrent clicks coalesce and repeat action reopens backend-reserved chat', async () => {
  const f = setup(), transport = p.createTransport(f.ctx, f.ownerHost)
  await Promise.all([transport.repair(f.row), transport.repair(f.row)])
  assert.equal(f.calls.filter(c => c.options?.method === 'POST').length, 1)
  await transport.repair(f.row)
  assert.deepEqual(f.opens, [['compressed_tip', { route }], ['compressed_tip', { route }]])
})
test('lost start response reconciles without resending start or a prompt, including later retry', async () => {
  const f = setup(); let statusFails = true
  const rest = f.ctx.rest
  f.ctx.rest = async (...args) => {
    if (args[0].includes('/repairs/') && statusFails) throw Error('lost status response')
    return rest(...args)
  }
  f.ownerHost.requestProfile = async (...args) => {
    f.rpc.push(args)
    if (args[1] === 'session.start_request' || statusFails) throw Error('lost response')
    return f.result
  }
  const transport = p.createTransport(f.ctx, f.ownerHost)
  await assert.rejects(transport.repair(f.row), /outcome|reconcil/i)
  statusFails = false
  await transport.repair(f.row)
  assert.deepEqual(f.rpc.map(c => c[1]), ['session.start_request'])
  assert.ok(f.calls.some(c => c.path.includes('/repairs/')))
  assert.equal(f.opens.length, 1)
})
test('profile switch during POST cannot retarget owner; same session IDs stay gateway-scoped', async () => {
  const f = setup(), old = { ...route }
  const rest = f.ctx.rest
  f.ctx.rest = async (...args) => { const r = await rest(...args); f.row.source.route.profile = 'switched'; return r }
  f.ownerHost.profileRoutes = async () => [old]
  await p.createTransport(f.ctx, f.ownerHost).repair(f.row)
  assert.deepEqual(f.opens[0][1].route, old)
  const b = setup()
  b.source.route.connectionId = 'second'
  b.row.source.route.connectionId = 'second'
  b.s.meta.owner.gateway_id = '22222222-2222-4222-8222-222222222222'
  b.result.gateway_id = b.s.meta.owner.gateway_id
  await p.createTransport(b.ctx, b.ownerHost).repair(b.row)
  assert.equal(b.opens[0][0], f.opens[0][0])
  assert.equal(b.opens[0][1].route.connectionId, 'second')
})
test('disconnected, conflicting, missing incident and wrong-owner RPC fail closed', async () => {
  for (const kind of ['disconnect', 'conflict', 'incident', 'identity', 'removed']) {
    const f = setup()
    if (kind === 'disconnect') f.disconnect()
    if (kind === 'conflict') f.row.actionable = false
    if (kind === 'incident') f.incident.resolved_at = new Date().toISOString()
    if (kind === 'identity') f.result.profile = 'wrong'
    if (kind === 'removed') f.ownerHost.requestProfile = async () => { f.disconnect(); return f.result }
    await assert.rejects(p.createTransport(f.ctx, f.ownerHost).repair(f.row))
    assert.equal(f.opens.length, 0)
    if (['disconnect', 'conflict', 'incident'].includes(kind)) assert.equal(f.rpc.length, 0)
  }
})
test('core-authority RPC result opens only after owner status binds the same session', async () => {
  // Live core keys StartOwner by its install authority, never the runners UUID.
  const f = setup()
  f.result.gateway_id = 'install:core-authority'
  await p.createTransport(f.ctx, f.ownerHost).repair(f.row)
  assert.deepEqual(f.opens, [['compressed_tip', { route }]])
  const g = setup()
  g.result.gateway_id = 'install:core-authority'
  const owned = g.result.stored_session_id
  const rest = g.ctx.rest
  g.ctx.rest = async (...args) => {
    const value = await rest(...args)
    if (args[0].includes('/repairs/') && args[1]?.method !== 'POST')
      value.data.repair.session.stored_session_id = owned
    return value
  }
  g.result.stored_session_id = 'unrelated_session'
  await assert.rejects(p.createTransport(g.ctx, g.ownerHost).repair(g.row), /identity mismatch/)
  assert.equal(g.opens.length, 0)
})
test('existing repair opens via status without scheduling another start', async () => {
  const f = setup(), rest = f.ctx.rest
  f.ctx.rest = async (...args) => {
    const value = await rest(...args)
    if (args[0].includes('/runners/')) value.data.repairs = [f.repair]
    return value
  }
  await p.createTransport(f.ctx, f.ownerHost).repair(f.row)
  assert.equal(f.calls.filter(c => c.options?.method === 'POST').length, 0)
  assert.equal(f.rpc.length, 0)
  assert.ok(f.calls.some(c => c.path.includes('/repairs/')))
})
test('host-targeted repair reopens from mapped runner without new reservation', async () => {
  const f = setup(), rest = f.ctx.rest
  f.repair.entity_id = f.row.host.resource_id
  f.ctx.rest = async (...args) => {
    const value = await rest(...args)
    if (args[0].includes('/runners/')) value.data.repairs = [f.repair]
    return value
  }
  await p.createTransport(f.ctx, f.ownerHost).repair(f.row)
  assert.equal(f.calls.filter(c => c.options?.method === 'POST').length, 0)
  assert.equal(f.rpc.length, 0)
  assert.deepEqual(f.opens, [['compressed_tip', { route }]])
})
test('maintenance submits reason, expiry, owner and revision without draining jobs', async () => {
  const f = setup(), transport = p.createTransport(f.ctx, f.ownerHost)
  const expires = new Date(Date.now() + 3600000).toISOString()
  await transport.maintenance(f.row, { state: 'active', reason: 'Scheduled inspection', expires_at: expires })
  const post = f.calls.at(-1)
  assert.equal(post.path, '/v1/maintenance?profile=example')
  assert.deepEqual(post.options.body, { entity_id: f.row.host.resource_id, state: 'active', reason: 'Scheduled inspection', expires_at: expires,
    expected_owner: f.s.meta.owner, expected_config_revision: f.s.meta.owner.config_revision })
  await transport.maintenance(f.row, { state: 'inactive', reason: 'Inspection finished', expires_at: null })
  assert.equal(f.calls.at(-1).options.body.expires_at, null)
  await assert.rejects(transport.maintenance(f.row, { state: 'active', reason: '', expires_at: expires }))
  assert.equal(f.rpc.length, 0)
})
test('lost reservation response retries identical POST key and ignores foreign route hints', async () => {
  const f = setup(), rest = f.ctx.rest; let lost = true
  f.response.data.owner_route = { ...route, connectionId: 'unrelated' }
  f.ctx.rest = async (...args) => {
    const result = await rest(...args)
    if (args[0].includes('/repairs') && lost) { lost = false; throw Error('response lost') }
    return result
  }
  const transport = p.createTransport(f.ctx, f.ownerHost)
  await assert.rejects(transport.repair(f.row))
  await transport.repair(f.row)
  const posts = f.calls.filter(c => c.options?.method === 'POST')
  assert.equal(posts[0].options.body.idempotency_key, posts[1].options.body.idempotency_key)
  assert.deepEqual(f.opens[0][1], { route })
})
test('host-scoped reservation can return a mapped sibling, never unrelated entity', async () => {
  const f = setup()
  const sibling = { ...f.s.data.runners[0], id: oid('sibling') }
  f.s.data.runners.push(sibling)
  f.repair.entity_id = sibling.id
  await p.createTransport(f.ctx, f.ownerHost).repair(f.row)
  assert.equal(f.opens.length, 1)
  f.repair.entity_id = oid('other')
  await assert.rejects(p.createTransport(f.ctx, f.ownerHost).repair(f.row), /entity/)
})
test('maintenance UI requires reason and expiry and can cancel without mutation', async () => {
  const f = setup(); Object.assign(host, f.ownerHost)
  const ui = await mount(React.createElement(p.Runners, { ctx: f.ctx, features: { repair_sessions: true } }))
  try {
    await ui.click(button('Show runners'))
    await ui.click(document.querySelector('[data-node-row]'))
    await ui.click(button(`Actions for ${f.row.display_name}`))
    await ui.click([...document.querySelectorAll('[role=menuitem]')].find(e => e.textContent === 'Mark maintenance…'))
    assert.equal(document.querySelector('[aria-label="Maintenance reason"]').required, true)
    assert.equal(document.querySelector('[aria-label="Maintenance expiry"]').required, true)
    assert.match(document.body.textContent, /does not drain jobs or authorize restarts/)
    await ui.click(button('Cancel'))
    assert.equal(document.querySelector('form'), null)
    assert.equal(f.calls.filter(c => c.path.includes('/maintenance')).length, 0)
  } finally { await ui.close() }
})
test('default feature flag hides repair and maintenance actions', async () => {
  const f = setup(); Object.assign(host, f.ownerHost)
  const ui = await mount(React.createElement(p.Runners, { ctx: f.ctx }))
  try {
    await ui.click(button('Show runners'))
    await ui.click(document.querySelector('[data-node-row]'))
    await ui.click(button(`Actions for ${f.row.display_name}`))
    assert.ok(![...document.querySelectorAll('[role=menuitem]')].some(e => /in new session|Open repair session|Mark maintenance/.test(e.textContent)))
  } finally { await ui.close() }
})
test('owner enrollment enables actions without a global feature override', async () => {
  const f = setup(), rest = f.ctx.rest
  Object.assign(host, f.ownerHost)
  f.ctx.rest = async (...args) => args[0].includes('/enrollment')
    ? { ...enrollment(), features: { repair_sessions: true } } : rest(...args)
  const ui = await mount(React.createElement(p.Runners, { ctx: f.ctx }))
  try {
    await ui.click(button('Show runners'))
    await ui.click(document.querySelector('[data-node-row]'))
    await ui.click(button(`Actions for ${f.row.display_name}`))
    assert.ok([...document.querySelectorAll('[role=menuitem]')].some(e => /in new session/.test(e.textContent)))
  } finally { await ui.close() }
})
test('authoritative reserved status recovers a pre-create crash using the same core ID', async () => {
  const f = setup(), rest = f.ctx.rest
  f.ctx.rest = async (...args) => {
    if (args[0].includes('/runners/')) return { meta: f.s.meta, data: { ...fixtures.RunnerDetail, repairs: [f.repair] } }
    if (args[0].includes('/repairs/') && !f.rpc.length) return { ...f.response, data: { repair: { ...f.repair,
      session: { ...f.repair.session, stored_session_id: null, lineage_tip_id: null, state: 'reserved' } } } }
    return rest(...args)
  }
  await p.createTransport(f.ctx, f.ownerHost).repair(f.row)
  assert.equal(f.rpc.length, 1)
  assert.equal(f.rpc[0][2].start_request_id, f.repair.session.start_request_id)
  assert.equal(f.opens.length, 1)
})
test('feature gated repair action preserves node-first expansion and shows active badge after opening', async () => {
  const f = setup()
  Object.assign(host, f.ownerHost)
  const ui = await mount(React.createElement(p.Runners, { ctx: f.ctx, features: { repair_sessions: true } }))
  try {
    await ui.click(button('Show runners'))
    assert.equal(document.querySelector('[data-runner-row]'), null)
    await ui.click(document.querySelector('[data-node-row]'))
    await ui.click(button(`Actions for ${f.row.display_name}`))
    const item = [...document.querySelectorAll('[role=menuitem]')].find(e => /in new session/.test(e.textContent))
    assert.ok(item)
    assert.notEqual(item.getAttribute('data-disabled'), '')
    await ui.click(item)
    assert.equal(f.opens.length, 1)
    assert.match(document.body.textContent, /Repair active/)
    await ui.click(button(`Actions for ${f.row.display_name}`))
    assert.ok([...document.querySelectorAll('[role=menuitem]')].some(e => e.textContent === 'Open repair session'))
  } finally { await ui.close() }
})

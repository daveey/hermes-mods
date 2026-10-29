// CI slot popover: create a repair, reopen the same session, and stay disabled without an incident.
import test from 'node:test'
import assert from 'node:assert/strict'
import { React, loadPlugin, mount, route } from './sdk-mock.mjs'

const P = await loadPlugin()
const NOW = Date.parse('2026-10-28T23:00:00Z')
const ENTITY = 'runner_' + 'ab'.repeat(16)
const INCIDENT = 'incident_' + 'cd'.repeat(8)
const REPAIR = 'repair_' + 'ef'.repeat(8)
const START = 'start_' + '12'.repeat(8)
const OWNER = {
  gateway_id: '00000000-0000-4000-8000-000000000001',
  profile: 'example',
  owner_epoch: 1,
  config_revision: 1
}

function enrollment(repairSessions = true) {
  return {
    contract_version: 1, profile: 'example', enrolled: true,
    capabilities: ['inventory', 'inspect', 'refresh', 'repair'],
    features: { repair_sessions: repairSessions }
  }
}

function detail({ incident = true, repair = null } = {}) {
  return {
    meta: { contract_version: 1, owner: { ...OWNER } },
    data: {
      runner: { id: ENTITY, host_id: null, display_name: 'example-ci-1' },
      incidents: incident ? [{ id: INCIDENT, entity_id: ENTITY, resolved_at: null }] : [],
      repairs: repair ? [repair] : [],
      hosts: []
    }
  }
}

function repairRecord(session = {}) {
  return {
    id: REPAIR, entity_id: ENTITY, owner: { ...OWNER }, incident_id: INCIDENT,
    session: { start_request_id: START, stored_session_id: 'same_session', lineage_tip_id: 'same_session', state: 'running', ...session }
  }
}

function fleetBody() {
  return {
    contract_version: 1, profile: 'example',
    gateways: { state: 'ready', machines: [] },
    ci: {
      state: 'ready', hidden_deregistered: 0, machines: [{
        name: 'example-ci', ephemeral: false, labels: [], warnings: [],
        slots: [{ id: ENTITY, runner: 'example-ci-1', state: 'offline', labels: [] }]
      }]
    }
  }
}

function harness({ incident = true, repair = null, repairSessions = true } = {}) {
  const calls = [], opens = [], rpc = []
  const ctx = {
    rest: async (path, options) => {
      calls.push({ path, options })
      if (path.includes('/enrollment')) return enrollment(repairSessions)
      if (path.includes('/runners/')) return detail({ incident, repair })
      if (path.includes('/repairs/') && options?.method !== 'POST')
        return { meta: { contract_version: 1, owner: { ...OWNER } }, data: { repair: repair || repairRecord() } }
      if (path.includes('/repairs'))
        return { meta: { contract_version: 1, owner: { ...OWNER } }, data: { repair: repairRecord(), owner_route: null, route_status: 'unresolved' } }
      throw Error(`unexpected ${path}`)
    }
  }
  const ownerHost = {
    profileRoutes: async () => [{ ...route }],
    requestProfile: async (...args) => {
      rpc.push(args)
      return {
        gateway_id: 'install:core-authority', profile: route.targetProfile,
        stored_session_id: 'same_session', resolved_session_id: 'same_session',
        exists: true, reconciliation: 'running'
      }
    },
    openSession: async (...args) => { opens.push(args) }
  }
  const view = P.merge([{ route, body: fleetBody(), at: NOW }], NOW)
  const repairClient = P.createRepairClient(ctx, ownerHost)
  return { calls, opens, rpc, view, repairClient }
}

async function openAction(ui, id) {
  await ui.click(ui.container.querySelector(`[data-runner-slot="${id}"]`))
  for (let i = 0; i < 6; i++) {
    const button = ui.container.querySelector('[data-repair-action]')
    if (button?.getAttribute('data-repair-ready') === '1') return button
    await ui.flush()
  }
  throw Error('repair action did not resolve')
}

test('create-new: slot popover reserves a repair and opens the owner session', async () => {
  const f = harness()
  const ui = await mount(React.createElement(P.Panel, { view: f.view, now: NOW, repairClient: f.repairClient }))
  try {
    const action = await openAction(ui, ENTITY)
    assert.equal(action.textContent, 'Repair in new session')
    assert.equal(action.disabled, false)
    await ui.click(action)
    const post = f.calls.find((c) => c.options?.method === 'POST')
    assert.equal(post.path, '/v1/repairs?profile=example')
    assert.deepEqual(Object.keys(post.options.body).sort(), ['entity_id', 'expected_owner', 'idempotency_key', 'incident_id', 'mode'])
    assert.equal(post.options.body.entity_id, ENTITY)
    assert.equal(post.options.body.incident_id, INCIDENT)
    assert.equal(post.options.body.mode, 'repair')
    assert.match(post.options.body.idempotency_key, /^repair_[0-9a-f]{32}$/)
    assert.equal(post.options.connectionId, route.connectionId)
    assert.deepEqual(f.rpc[0].slice(0, 3), [route, 'session.start_request', {
      namespace: 'runners', profile: 'example', start_request_id: START
    }])
    assert.deepEqual(f.opens, [['same_session', { route }]])
  } finally {
    await ui.close()
  }
})

test('reopen-existing: the same session id opens twice and no second repair is reserved', async () => {
  const f = harness({ repair: repairRecord() })
  const ui = await mount(React.createElement(P.Panel, { view: f.view, now: NOW, repairClient: f.repairClient }))
  try {
    const action = await openAction(ui, ENTITY)
    assert.equal(action.textContent, 'Open repair session')
    await ui.click(action)
    await ui.click(action)
    assert.equal(f.calls.filter((c) => c.options?.method === 'POST').length, 0)
    assert.equal(f.rpc.length, 0)
    assert.deepEqual(f.opens, [['same_session', { route }], ['same_session', { route }]])
    assert.equal(f.opens[0][0], f.opens[1][0])
  } finally {
    await ui.close()
  }
})

test('disabled-without-incident: the action stays disabled and opens nothing', async () => {
  const f = harness({ incident: false })
  const ui = await mount(React.createElement(P.Panel, { view: f.view, now: NOW, repairClient: f.repairClient }))
  try {
    const action = await openAction(ui, ENTITY)
    assert.equal(action.disabled, true)
    assert.equal(action.title, 'No open incident')
    await ui.click(action)
    assert.equal(f.opens.length, 0)
    assert.equal(f.rpc.length, 0)
    assert.equal(f.calls.filter((c) => c.options?.method === 'POST').length, 0)
  } finally {
    await ui.close()
  }
})
